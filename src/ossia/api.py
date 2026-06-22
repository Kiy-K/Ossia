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

import asyncio
import hashlib
import logging
import os
import secrets
import uuid
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from dotenv import find_dotenv, load_dotenv

# Populate os.environ from .env before langchain/langsmith reads tracing config.
load_dotenv(find_dotenv(usecwd=True))

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

from ossia.agent import build_agent_async  # noqa: E402
from ossia.audit import run_audit  # noqa: E402
from ossia.config import get_settings  # noqa: E402
from ossia.context import OssiaContext  # noqa: E402
from ossia.eval import run_eval  # noqa: E402
from ossia.memory import get_checkpointer  # noqa: E402
from ossia.schemas import (  # noqa: E402
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
    StreamEvent,
    ThreadHistoryResponse,
    ThreadStateResponse,
    ToolInfo,
    ToolListResponse,
)

logger = logging.getLogger(__name__)


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
    return hashlib.sha256(provided.encode()).hexdigest()[:16]


def _thread_id_for(caller: str, requested: str | None) -> str:
    """Scope a thread id to the authenticated caller."""
    base = requested or "default"
    return f"{caller}:{base}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize agent and checkpointer on startup, clean up on shutdown."""
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
                tools_node.bound._tools_by_name.items()
                if hasattr(tools_node.bound, "_tools_by_name")
                else []
            ):
                srv = getattr(tool, "_mcp_server", None)
                if srv:
                    mcp_servers[tname] = srv
        app.state.mcp_tool_servers = mcp_servers
        yield


