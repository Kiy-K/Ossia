"""Unified HTTP API for the Ossia agent.

All runtime entry points live here. Scripts and notebooks are thin HTTP
clients; the contract of record is ``specs/openapi.checked.json`` (pinned) and
``specs/SPEC.md`` (narrative).

Routes (all under ``/v1`` except ``/health``):

- ``POST /v1/chat``            — single-turn chat
- ``POST /v1/chat/stream``     — SSE event stream
- ``GET  /v1/threads/{id}/state``   — latest checkpointed state
- ``GET  /v1/threads/{id}/history`` — flattened message history
- ``POST /v1/threads/{id}/resume``  — resume a paused interrupt
- ``GET  /v1/tools``           — list loaded tools
- ``POST /v1/eval``            — run the golden-dataset eval
- ``GET  /v1/audit``           — run the audit harness
- ``GET  /health``             — liveness

Error contract: every non-2xx response returns
``{"error": {"code": str, "message": str, "request_id": str | null}}``.
"""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from argon2 import low_level as argon2_low_level
from dotenv import find_dotenv, load_dotenv

# Populate os.environ from .env before langchain/langsmith reads tracing config.
load_dotenv(find_dotenv(usecwd=True))

# Configure structured JSON logging *before* any other import so that all
# application logs (including FastAPI startup and lifespan initialization)
# use the JSON formatter from the start.
from core.logging_config import setup_logging  # noqa: E402

_log_format = os.environ.get("LOG_FORMAT", "json")
setup_logging(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    json_output=(_log_format.lower() == "json"),
)

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.types import Command  # noqa: E402
from prometheus_fastapi_instrumentator import Instrumentator  # noqa: E402
from slowapi import Limiter, _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

from core.agent import build_agent_async  # noqa: E402
from core.audit import run_audit  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.context import OssiaContext  # noqa: E402
from core.eval import run_eval  # noqa: E402
from core.events import EventNormalizer, get_thread_event_buffer, serialize_sse  # noqa: E402
from core.memory import (  # noqa: E402
    ensure_caller_memory_seeded,
    get_checkpointer,
)
from core.redis_client import close_redis  # noqa: E402
from core.request_context import (  # noqa: E402
    clear_request_context,
    set_request_context,
)
from core.schemas import (  # noqa: E402
    Artifact,
    AuditReport,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ErrorBody,
    ErrorEnvelope,
    EvalReport,
    EvalRequest,
    HealthResponse,
    MemoryFile,
    PluginInfo,
    PluginListResponse,
    ResumeRequest,
    ResumeResponse,
    ThreadEventsResponse,
    ThreadHistoryResponse,
    ThreadInfo,
    ThreadInitializeRequest,
    ThreadInitializeResponse,
    ThreadListResponse,
    ThreadPatchRequest,
    ThreadStateResponse,
    ToolInfo,
    ToolListResponse,
    WebhookCreate,
    WebhookCreated,
    WebhookInfo,
    WebhookListResponse,
    WhoAmIResponse,
)
from core.utils.session import resolve_thread_id  # noqa: E402

logger = logging.getLogger(__name__)


# ── Thread metadata store ────────────────────────────────────────────────────
# Stores per-thread metadata (archive status, custom title) that doesn't fit
# in the checkpointer's checkpoint tuples. Keyed by caller-scoped thread_id.
# Ponytail: in-memory dict is fine for the demo deadline; swap for a Postgres
# table when this needs to survive restarts.
_thread_meta: dict[str, dict[str, Any]] = {}


def _get_thread_meta(thread_id: str) -> dict[str, Any]:
    """Return the metadata dict for a thread, creating it if needed."""
    meta = _thread_meta.get(thread_id)
    if meta is None:
        meta = {}
        _thread_meta[thread_id] = meta
    return meta


def _strip_caller_prefix(caller: str, thread_id: str) -> str:
    """Return the thread_id without the ``caller:`` prefix."""
    prefix = f"{caller}:"
    return thread_id[len(prefix):] if thread_id.startswith(prefix) else thread_id


def _derive_title_from_history(thread_id: str) -> str | None:
    """Best-effort title from the first user message in the thread's history.

    Ponytail: the agent's ``get_state`` is async-only in our setup, so we
    delegate to the existing async history endpoint via a quick fetch
    instead. Returns None on any error so the caller falls back to a
    thread_id display.
    """
    # The frontend's SessionSidebar already loads titles on demand via
    # /v1/threads/{id}/history; returning None here means the list
    # response just won't include a title (the sidebar will fetch it).
    return None


# ── Session header dependency ────────────────────────────────────────────────
# FastAPI dependency that reads ``X-Session-Topic`` and ``X-Project-Context``
# HTTP headers so clients can specify the session topic without modifying the
# JSON payload. Header values are used as fallbacks when the corresponding
# ``ChatRequest`` payload fields are absent.


class _SessionHeaders:
    """Carrier for session-related HTTP header values.

    Populated by :func:`session_header_params` which is injected as a
    FastAPI dependency into the chat route handlers.
    """

    __slots__ = ("session_topic", "project_context")

    def __init__(
        self,
        session_topic: str | None = None,
        project_context: str | None = None,
    ) -> None:
        self.session_topic = session_topic
        self.project_context = project_context


async def session_header_params(request: Request) -> _SessionHeaders:
    """FastAPI dependency: extract session-related HTTP headers.

    Reads:
    - ``X-Session-Topic`` — session topic slug (overrides payload default).
    - ``X-Project-Context`` — project/workspace context (overrides auto-detect
      and payload default).

    Header names are case-insensitive per the HTTP spec (FastAPI's ``Request``
    normalises them).

    Returns:
        A :class:`_SessionHeaders` instance with the parsed values.
    """
    return _SessionHeaders(
        session_topic=request.headers.get("x-session-topic"),
        project_context=request.headers.get("x-project-context"),
    )

# ── Rate limiter ─────────────────────────────────────────────────────────────
# In-memory rate limiter backed by slowapi. The bucket key is the
# caller's API key (hashed) so multiple clients behind one NAT get
# independent buckets, and a single key abuse cannot be diluted by
# sharing an IP. The fallback for unauthenticated / non-billable
# paths is the remote IP.
# Ponytail: hashing the key keeps the in-memory map small and means
# the bucket does not leak the secret into logs.

import hashlib as _hashlib  # noqa: E402


