"""FastAPI application exposing the Ossia agent over HTTP."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from dotenv import find_dotenv, load_dotenv

# Populate os.environ from .env before langchain/langsmith reads tracing config.
load_dotenv(find_dotenv(usecwd=True))

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

from ossia.agent import build_agent_async  # noqa: E402
from ossia.config import get_settings  # noqa: E402
from ossia.memory import get_checkpointer  # noqa: E402


def _expected_api_key() -> str | None:
    """Return the expected API key from the OSSIA_API_KEY env var."""
    return os.environ.get("OSSIA_API_KEY")


def _require_api_key_at_startup() -> str:
    """Validate the API key is configured at startup, failing fast otherwise.

    Raises:
        RuntimeError: When OSSIA_API_KEY is not set.
    """
    expected = _expected_api_key()
    if not expected:
        raise RuntimeError(
            "OSSIA_API_KEY is not configured. Set it in the environment or .env "
            "before starting the server."
        )
    return expected


async def verify_api_key(request: Request) -> str:
    """Dependency that validates the X-API-Key header.

    Args:
        request: Incoming HTTP request.

    Returns:
        The authenticated caller identifier derived from the API key.

    Raises:
        HTTPException: 401 when the API key is missing or invalid.
    """
    expected = _expected_api_key()
    provided = request.headers.get("x-api-key", "")

    if not expected:
        raise HTTPException(
            status_code=500,
            detail="OSSIA_API_KEY is not configured on the server.",
        )
    # Constant-time comparison to avoid leaking the key via timing.
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")

    return hashlib.sha256(provided.encode()).hexdigest()[:16]


def _thread_id_for(caller: str, requested: str | None) -> str:
    """Scope a thread id to the authenticated caller.

    Args:
        caller: Caller identifier derived from the API key.
        requested: Optional thread id requested by the client.

    Returns:
        A caller-scoped thread id, preventing cross-user state access.
    """
    base = requested or "default"
    return f"{caller}:{base}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize agent and checkpointer on startup, clean up on shutdown."""
    # Fail fast on missing required configuration so a misconfigured deploy does
    # not pass health checks while serving 500s on every request.
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
        yield


app = FastAPI(title="Ossia Support Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/chat")
async def chat(
    request: dict[str, Any],
    caller: str = Depends(verify_api_key),
) -> dict[str, Any]:
    """Run a single chat turn against the agent.

    Args:
        request: JSON body with `message` and optional `thread_id`.
        caller: Authenticated caller identifier.

    Returns:
        Final agent state as JSON.
    """
    message = request.get("message")
    thread_id = _thread_id_for(caller, request.get("thread_id"))
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    agent = app.state.agent
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config,
    )
    return {"messages": [m.dict() for m in result.get("messages", [])]}


@app.post("/chat/stream")
async def chat_stream(
    request: dict[str, Any],
    caller: str = Depends(verify_api_key),
) -> StreamingResponse:
    """Stream agent events for a chat turn as Server-Sent Events.

    Args:
        request: JSON body with `message` and optional `thread_id`.
        caller: Authenticated caller identifier.

    Returns:
        Server-sent events stream with JSON-encoded event payloads.
    """
    message = request.get("message")
    thread_id = _thread_id_for(caller, request.get("thread_id"))
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    agent = app.state.agent
    config = {"configurable": {"thread_id": thread_id}}

    async def event_stream() -> AsyncGenerator[str, None]:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config,
            version="v2",
        ):
            payload = json.dumps(event, default=str)
            yield f"event: {event['event']}\ndata: {payload}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