app = FastAPI(
    title="Ossia Support Agent",
    version="1.0.0",
    description="Unified HTTP API for the Ossia support agent.",
    lifespan=lifespan,
)


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
    echoes it on the response. Error envelope conversion is done by the
    exception handlers below so dependencies and validation errors are
    also wrapped consistently.
    """
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
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
    """Normalize a LangChain message into the ChatMessage wire schema."""
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

    content = getattr(msg, "content", "")
    if isinstance(content, list):
        text_parts = [
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        content = "".join(text_parts)
    if not isinstance(content, str):
        content = str(content)

    tool_calls = [
        {"id": tc.get("id", ""), "name": tc.get("name", ""), "args": tc.get("args", {})}
        for tc in (getattr(msg, "tool_calls", None) or [])
    ]
    return ChatMessage(
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=getattr(msg, "tool_call_id", None),
        name=getattr(msg, "name", None),
    )


def _build_invocation(
    payload: ChatRequest, thread_id: str
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Build the (agent, input, config) tuple used by chat + chat_stream.

    Centralized so the two routes cannot drift on how the agent is invoked.
    """
    agent = app.state.agent
    config = {"configurable": {"thread_id": thread_id}}
    input_dict = {"messages": [HumanMessage(content=payload.message)]}
    return agent, input_dict, config


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness check."""
    return HealthResponse(status="ok")


@app.post("/v1/chat", response_model=ChatResponse)
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

        def _safe(obj: Any) -> Any:
            """Best-effort conversion to a JSON-friendly value."""
            if obj is None or isinstance(obj, (bool, int, float, str)):
                return obj
            if isinstance(obj, dict):
                return {str(k): _safe(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_safe(v) for v in obj]
            if hasattr(obj, "model_dump"):
                try:
                    return _safe(obj.model_dump())
                except Exception:
                    return repr(obj)
            if hasattr(obj, "value") and not callable(obj.value):
                return _safe(obj.value)
            return str(obj)

        async def emit(kind: str, payload: dict[str, Any]) -> str:
            evt = StreamEvent(kind=kind, data=_safe(payload))  # type: ignore[arg-type]
            return f"event: {evt.kind}\ndata: {evt.model_dump_json()}\n\n"

        async def relay_messages() -> AsyncGenerator[str, None]:
            try:
                async for m in stream.messages:
                    text = getattr(m, "text", None)
                    if text is None:
                        # Some message types have no `text`; skip.
                        continue
                    yield await emit(
                        "message",
                        {
                            "role": getattr(m, "role", "ai"),
                            "text": str(text),
                            "id": getattr(m, "id", None),
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("messages projection failed: %r", exc)

        async def relay_tool_calls() -> AsyncGenerator[str, None]:
            try:
                async for c in stream.tool_calls:
                    deltas: list[str] = []
                    # Drain the per-token output stream if the tool streams.
                    try:
                        async for d in c.output_deltas:
                            deltas.append(str(d))
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("output_deltas failed: %r", exc)
                    error = getattr(c, "error", None)
                    payload_d: dict[str, Any] = {
                        "name": getattr(c, "tool_name", ""),
                        "input": getattr(c, "input", {}) or {},
                        "output": getattr(c, "output", None),
                        "completed": bool(getattr(c, "completed", False)),
                        "error": str(error) if error is not None else None,
                        "output_deltas": deltas,
                    }
                    yield await emit("tool_call", payload_d)
            except Exception as exc:  # noqa: BLE001
                logger.debug("tool_calls projection failed: %r", exc)

        async def relay_subagents() -> AsyncGenerator[str, None]:
            try:
                async for s in stream.subagents:
                    yield await emit(
                        "subagent",
                        {
                            "name": getattr(s, "name", ""),
                            "status": str(getattr(s, "status", "unknown")),
                            "path": list(getattr(s, "path", []) or []),
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("subagents projection failed: %r", exc)

        async def relay_values() -> AsyncGenerator[str, None]:
            # State snapshots are noisy; only emit the final one. Drain the
            # projection by reading until exhaustion so it does not block
            # the other consumers.
            try:
                last: Any = None
                async for v in stream.values:
                    last = v
                if last is not None:
                    yield await emit("value", {"values": _safe(last)})
            except Exception as exc:  # noqa: BLE001
                logger.debug("values projection failed: %r", exc)

        # Drive all projections concurrently. ``asyncio.gather`` returns
        # when every consumer finishes; cancellation propagates.
        results = await asyncio.gather(
            _drain(relay_messages()),
            _drain(relay_tool_calls()),
            _drain(relay_subagents()),
            _drain(relay_values()),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException) and not isinstance(
                r, asyncio.CancelledError
            ):
                logger.debug("stream projection raised: %r", r)

        # Final complete event. The v3 stream exposes ``output`` and
        # ``interrupted`` / ``interrupts`` as properties that drive the
        # run to completion on access.
        try:
            output = stream.output
            output_dict = _safe(output) if output is not None else {}
            if not isinstance(output_dict, dict):
                output_dict = {"output": output_dict}
        except Exception as exc:  # noqa: BLE001
            logger.debug("stream.output failed: %r", exc)
            output_dict = {}
        try:
            interrupted = bool(stream.interrupted)
        except Exception as exc:  # noqa: BLE001
            logger.debug("stream.interrupted failed: %r", exc)
            interrupted = False
        interrupt_payload: list[dict[str, Any]] = []
        if interrupted:
            try:
                for it in stream.interrupts or ():
                    raw = it.value if hasattr(it, "value") else it
                    interrupt_payload.append(_safe(raw))
            except Exception as exc:  # noqa: BLE001
                logger.debug("stream.interrupts failed: %r", exc)
        if interrupt_payload:
            yield await emit("interrupt", {"interrupts": interrupt_payload})
        yield await emit(
            "complete", {"output": output_dict, "interrupted": interrupted}
        )

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
        dataset_path=payload.dataset_path,
        min_pass_rate=payload.min_pass_rate,
    )


@app.get("/v1/audit", response_model=AuditReport)
async def run_audit_endpoint(
    caller: str = Depends(verify_api_key),
) -> AuditReport:
    """Run the in-process audit harness and return a structured report."""
    del caller
    return await run_audit()