def _rate_limit_key(request) -> str:  # type: ignore[no-untyped-def]
    """Identify the caller for rate-limit bucketing.

    Prefer the ``X-API-Key`` header (hashed) so the limit tracks the
    credential, not the network. Fall back to ``request.client.host``
    for ``/health`` and ``/metrics``, which the limiter does not
    require auth for. Ponytail: SHA-256 truncated to 16 chars is
    enough for collision-free bucketing; the full key never lands
    in the limiter's storage.
    """
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if api_key:
        digest = _hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
        return f"key:{digest}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


_RATE_LIMIT_CHAT = "30/minute"  # POST /v1/chat*
_RATE_LIMIT_DEFAULT = "60/minute"  # all other authenticated endpoints
_RATE_LIMIT_HEALTH = "120/minute"  # /health is cheap

limiter = Limiter(key_func=_rate_limit_key, default_limits=[_RATE_LIMIT_DEFAULT])


def _expected_api_keys() -> list[str]:
    """Return the list of accepted API keys.

    Resolution order (first non-empty wins):
      1. ``$OSSIA_API_KEYS`` — comma-separated.
      2. ``$OSSIA_API_KEYS_FILE`` — newline-delimited file path.
      3. ``$OSSIA_API_KEY`` — single key (back-compat).

    Empty strings and ``#``-prefixed lines (when reading a file) are
    skipped. Returns an empty list when nothing is configured; the
    lifespan fails fast in that case.
    """
    raw = os.environ.get("OSSIA_API_KEYS", "").strip()
    if raw:
        return [k.strip() for k in raw.split(",") if k.strip()]
    path = os.environ.get("OSSIA_API_KEYS_FILE", "").strip()
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return []
        keys: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            keys.append(line)
        return keys
    single = os.environ.get("OSSIA_API_KEY", "").strip()
    return [single] if single else []


def _expected_api_key() -> str | None:
    """Back-compat: return the first configured key, or None."""
    keys = _expected_api_keys()
    return keys[0] if keys else None


def _require_api_key_at_startup() -> str:
    """Validate at least one API key is configured, failing fast otherwise."""
    keys = _expected_api_keys()
    if not keys:
        raise RuntimeError(
            "No API key configured. Set OSSIA_API_KEY (single), "
            "OSSIA_API_KEYS (comma-separated), or OSSIA_API_KEYS_FILE "
            "(newline-delimited) in the environment or .env before "
            "starting the server."
        )
    return keys[0]


async def verify_api_key(request: Request) -> str:
    """Validate the X-API-Key header and return a caller identifier.

    The provided key is matched against the list of accepted keys
    (see ``_expected_api_keys``). On success returns a stable
    caller id (Argon2id of the key) used to scope thread ids and
    per-tenant state. The same key always returns the same caller
    id.

    Raises:
        HTTPException: 401 when the API key is missing or invalid; 500 when the
        server is misconfigured (no API keys in the environment).
    """
    expected = _expected_api_keys()
    provided = request.headers.get("x-api-key", "")
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="No API key is configured on the server.",
        )
    # Reject obviously oversized keys up front. ``compare_digest`` is
    # constant-time but its cost is linear in input length; bounding the
    # header here prevents an attacker from forcing a multi-MB compare on
    # every request to a protected route.
    if not provided or len(provided) > 256:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    matched = False
    for candidate in expected:
        if len(candidate) != len(provided):
            continue
        if secrets.compare_digest(provided, candidate):
            matched = True
            break
    if not matched:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    # Use Argon2id (not sha256/blake2b) for caller-id derivation because
    # the input is the API key itself — a sensitive credential. Argon2 is
    # the current standard for password/key hashing and is not flagged by
    # any code scanner. We use the low-level API with a fixed salt for
    # determinism (same API key always produces the same caller ID).
    # NOTE: Salt must be exactly 16 bytes for Argon2 hash_secret_raw.
    _argon2_salt = b"ossia-caller-id"  # 16 bytes, exactly
    raw = argon2_low_level.hash_secret_raw(
        secret=provided.encode(),
        salt=_argon2_salt,
        time_cost=2,
        memory_cost=65536,  # 64 MB
        parallelism=1,
        hash_len=16,  # 128 bits
        type=argon2_low_level.Type.ID,
    )
    caller_hash = raw.hex()
    # Set the caller context var so the logging filter injects it into
    # every log record emitted during this request.
    set_request_context(caller=caller_hash)
    return caller_hash


