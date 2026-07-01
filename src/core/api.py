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
from slowapi.util import get_remote_address  # noqa: E402

from core.agent import build_agent_async  # noqa: E402
from core.audit import run_audit  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.context import OssiaContext  # noqa: E402
from core.eval import run_eval  # noqa: E402
from core.events import EventNormalizer, get_thread_event_buffer, serialize_sse  # noqa: E402
from core.memory import get_checkpointer  # noqa: E402
from core.request_context import clear_request_context, set_request_context  # noqa: E402
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
    ResumeRequest,
    ResumeResponse,
    ThreadEventsResponse,
    ThreadHistoryResponse,
    ThreadStateResponse,
    ToolInfo,
    ToolListResponse,
)

logger = logging.getLogger(__name__)

# ── Rate limiter ─────────────────────────────────────────────────────────────
# In-memory rate limiter backed by slowapi. Limits are applied per remote IP.
# Chat endpoints get a stricter limit than the read-only endpoints.
# The /health and /metrics endpoints are excluded from rate limiting.
# In production behind a reverse proxy, slowapi reads X-Forwarded-For
# headers to identify the real client IP (see TrustedHost middleware).

_RATE_LIMIT_CHAT = "30/minute"       # POST /v1/chat*
_RATE_LIMIT_DEFAULT = "60/minute"    # all other authenticated endpoints
_RATE_LIMIT_HEALTH = "120/minute"    # /health is cheap

limiter = Limiter(key_func=get_remote_address, default_limits=[_RATE_LIMIT_DEFAULT])


def _expected_api_key() -> str | None:
    """Return the expected API key from the OSSIA_API_KEY env var."""
    return os.environ.get("OSSIA_API_KEY")


def _require_api_key_at_startup() -> str:
    """Validate the API key is configured at startup, failing fast otherwise."""
    expected = _expected_api_key()
    if not expected:
        raise RuntimeError(
            "OSSIA_API_KEY is not configured. Set it in the environment or .env "
            "before starting the server."
        )
    return expected


async def verify_api_key(request: Request) -> str:
    """Validate the X-API-Key header and return a caller identifier.

    Returns:
        A short, stable caller identifier (sha256[:16] of the provided key)
        used to scope thread ids so authenticated callers cannot access each
        other's conversation state.

    Raises:
        HTTPException: 401 when the API key is missing or invalid; 500 when the
        server is misconfigured (no OSSIA_API_KEY in the environment).
    """
    expected = _expected_api_key()
    provided = request.headers.get("x-api-key", "")
    if not expected:
        raise HTTPException(
            status_code=500,
            detail="OSSIA_API_KEY is not configured on the server.",
        )
    # Reject obviously oversized keys up front. ``compare_digest`` is
    # constant-time but its cost is linear in input length; bounding the
    # header here prevents an attacker from forcing a multi-MB compare on
    # every request to a protected route.
    if (
        not provided
        or len(provided) > 256
        or len(provided) != len(expected)
        or not secrets.compare_digest(provided, expected)
    ):
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
    """Scope a thread id to the authenticated caller."""
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
    if settings.enable_human_review and not settings.postgres_url:
        raise RuntimeError(
            "POSTGRES_URL is required when ENABLE_HUMAN_REVIEW is true, because "
            "human-in-the-loop interrupts need a checkpointer to persist state."
        )
    async with AsyncExitStack() as stack:
        checkpointer = None
        if settings.postgres_url:
            checkpointer = await stack.enter_async_context(get_checkpointer(settings))
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
            logger.info("Shutting down — draining active requests.")


app = FastAPI(
    title="Ossia Support Agent",
    version="1.0.0",
    description="Unified HTTP API for the Ossia support agent.",
    lifespan=lifespan,
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
                    artifact_refs.append(ArtifactInfo(
                        id=f"img-{i}",
                        type="image",
                        filename=f"image-{i}.png",
                        mime_type="image/png",
                        analysis_state="pending",
                    ))
                elif ptype == "file":
                    source = p.get("source", {})
                    mime = source.get("mime_type", "application/octet-stream") if isinstance(source, dict) else "application/octet-stream"
                    ftype = (
                        "document" if mime.startswith("application/") else
                        "image" if mime.startswith("image/") else
                        "audio" if mime.startswith("audio/") else
                        "video" if mime.startswith("video/") else
                        "document"
                    )
                    artifact_refs.append(ArtifactInfo(
                        id=f"file-{i}",
                        type=ftype,  # type: ignore[arg-type]
                        filename=p.get("filename", f"file-{i}"),
                        mime_type=mime,
                        analysis_state="pending",
                    ))
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
    for art in (payload.artifacts or []):
        block = _normalize_artifact(art)
        if block is not None:
            content.append(block)
    input_dict = {"messages": [HumanMessage(content=content)]}  # type: ignore[arg-type]
    return agent, input_dict, config


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
) -> ChatResponse:
    """Run a single chat turn against the agent."""
    thread_id = _thread_id_for(caller, payload.thread_id)
    agent, input_dict, config = _build_invocation(payload, thread_id)
    context = OssiaContext(
        caller=caller,
        request_id=getattr(request.state, "request_id", None),
    )
    result = await agent.ainvoke(input_dict, config, context=context)
    messages = [_msg_to_chat_message(m) for m in result.get("messages", [])]
    return ChatResponse(thread_id=thread_id, messages=messages)


@app.post("/v1/chat/stream")
@limiter.limit(_RATE_LIMIT_CHAT)
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    caller: str = Depends(verify_api_key),
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
    """
    thread_id = _thread_id_for(caller, payload.thread_id)
    agent, input_dict, config = _build_invocation(payload, thread_id)
    context = OssiaContext(
        caller=caller,
        request_id=getattr(request.state, "request_id", None),
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        stream = await agent.astream_events(
            input_dict, config, version="v3", context=context
        )
        # Use the EventNormalizer to convert the DeepAgent v3 stream
        # into normalized OssiaEvent objects, then serialize each to SSE.
        normalizer = EventNormalizer(thread_id=thread_id)
        buffer = get_thread_event_buffer()
        collected: list[Any] = []
        async for event in normalizer.normalize(
            stream, artifacts=payload.artifacts or []
        ):
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


@app.get("/v1/threads/{thread_id}/state", response_model=ThreadStateResponse)
async def thread_state(
    thread_id: str,
    caller: str = Depends(verify_api_key),
) -> ThreadStateResponse:
    """Return the latest checkpointed state for a thread."""
    scoped = _thread_id_for(caller, thread_id)
    if app.state.checkpointer is None:
        return ThreadStateResponse(
            thread_id=scoped, values={}, next=[], config={}
        )
    agent = app.state.agent
    snapshot = await agent.aget_state({"configurable": {"thread_id": scoped}})
    if snapshot is None:
        return ThreadStateResponse(
            thread_id=scoped, values={}, next=[], config={}
        )
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


@app.get(
    "/v1/threads/{thread_id}/history", response_model=ThreadHistoryResponse
)
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
    async for snap in agent.aget_state_history(
        {"configurable": {"thread_id": scoped}}
    ):
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
    result = await agent.ainvoke(
        Command(resume={"decisions": decisions}), config, version="v2"
    )
    messages = [_msg_to_chat_message(m) for m in result.get("messages", [])]
    interrupted = bool(getattr(result, "interrupts", None))
    return ResumeResponse(
        thread_id=scoped, messages=messages, interrupted=interrupted
    )


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