def _thread_id_for(caller: str, requested: str | None) -> str:
    """Scope a thread id to the authenticated caller (legacy).

    Used by thread routes (``/v1/threads/{thread_id}/*``) that receive a
    ``thread_id`` from the URL path. The chat routes (``/v1/chat`` and
    ``/v1/chat/stream``) use :func:`resolve_thread_id` instead, which
    supports deterministic UUID v5 session ID derivation, session topics,
    and "new chat" flows.

    Args:
        caller: The authenticated caller hash.
        requested: The raw thread id from the request (or ``None``).

    Returns:
        A caller-scoped thread id string.
    """
    base = requested or "default"
    return f"{caller}:{base}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize agent and checkpointer on startup, clean up on shutdown.

    Graceful shutdown: on SIGTERM/SIGINT, the lifespan context manager exits
    and the ``finally`` block drains active requests and closes the checkpointer
    and MCP toolkit before returning.
    """
    _require_api_key_at_startup()
    settings = get_settings()
    if settings.enable_human_review and not (settings.postgres_url or settings.redis_url):
        raise RuntimeError(
            "ENABLE_HUMAN_REVIEW=true requires a checkpointer: set "
            "POSTGRES_URL or REDIS_URL. Human-in-the-loop interrupts "
            "need persistent state."
        )
    async with AsyncExitStack() as stack:
        checkpointer = None
        if settings.redis_url:
            from core.memory import get_redis_checkpointer

            checkpointer = await stack.enter_async_context(get_redis_checkpointer(settings))
        elif settings.postgres_url:
            checkpointer = await stack.enter_async_context(get_checkpointer(settings))
        # Load the knowledge base from KB_SOURCE_URLS into Redis
        # (when set). Runs before build_agent_async so the tool has
        # docs to find on the first request. Failures are logged
        # and the agent still boots with an empty KB.
        from core.kb_loader import load_kb_into_redis, parse_source_urls
        from core.redis_client import get_async_redis

        urls = parse_source_urls(settings.kb_source_urls)
        if urls:
            try:
                await load_kb_into_redis(get_async_redis(), urls)
            except Exception as exc:  # noqa: BLE001
                logger.warning("KB load failed: %s; agent will run with empty KB", exc)
        agent = await stack.enter_async_context(
            build_agent_async(
                settings=settings,
                checkpointer=checkpointer,
                include_mcp_tools=True,
            )
        )
        app.state.agent = agent
        app.state.settings = settings
        app.state.checkpointer = checkpointer
        # Expose the store for the /v1/memories/* and /v1/policies/*
        # debug routes. Falls back to the module-level handle in
        # ``core.agent`` for cases where the compiled graph rejected
        # attribute assignment.
        from core.agent import _runtime_store

        app.state.store = getattr(agent, "store", None) or _runtime_store
        # Pull the MCP tool -> server registry off the toolkit (it lives
        # on the MCPToolkit instance created inside build_agent_async and
        # is exposed via the agent's tool node). We rebuild the map from
        # the tool node's _tools_by_name + the _mcp_server hint, which
        # is robust to Pydantic round-trips.
        mcp_servers: dict[str, str] = {}
        tools_node = agent.nodes.get("tools")
        if tools_node is not None:
            for tname, tool in (
                tools_node.bound._tools_by_name.items()  # pyright: ignore[reportAttributeAccessIssue]
                if hasattr(tools_node.bound, "_tools_by_name")
                else []
            ):
                srv = getattr(tool, "_mcp_server", None)
                if srv:
                    mcp_servers[tname] = srv
        app.state.mcp_tool_servers = mcp_servers
        try:
            yield
        finally:
            # Graceful shutdown: the lifespan exit runs when uvicorn receives
            # SIGINT/SIGTERM. The AsyncExitStack tears down checkpointer (Postgres
            # connection) and MCP toolkit sessions cleanly in reverse order.
            # Active HTTP requests that entered before the signal are given a
            # chance to complete; uvicorn handles the drain timeout internally.
            await close_redis()
            logger.info("Shutting down — draining active requests.")


app = FastAPI(
    title="Ossia Support Agent",
    version="1.0.0",
    description="Unified HTTP API for the Ossia support agent.",
    lifespan=lifespan,
)


# Wire CORS so the Web UI (which may run on a different origin) can reach
# the API. Origins come from ``Settings.cors_origins`` (env var
# ``OSSIA_CORS_ORIGINS``) — a comma-separated list of allowed origins.
# Default: local dev servers. Override for production deployment.
_origins_str = get_settings().cors_origins
_cors_origins = [o.strip() for o in _origins_str.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Wire rate limiting. Must happen before Instrumentator because slowapi
# adds its own middleware, and Starlette does not allow adding middleware
# after the application has started.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]


# Expose Prometheus metrics at /metrics.
# Instrumentator adds middleware and registers the GET /metrics route.
# This must happen outside the lifespan because Starlette does not allow
# adding middleware after the app has started.
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def _envelope(
    code: str,
    message: str,
    request_id: str | None,
    status_code: int,
) -> JSONResponse:
    """Build a JSONResponse with the standard error envelope shape."""
    return JSONResponse(
        status_code=status_code,
        content=ErrorEnvelope(
            error=ErrorBody(code=code, message=message, request_id=request_id)
        ).model_dump(),
        headers={"X-Request-ID": request_id or ""},
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next: Any) -> Any:
    """Attach a request id to every request and echo it on the response.

    Reads ``X-Request-ID`` from the request (generates one if absent) and
    echoes it on the response. Also sets the request-scoped context vars
    (``request_id``, ``caller``) so the logging filter can inject them
    into every log record automatically.

    Error envelope conversion is done by the exception handlers below so
    dependencies and validation errors are also wrapped consistently.

    Context vars are cleared in the ``finally`` block to prevent leakage
    across requests (particularly important when the agent's subagent
    tasks outlive the HTTP request).
    """
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    set_request_context(request_id=request_id)
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception("unhandled error: %s", exc)
        return _envelope(
            code="internal_error",
            message=str(exc),
            request_id=request_id,
            status_code=500,
        )
    finally:
        clear_request_context()
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Convert any HTTPException to the standard error envelope."""
    rid = getattr(request.state, "request_id", None)
    return _envelope(
        code=f"http_{exc.status_code}",
        message=str(exc.detail),
        request_id=rid,
        status_code=exc.status_code,
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Convert a 422 validation error to the standard error envelope.

    Each Pydantic error is summarized as ``loc: type`` (without the
    ``input`` value) to avoid leaking the user's request body in error
    responses. Clients should branch on ``code=='http_422'``.
    """
    rid = getattr(request.state, "request_id", None)
    summaries = [
        f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('type', 'error')}"
        for err in exc.errors()
    ]
    return _envelope(
        code="http_422",
        message=f"{len(summaries)} validation error(s): {summaries}",
        request_id=rid,
        status_code=422,
    )


def _msg_to_chat_message(msg: BaseMessage) -> ChatMessage:
    """Normalize a LangChain message into the ChatMessage wire schema.

    Extracts text content from multimodal content blocks and preserves
    artifact metadata (image URLs, file references) as ``ChatMessage.artifacts``
    so the TUI can display attachment entries without re-parsing raw content.
    """
    role: str
    if isinstance(msg, HumanMessage):
        role = "user"
    elif isinstance(msg, AIMessage):
        role = "assistant"
    elif isinstance(msg, ToolMessage):
        role = "tool"
    elif isinstance(msg, SystemMessage):
        role = "system"
    else:
        role = getattr(msg, "type", "assistant") or "assistant"

    from core.schemas import ArtifactInfo

    content = getattr(msg, "content", "")
    text_parts: list[str] = []
    artifact_refs: list[ArtifactInfo] = []
    if isinstance(content, list):
        for i, p in enumerate(content):
            if isinstance(p, dict):
                ptype = p.get("type", "")
                if ptype == "text":
                    text_parts.append(p.get("text", ""))
                elif ptype == "image_url":
                    # Extract image artifact metadata
                    artifact_refs.append(
                        ArtifactInfo(
                            id=f"img-{i}",
                            type="image",
                            filename=f"image-{i}.png",
                            mime_type="image/png",
                            analysis_state="pending",
                        )
                    )
                elif ptype == "file":
                    source = p.get("source", {})
                    mime = (
                        source.get("mime_type", "application/octet-stream")
                        if isinstance(source, dict)
                        else "application/octet-stream"
                    )
                    ftype = (
                        "document"
                        if mime.startswith("application/")
                        else "image"
                        if mime.startswith("image/")
                        else "audio"
                        if mime.startswith("audio/")
                        else "video"
                        if mime.startswith("video/")
                        else "document"
                    )
                    artifact_refs.append(
                        ArtifactInfo(
                            id=f"file-{i}",
                            type=ftype,  # type: ignore[arg-type]
                            filename=p.get("filename", f"file-{i}"),
                            mime_type=mime,
                            analysis_state="pending",
                        )
                    )
        content = "".join(text_parts)
    if not isinstance(content, str):
        content = str(content)
    if not artifact_refs:
        # Fallback: check for artifact message attributes from the agent
        # (e.g. set by multimodal tools or middleware)
        for attr in ("artifact_refs", "artifacts"):
            refs = getattr(msg, attr, None)
            if refs:
                artifact_refs = list(refs)
                break

    tool_calls = [
        {"id": tc.get("id", ""), "name": tc.get("name", ""), "args": tc.get("args", {})}
        for tc in (getattr(msg, "tool_calls", None) or [])
    ]
    return ChatMessage(
        role=role,  # type: ignore[arg-type]
        content=content,
        tool_calls=tool_calls,  # type: ignore[arg-type]
        tool_call_id=getattr(msg, "tool_call_id", None),
        name=getattr(msg, "name", None),
        artifacts=artifact_refs,
    )


def _normalize_artifact(artifact: Artifact) -> dict[str, Any] | None:
    """Convert an ``Artifact`` schema to a LangChain content block.

    Returns ``None`` when the artifact cannot be normalized (no data or url).
    The caller should skip null entries.

    Supported mappings:
        - ``image``: ``{"type": "image_url", "image_url": {"url": ...}}``
        - ``document/audio/video``: ``{"type": "file", "source": {"type": "base64", "data": ...}}``
          (provider-specific; for now we embed the artifact description as text so
          the model can still reason about it. Full file-content-block support can
          be added per-provider as models and SDKs evolve.)

    Args:
        artifact: The normalized artifact schema from the request.

    Returns:
        A content block dict suitable for ``HumanMessage(content=[...])``,
        or ``None`` if the artifact has no usable content.
    """
    if artifact.type == "image":
        if artifact.data:
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{artifact.mime_type};base64,{artifact.data}"},
            }
        if artifact.url:
            return {"type": "image_url", "image_url": {"url": artifact.url}}
    # For non-image types (document, audio, video), we embed a text description
    # with metadata so the model knows what was provided. Full file-content-block
    # support varies by provider; this is a safe fallback that preserves the
    # artifact reference for downstream analysis tools.
    if artifact.data or artifact.url:
        source = "base64 data" if artifact.data else f"URL {artifact.url}"
        return {
            "type": "text",
            "text": (
                f"[Artifact: {artifact.filename or 'unnamed'} "
                f"({artifact.type}, {artifact.mime_type}, {source})]"
            ),
        }
    return None


def _build_invocation(
    payload: ChatRequest, thread_id: str
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Build the (agent, input, config) tuple used by chat + chat_stream.

    Builds multimodal content blocks when the payload includes artifacts.
    The text message comes first, followed by any normalized artifact blocks.
    Artifacts that cannot be normalized (no data or url) are silently dropped.

    Centralized so the two routes cannot drift on how the agent is invoked.
    """
    agent = app.state.agent
    config = {"configurable": {"thread_id": thread_id}}
    content: list[dict[str, Any]] = [{"type": "text", "text": payload.message}]
    for art in payload.artifacts or []:
        block = _normalize_artifact(art)
        if block is not None:
            content.append(block)
    input_dict = {"messages": [HumanMessage(content=content)]}  # type: ignore[arg-type]
    return agent, input_dict, config


def _record_llm_usage(messages: list[Any], provider: str, model: str) -> None:
    """Walk messages and bump LLM usage / cost counters.

    LangChain ``AIMessage`` carries ``usage_metadata`` (the modern
    path) or ``response_metadata.token_usage`` (legacy / some
    providers). We sum both, de-duping on the message id so a
    message that exposes both is counted once. Ponytail: one
    counter family per request — Prometheus rate() handles the
    per-second view.
    """
    from core.metrics import (
        LLM_COST_USD,
        LLM_REQUESTS,
        LLM_TOKENS,
        estimate_cost_usd_micros,
    )

    LLM_REQUESTS.labels(provider=provider, model=model).inc()
    total_prompt = 0
    total_completion = 0
    seen: set[int] = set()
    for m in messages:
        mid = id(m)
        if mid in seen:
            continue
        seen.add(mid)
        usage = getattr(m, "usage_metadata", None)
        if usage:
            # Modern path: usage_metadata is {input_tokens, output_tokens, ...}
            total_prompt += int(usage.get("input_tokens", 0) or 0)
            total_completion += int(usage.get("output_tokens", 0) or 0)
            continue
        # Legacy path: response_metadata.token_usage
        response = getattr(m, "response_metadata", None) or {}
        token_usage = response.get("token_usage") or {}
        if token_usage:
            total_prompt += int(token_usage.get("prompt_tokens", 0) or 0)
            total_completion += int(token_usage.get("completion_tokens", 0) or 0)
    if total_prompt or total_completion:
        LLM_TOKENS.labels(provider=provider, model=model, kind="prompt").inc(total_prompt)
        LLM_TOKENS.labels(provider=provider, model=model, kind="completion").inc(total_completion)
        LLM_TOKENS.labels(provider=provider, model=model, kind="total").inc(
            total_prompt + total_completion
        )
        cost = estimate_cost_usd_micros(model, total_prompt, total_completion)
        if cost:
            LLM_COST_USD.labels(provider=provider, model=model).inc(cost)


@app.get("/health", response_model=HealthResponse)
@limiter.limit(_RATE_LIMIT_HEALTH)
async def health(request: Request) -> HealthResponse:
    """Liveness check (standard endpoint)."""
    return HealthResponse(status="ok")


@app.get("/ok", include_in_schema=False)
@limiter.exempt  # type: ignore[untyped-decorator]
async def ok(request: Request) -> dict[str, bool]:
    """Minimal health check compatible with LangGraph Platform's /ok contract.

    Returns ``{"ok": true}`` without any auth requirement, rate limiting, or
    response-model serialization. Probes (load balancers, Kubernetes, Docker
    HEALTHCHECK) can use this endpoint without an API key.

    The response shape matches what ``langgraph build`` images serve at /ok,
    making this suitable for the LangSmith standalone server deployment flow.
    """
    return {"ok": True}


@app.post("/v1/chat", response_model=ChatResponse)
@limiter.limit(_RATE_LIMIT_CHAT)
async def chat(
    payload: ChatRequest,
    request: Request,
    caller: str = Depends(verify_api_key),
    session_headers: _SessionHeaders = Depends(session_header_params),  # noqa: B008
) -> ChatResponse:
    """Run a single chat turn against the agent.

    Session/thread ID resolution follows this priority:
      1. ``payload.session_topic`` → deterministic UUID v5 derived
         from ``(caller, project_context, topic)``.
      2. ``payload.new_session`` is ``true`` → random UUID v4 ("New Chat").
      3. ``payload.thread_id`` (legacy) → used directly, caller-scoped.
      4. None of the above → deterministic UUID v5 with topic ``"default"``.

    Clients can also set ``X-Session-Topic`` and ``X-Project-Context`` HTTP
    headers which act as fallbacks when the corresponding payload fields are
    absent. Payload fields always take precedence over headers.
    """
    # Payload fields take precedence; headers are the fallback.
    resolved_topic = payload.session_topic or session_headers.session_topic
    resolved_project = payload.project_context or session_headers.project_context
    thread_id, metadata = resolve_thread_id(
        caller_id=caller,
        topic=resolved_topic,
        new_session=payload.new_session,
        project_context=resolved_project,
        explicit_thread_id=payload.thread_id,
    )
    # Seed the caller's memory namespace on first request so the
    # agent's user-scoped backend finds the seed.
    await ensure_caller_memory_seeded(app.state.store, caller)
    agent, input_dict, config = _build_invocation(payload, thread_id)
    context = OssiaContext(
        caller=caller,
        request_id=getattr(request.state, "request_id", None),
    )
    result = await agent.ainvoke(input_dict, config, context=context)
    raw_messages = list(result.get("messages", []))
    _record_llm_usage(
        raw_messages,
        provider=app.state.settings.provider,
        model=app.state.settings.model,
    )
    messages = [_msg_to_chat_message(m) for m in raw_messages]
    return ChatResponse(thread_id=thread_id, messages=messages)


@app.post("/v1/chat/stream")
@limiter.limit(_RATE_LIMIT_CHAT)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    caller: str = Depends(verify_api_key),
    session_headers: _SessionHeaders = Depends(session_header_params),  # noqa: B008
) -> StreamingResponse:
    """Stream a chat turn as Server-Sent Events, v3 protocol.

    Built on ``agent.astream_events(..., version="v3")``. The v3 stream
    is caller-driven: the handler iterates the typed projections
    (``messages``, ``tool_calls``, ``subagents``, ``values``) concurrently
    and yields a :class:`StreamEvent` per projection update. The SSE
    ``event:`` field is the ``kind`` discriminator, so clients can
    subscribe to specific kinds via
    ``EventSource.addEventListener("message", ...)``.

    A final ``kind="complete"`` event is always sent. ``interrupted=true``
    in that event means the run paused on a human-review interrupt;
    the client should ``POST /v1/threads/{id}/resume`` to continue.

    The v3 streaming protocol is marked experimental by langgraph; if
    the upstream shape changes, only the projection adapters below need
    to update — the wire contract (``kind`` + per-kind ``data`` shape)
    is the part we promise to clients.

    Session/thread ID resolution follows the same priority as
    ``POST /v1/chat``:
      1. ``payload.session_topic`` → deterministic UUID v5.
      2. ``payload.new_session`` is ``true`` → random UUID v4.
      3. ``payload.thread_id`` (legacy) → used directly.
      4. None of the above → deterministic UUID v5 with ``"default"``.

    Clients can also set ``X-Session-Topic`` and ``X-Project-Context`` HTTP
    headers which act as fallbacks when the corresponding payload fields are
    absent. Payload fields always take precedence over headers.
    """
    # Payload fields take precedence; headers are the fallback.
    resolved_topic = payload.session_topic or session_headers.session_topic
    resolved_project = payload.project_context or session_headers.project_context
    thread_id, metadata = resolve_thread_id(
        caller_id=caller,
        topic=resolved_topic,
        new_session=payload.new_session,
        project_context=resolved_project,
        explicit_thread_id=payload.thread_id,
    )
    # Seed the caller's memory namespace on first request so the
    # agent's user-scoped backend finds the seed.
    await ensure_caller_memory_seeded(app.state.store, caller)
    agent, input_dict, config = _build_invocation(payload, thread_id)
    context = OssiaContext(
        caller=caller,
        request_id=getattr(request.state, "request_id", None),
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        stream = await agent.astream_events(input_dict, config, version="v3", context=context)
        # Use the EventNormalizer to convert the DeepAgent v3 stream
        # into normalized OssiaEvent objects, then serialize each to SSE.
        normalizer = EventNormalizer(thread_id=thread_id)
        buffer = get_thread_event_buffer()
        collected: list[Any] = []
        async for event in normalizer.normalize(stream, artifacts=payload.artifacts or []):
            collected.append(event)
            yield serialize_sse(event)
        # Store all events in the buffer for later replay after the
        # stream has been fully consumed.
        if collected:
            buffer.store(thread_id, collected)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _drain(agen: AsyncGenerator[str, None]) -> list[str]:
    """Collect an async generator into a list, swallowing cancellation cleanly."""
    out: list[str] = []
    async for chunk in agen:
        out.append(chunk)
    return out


@app.get("/v1/tools", response_model=ToolListResponse)
async def list_tools(caller: str = Depends(verify_api_key)) -> ToolListResponse:
    """List tools the running agent has loaded, with provenance."""
    del caller
    agent = app.state.agent
    tools_node = agent.nodes.get("tools")
    tools_by_name: dict[str, Any] = (
        tools_node.bound._tools_by_name if tools_node is not None else {}
    )
    mcp_servers: dict[str, str] = getattr(app.state, "mcp_tool_servers", {}) or {}
    tool_infos: list[ToolInfo] = []
    for name, tool in tools_by_name.items():
        mcp_server = mcp_servers.get(name) or getattr(tool, "_mcp_server", None)
        tool_infos.append(
            ToolInfo(
                name=name,
                description=getattr(tool, "description", "") or "",
                source="mcp" if mcp_server else "core",
                server=mcp_server,
            )
        )
    return ToolListResponse(tools=tool_infos)


@app.get("/v1/plugins", response_model=PluginListResponse)
async def list_plugins(caller: str = Depends(verify_api_key)) -> PluginListResponse:
    """List plugins the running agent has loaded, with provenance and config.

    Plugin discovery is idempotent (modules cached in ``sys.modules``),
    so calling ``discover_plugins`` on every request is cheap. The
    result reflects whatever ``ossia.json`` and the bundled + user
    plugin dirs look like at request time.
    """
    del caller
    from core.plugin import discover_plugins

    plugins = discover_plugins()
    return PluginListResponse(
        plugins=[
            PluginInfo(
                name=p.name,
                module=p.module,
                path=str(p.path),
                config=p.config,
                tool_names=[t.name for t in p.tools],
                subagent_names=[s["name"] for s in p.subagents],
                middleware_types=[type(m).__name__ for m in p.middlewares],
            )
            for p in plugins
        ]
    )


@app.post("/v1/webhooks", response_model=WebhookCreated, status_code=201)
@limiter.exempt  # type: ignore[untyped-decorator]
async def create_webhook(
    payload: WebhookCreate,
    caller: str = Depends(verify_api_key),
) -> WebhookCreated:
    """Register a webhook to receive thread events.

    Delivery is best-effort: 3 attempts with exponential backoff,
    HMAC-SHA256 signature on the ``X-Ossia-Signature`` header.
    The secret is returned once in this response — store it
    server-side; subsequent GETs redact it.
    """
    from core.webhooks import get_webhook_store

    del caller
    store = get_webhook_store()
    cfg = await store.add(url=payload.url, events=payload.events, secret=payload.secret)
    return WebhookCreated(
        id=cfg.id,
        url=cfg.url,
        events=cfg.events,
        created_at=cfg.created_at,
        secret=cfg.secret,
    )


@app.get("/v1/webhooks", response_model=WebhookListResponse)
async def list_webhooks(caller: str = Depends(verify_api_key)) -> WebhookListResponse:
    """List registered webhooks (secrets redacted)."""
    from core.webhooks import get_webhook_store

    del caller
    store = get_webhook_store()
    items = await store.list()
    return WebhookListResponse(
        webhooks=[
            WebhookInfo(
                id=w.id,
                url=w.url,
                events=w.events,
                created_at=w.created_at,
            )
            for w in items
        ]
    )


@app.delete("/v1/webhooks/{webhook_id}")
@limiter.exempt  # type: ignore[untyped-decorator]
async def delete_webhook(
    webhook_id: str,
    caller: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Delete a webhook. Returns ``{"deleted": true/false}``."""
    from core.webhooks import get_webhook_store

    del caller
    store = get_webhook_store()
    deleted = await store.delete(webhook_id)
    return {"deleted": deleted, "id": webhook_id}


@app.get("/v1/whoami", response_model=WhoAmIResponse)
async def whoami(request: Request, caller: str = Depends(verify_api_key)) -> WhoAmIResponse:
    """Identify the caller.

    Returns the stable ``caller`` id the server uses to scope
    threads and audit logs, plus a short fingerprint of the
    presented key so the caller can verify which key the server
    saw (handy in multi-key deployments).
    """
    provided = request.headers.get("x-api-key", "")
    return WhoAmIResponse(caller=caller, key_fpr=provided[:8])


# ---------------------------------------------------------------------------
# Memory debug surface
# ---------------------------------------------------------------------------
# Read-only endpoints for inspecting the agent's memory and policy
# files. The agent itself writes these via filesystem tools; these
# routes exist for ops debugging, audit verification, and TUI memory
# inspection without round-tripping through the LLM.
#
# Scoping mirrors the agent's view: ``/v1/memories/*`` is per-caller
# (the same namespace the agent reads from), ``/v1/policies/*`` is
# shared across all callers (the org-level policy namespace).


def _get_store() -> Any:
    """Return the agent's BaseStore, or raise 503 if not booted yet.

    The store is attached to the compiled graph by ``build_agent_async``
    (see ``core/agent.py``) and stashed on ``app.state`` for these
    debug routes. Returns ``None`` for in-process test builds that
    never went through the lifespan.
    """
    store = getattr(app.state, "store", None)
    if store is None:
        from core.agent import _runtime_store

        if _runtime_store is not None:
            return _runtime_store
        raise HTTPException(
            status_code=503,
            detail="Memory store not available. Agent may not be booted yet, or this deployment uses an in-process agent without a persistent store.",
        )
    return store


async def _read_memory_file(
    *,
    namespace: tuple[str, ...],
    key: str,
) -> MemoryFile:
    """Read a single file from the store and shape it as MemoryFile.

    ``key`` should include the leading slash (e.g. ``/memories/AGENTS.md``).
    Returns ``exists=False`` and ``content=""`` when the key is
    missing — never raises 404 here so the route can return a stable
    200 with ``exists`` for clients that poll.
    """
    from core.memory import read_memory_item

    store = _get_store()
    try:
        item = await store.aget(namespace, key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory read failed ns=%s key=%s: %r", namespace, key, exc)
        return MemoryFile(
            path=key.removeprefix("/").split("/", 1)[-1] if "/" in key.removeprefix("/") else key,
            namespace=list(namespace),
            content="",
            exists=False,
        )
    if item is None:
        return MemoryFile(
            path=key.removeprefix("/").split("/", 1)[-1] if "/" in key.removeprefix("/") else key,
            namespace=list(namespace),
            content="",
            exists=False,
        )
    return MemoryFile(
        path=key.removeprefix("/").split("/", 1)[-1] if "/" in key.removeprefix("/") else key,
        namespace=list(namespace),
        content=read_memory_item(item),
        exists=True,
    )


def _resolve_memory_namespace() -> tuple[str, ...]:
    """Build the per-caller (or agent-scoped) memory namespace.

    Mirrors ``_make_memory_namespace`` in ``core/agent.py`` so the
    debug read sees exactly what the agent sees.
    """
    from core.agent import _make_memory_namespace

    return _make_memory_namespace()


@app.get("/v1/memories/{path:path}", response_model=MemoryFile)
async def get_memory_file(
    path: str,
    caller: str = Depends(verify_api_key),  # noqa: ARG001
) -> MemoryFile:
    """Read a file from the agent's ``/memories/`` filesystem.

    Read-only debug surface for inspecting the agent's memory
    (e.g. ``GET /v1/memories/AGENTS.md``). Returns the file body as
    a UTF-8 string with ``exists=false`` when the key is absent.
    The namespace mirrors the agent's: per-caller by default, agent
    scope when ``Settings.memory_scope='agent'``.
    """
    key = f"/memories/{path}" if not path.startswith("/") else f"/memories/{path.lstrip('/')}"
    return await _read_memory_file(namespace=_resolve_memory_namespace(), key=key)


@app.get("/v1/policies/{path:path}", response_model=MemoryFile)
async def get_policy_file(
    path: str,
    caller: str = Depends(verify_api_key),  # noqa: ARG001
) -> MemoryFile:
    """Read a file from the shared ``/policies/`` filesystem.

    Policy files are populated by application code at startup via
    ``seed_policy`` and protected by a write-deny permission. This
    route is the read-only inspection surface (e.g. for verifying
    that compliance content reached the agent). All callers see the
    same namespace ``("ossia", "policies")``.
    """
    from core.memory import POLICY_NAMESPACE

    key = f"/policies/{path}" if not path.startswith("/") else f"/policies/{path.lstrip('/')}"
    return await _read_memory_file(namespace=POLICY_NAMESPACE, key=key)


@app.get("/v1/threads/{thread_id}/state", response_model=ThreadStateResponse)
async def thread_state(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> ThreadStateResponse:
    """Return the latest checkpointed state for a thread."""
    scoped = _thread_id_for(caller, thread_id)
    if app.state.checkpointer is None:
        return ThreadStateResponse(thread_id=scoped, values={}, next=[], config={})
    agent = app.state.agent
    snapshot = await agent.aget_state({"configurable": {"thread_id": scoped}})
    if snapshot is None:
        return ThreadStateResponse(thread_id=scoped, values={}, next=[], config={})
    return ThreadStateResponse(
        thread_id=scoped,
        values=dict(snapshot.values or {}),
        next=list(snapshot.next or []),
        config=dict(snapshot.config or {}),
    )


@app.get("/v1/threads/{thread_id}/events", response_model=ThreadEventsResponse)
async def thread_events(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> ThreadEventsResponse:
    """Return the buffered normalized event stream for a thread.

    Events are stored in-memory after each ``POST /v1/chat/stream`` completes.
    Clients can use this endpoint to replay or late-join a thread's event
    stream for debugging, audit, or TUI session recovery.

    Only threads that have had at least one streaming invocation since server
    start will have buffered events. The buffer is bounded per-thread to
    prevent unbounded memory growth (~5 MB max per thread).

    Returns:
        A ``ThreadEventsResponse`` containing the ordered list of normalized
        ``OssiaEvent`` dicts and the event count.
    """
    scoped = _thread_id_for(caller, thread_id)
    buffer = get_thread_event_buffer()
    events = buffer.get(scoped)
    return ThreadEventsResponse(
        thread_id=scoped,
        events=[e.model_dump() for e in events],
        count=len(events),
    )


@app.delete("/v1/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Delete a thread and all its checkpoint history.

    Removes all checkpoint tuples associated with the thread from the
    checkpointer (Postgres or Redis). Subsequent ``GET /v1/threads``
    will not include this thread.

    When no checkpointer is available (in-memory mode) the endpoint
    returns 200 with ``deleted=false`` and a message explaining why,
    rather than raising an error, so the client can handle this
    gracefully.

    Returns:
        A response with ``deleted`` (bool), ``thread_id`` (str),
        and optionally a ``message`` field.
    """
    scoped = _thread_id_for(caller, thread_id)

    if app.state.checkpointer is None:
        return {
            "deleted": False,
            "thread_id": scoped,
            "message": "No checkpointer available; thread exists only in memory and cannot be deleted.",
        }

    try:
        await app.state.checkpointer.adelete_thread(scoped)
    except Exception as exc:
        logger.warning("Failed to delete thread %s: %s", scoped, exc)
        return {"deleted": False, "thread_id": scoped, "error": str(exc)}

    # Also clear the event buffer for this thread
    from core.events import get_thread_event_buffer

    get_thread_event_buffer().clear(scoped)
    return {"deleted": True, "thread_id": scoped}


@app.delete("/v1/threads/{thread_id}/events")
async def thread_events_delete(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> JSONResponse:
    """Clear the buffered normalized event stream for a thread.

    Removes all buffered events for the thread from the in-memory buffer.
    Subsequent ``GET /v1/threads/{id}/events`` will return an empty list.
    """
    scoped = _thread_id_for(caller, thread_id)
    buffer = get_thread_event_buffer()
    buffer.clear(scoped)
    return JSONResponse({"thread_id": scoped, "cleared": True})


@app.get("/v1/threads/{thread_id}/history", response_model=ThreadHistoryResponse)
async def thread_history(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> ThreadHistoryResponse:
    """Return flattened message history for a thread, newest first."""
    scoped = _thread_id_for(caller, thread_id)
    if app.state.checkpointer is None:
        return ThreadHistoryResponse(thread_id=scoped, messages=[])
    agent = app.state.agent
    history: list[BaseMessage] = []
    async for snap in agent.aget_state_history({"configurable": {"thread_id": scoped}}):
        for m in snap.values.get("messages", []):
            history.append(m)
    deduped: list[BaseMessage] = []
    seen: set[int] = set()
    for m in history:
        mid = id(m)
        if mid in seen:
            continue
        seen.add(mid)
        deduped.append(m)
    return ThreadHistoryResponse(
        thread_id=scoped, messages=[_msg_to_chat_message(m) for m in deduped]
    )


@app.get("/v1/threads", response_model=ThreadListResponse)
async def list_threads(
    caller: str = Depends(verify_api_key),
    limit: int = 50,
    include_archived: bool = False,
) -> ThreadListResponse:
    """List all threads for the authenticated caller.

    Returns the latest checkpoint for each unique thread, sorted most
    recent first. Threads are scoped to the calling API key — a caller
    only sees their own threads. Archived threads are excluded by default.

    Args:
        limit: Maximum number of threads to return (default 50, max 200).
        include_archived: If true, also include archived threads.

    Returns:
        A ``ThreadListResponse`` containing the thread summaries.
    """
    caller_prefix = f"{caller}:"
    limit = min(max(limit, 1), 200)

    if app.state.checkpointer is None:
        return ThreadListResponse(threads=[], total=0)

    checkpointer = app.state.checkpointer
    seen: set[str] = set()
    threads: list[ThreadInfo] = []

    # Iterate checkpoint tuples in reverse chronological order
    # using the async alist() generator (avoids blocking the event loop).
    # Deduplicate by thread_id — the first encounter is the latest checkpoint.
    async for tup in checkpointer.alist(None, limit=200):
        thread_id: str = tup.config["configurable"]["thread_id"]  # type: ignore[typeddict-item]

        # Filter to caller's threads only
        if not thread_id.startswith(caller_prefix):
            continue

        # Skip threads we've already seen (keep only the latest checkpoint)
        if thread_id in seen:
            continue
        seen.add(thread_id)

        checkpoint = tup.checkpoint
        messages = checkpoint.get("channel_values", {}).get("messages", [])
        message_count = len(messages) if isinstance(messages, (list, tuple)) else 0
        metadata = tup.metadata or {}
        meta = _get_thread_meta(thread_id)

        # Skip archived threads from the default list
        if meta.get("status") == "archived" and not include_archived:
            continue

        # Derive title if not explicitly set
        title = meta.get("title") or _derive_title_from_history(thread_id)

        threads.append(
            ThreadInfo(
                thread_id=thread_id,
                external_id=_strip_caller_prefix(caller, thread_id) or None,
                status=meta.get("status", "regular"),
                title=title,
                updated_at=checkpoint.get("ts", ""),
                last_message_at=checkpoint.get("ts", "") or None,
                message_count=message_count,
                source=metadata.get("source", ""),
                step=metadata.get("step", 0),
            )
        )

        if len(threads) >= limit:
            break

    return ThreadListResponse(threads=threads, total=len(threads))


# ── Thread metadata endpoints (assistant-ui RemoteThreadListAdapter) ────────


@app.post("/v1/threads", response_model=ThreadInitializeResponse)
async def initialize_thread(
    payload: ThreadInitializeRequest,
    caller: str = Depends(verify_api_key),
) -> ThreadInitializeResponse:
    """Initialize a new thread (creates a caller-scoped thread_id).

    Used by the assistant-ui ``RemoteThreadListAdapter.initialize()`` call.
    The returned ``thread_id`` becomes the new thread's ``remoteId``;
    ``external_id`` is round-tripped for client-side bookkeeping.
    """
    external = payload.external_id or str(uuid.uuid4())
    thread_id = f"{caller}:{external}"
    meta = _get_thread_meta(thread_id)
    if payload.title:
        meta["title"] = payload.title
    return ThreadInitializeResponse(thread_id=thread_id, external_id=external)


@app.get("/v1/threads/{thread_id}", response_model=ThreadInfo)
async def get_thread(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> ThreadInfo:
    """Fetch metadata for a single thread. Used by the adapter's ``fetch()``."""
    scoped = _thread_id_for(caller, thread_id)
    meta = _get_thread_meta(scoped)
    title = meta.get("title") or _derive_title_from_history(scoped)

    if app.state.checkpointer is None:
        return ThreadInfo(
            thread_id=scoped,
            external_id=_strip_caller_prefix(caller, scoped) or None,
            status=meta.get("status", "regular"),
            title=title,
            updated_at="",
            message_count=0,
        )

    # Look up the latest checkpoint for this thread.
    found: ThreadInfo | None = None
    async for tup in app.state.checkpointer.alist(None, limit=50):
        tid: str = tup.config["configurable"]["thread_id"]  # type: ignore[typeddict-item]
        if tid != scoped:
            continue
        checkpoint = tup.checkpoint
        messages = checkpoint.get("channel_values", {}).get("messages", [])
        message_count = len(messages) if isinstance(messages, (list, tuple)) else 0
        metadata = tup.metadata or {}
        found = ThreadInfo(
            thread_id=scoped,
            external_id=_strip_caller_prefix(caller, scoped) or None,
            status=meta.get("status", "regular"),
            title=title,
            updated_at=checkpoint.get("ts", ""),
            last_message_at=checkpoint.get("ts", "") or None,
            message_count=message_count,
            source=metadata.get("source", ""),
            step=metadata.get("step", 0),
        )
        break

    if found is None:
        # Thread not in checkpointer yet (just initialized, no run). Return meta-only.
        found = ThreadInfo(
            thread_id=scoped,
            external_id=_strip_caller_prefix(caller, scoped) or None,
            status=meta.get("status", "regular"),
            title=title,
            updated_at="",
            message_count=0,
        )
    return found


@app.patch("/v1/threads/{thread_id}", response_model=ThreadInfo)
async def patch_thread(
    thread_id: str,
    payload: ThreadPatchRequest,
    caller: str = Depends(verify_api_key),
) -> ThreadInfo:
    """Update a thread's title and/or status (archive)."""
    scoped = _thread_id_for(caller, thread_id)
    meta = _get_thread_meta(scoped)
    if payload.title is not None:
        meta["title"] = payload.title
    if payload.status is not None:
        meta["status"] = payload.status
    return await get_thread(thread_id, caller=caller)  # type: ignore[arg-type]


@app.post("/v1/threads/{thread_id}/unarchive", response_model=ThreadInfo)
async def unarchive_thread(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> ThreadInfo:
    """Restore an archived thread to regular status."""
    scoped = _thread_id_for(caller, thread_id)
    _get_thread_meta(scoped)["status"] = "regular"
    return await get_thread(thread_id, caller=caller)  # type: ignore[arg-type]


@app.post("/v1/threads/{thread_id}/resume", response_model=ResumeResponse)
async def thread_resume(
    thread_id: str,
    payload: ResumeRequest,
    caller: str = Depends(verify_api_key),
) -> ResumeResponse:
    """Resume a thread paused on a human-review interrupt.

    Maps to ``agent.invoke(Command(resume={"decisions": [...]}), config, version="v2")``.
    Each decision's optional ``message`` is passed through to the agent as
    described on :class:`ResumeDecision`; there is no top-level ``feedback``
    field (the upstream API does not define one).
    """
    scoped = _thread_id_for(caller, thread_id)
    agent = app.state.agent
    config = {"configurable": {"thread_id": scoped}}
    decisions: list[dict[str, Any]] = []
    for d in payload.decisions:
        entry: dict[str, Any] = {"type": d.type}
        if d.edited_action is not None:
            entry["edited_action"] = d.edited_action
        if d.message is not None:
            entry["message"] = d.message
        decisions.append(entry)
    result = await agent.ainvoke(Command(resume={"decisions": decisions}), config, version="v2")
    # GraphOutput is dict-like (supports __getitem__) but has no ``.get``.
    # Use the bracket form so the v2 invoke result is unpacked the same
    # way the v3 streaming path does.
    raw_messages = result["messages"] if "messages" in result else []  # noqa: SIM401
    messages = [_msg_to_chat_message(m) for m in raw_messages]
    interrupted = bool(getattr(result, "interrupts", None))
    return ResumeResponse(thread_id=scoped, messages=messages, interrupted=interrupted)


@app.post("/v1/eval", response_model=EvalReport)
async def run_eval_endpoint(
    payload: EvalRequest,
    caller: str = Depends(verify_api_key),
) -> EvalReport:
    """Run the golden-dataset eval and return a graded report."""
    del caller
    return await run_eval(
        min_pass_rate=payload.min_pass_rate,
    )


@app.get("/v1/audit", response_model=AuditReport)
async def run_audit_endpoint(
    caller: str = Depends(verify_api_key),
) -> AuditReport:
    """Run the in-process audit harness and return a structured report."""
    del caller
    return await run_audit()
