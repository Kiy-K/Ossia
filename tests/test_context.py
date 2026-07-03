"""Tests for the runtime context wiring.

Per the Deep Agents "Context engineering" doc, runtime context is
per-invoke configuration that propagates to all subagents. Tools can
read it via the injected ``ToolRuntime``; the FastAPI layer constructs
an :class:`OssiaContext` and passes it as ``context=`` to ``ainvoke`` /
``astream_events``.

Tests cover:

1. ``OssiaContext`` is a frozen dataclass with the expected fields.
2. The compiled agent is built with ``context_schema=OssiaContext``.
3. The FastAPI ``/v1/chat`` handler constructs an ``OssiaContext`` and
   passes it as ``context=`` to ``agent.ainvoke``.
4. The FastAPI ``/v1/chat/stream`` handler does the same for
   ``agent.astream_events`` (with the v3 ``context=`` kwarg).
5. ``grade_response`` is callable without a runtime (backward
   compat) and reads ``runtime.context.caller`` when present.
"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import find_dotenv, load_dotenv

from core.context import OssiaContext

# Match the env shape used by test_api.py: a known API key, human
# review off, no Postgres. Required for the FastAPI lifespan to boot.
load_dotenv(find_dotenv(usecwd=True))
os.environ["OSSIA_API_KEY"] = "test-api-key"
os.environ["ENABLE_HUMAN_REVIEW"] = "false"
os.environ["POSTGRES_URL"] = ""


# ---------------------------------------------------------------------------
# Context dataclass
# ---------------------------------------------------------------------------


def test_ossia_context_is_frozen() -> None:
    """Per the doc, context is per-invoke and immutable."""
    ctx = OssiaContext(caller="abc123")
    with pytest.raises(FrozenInstanceError):
        ctx.caller = "different"  # type: ignore[misc]


def test_ossia_context_default_provider() -> None:
    ctx = OssiaContext(caller="abc")
    assert ctx.caller == "abc"
    assert ctx.request_id is None
    assert ctx.provider == "openrouter"


def test_ossia_context_with_request_id() -> None:
    ctx = OssiaContext(caller="abc", request_id="req-1")
    assert ctx.request_id == "req-1"


# ---------------------------------------------------------------------------
# Agent wiring: context_schema is OssiaContext
# ---------------------------------------------------------------------------


def test_compiled_agent_passes_context_schema() -> None:
    """``create_deep_agent`` is called with ``context_schema=OssiaContext``."""

    from core.agent import build_agent
    from core.config import Provider, Settings

    with (
        patch("core.agent.create_deep_agent") as mock_create,
        patch("core.agent.create_chat_model"),
        patch("core.agent.load_system_prompt", return_value="sys"),
    ):
        mock_create.return_value = MagicMock()
        s = Settings(
            provider=Provider.OPENROUTER,
            model="openai/gpt-4o-mini",
            openrouter_api_key="sk-test",
            enable_human_review=False,
        )
        build_agent(settings=s)
        assert mock_create.call_args.kwargs.get("context_schema") is OssiaContext


# ---------------------------------------------------------------------------
# FastAPI handlers: pass context= to ainvoke / astream_events
# ---------------------------------------------------------------------------


def test_chat_handler_passes_ossia_context_to_ainvoke() -> None:
    """``POST /v1/chat`` constructs an ``OssiaContext`` and passes it
    via ``context=`` to ``agent.ainvoke``.
    """
    from fastapi.testclient import TestClient

    from core.api import app
    from core.context import OssiaContext

    fake_agent = MagicMock()
    fake_agent.ainvoke = AsyncMock(return_value={"messages": []})

    with patch("core.api.build_agent_async") as ba:

        class _CM:
            async def __aenter__(self):
                return fake_agent

            async def __aexit__(self, *_):
                return False

        ba.return_value = _CM()

        with TestClient(app) as client:
            r = client.post(
                "/v1/chat",
                json={"message": "hi", "thread_id": "t1"},
                headers={"X-API-Key": "test-api-key", "X-Request-ID": "rid-1"},
            )
    assert r.status_code == 200, r.text
    assert fake_agent.ainvoke.await_count == 1
    kwargs = fake_agent.ainvoke.await_args.kwargs
    assert "context" in kwargs
    ctx = kwargs["context"]
    assert isinstance(ctx, OssiaContext)
    assert ctx.caller != ""
    assert ctx.request_id == "rid-1"


def test_chat_stream_handler_passes_ossia_context() -> None:
    """``POST /v1/chat/stream`` constructs an ``OssiaContext`` and
    passes it via ``context=`` to ``agent.astream_events``.
    """
    from fastapi.testclient import TestClient

    from core.api import app
    from core.context import OssiaContext

    captured: dict[str, Any] = {}

    class _FakeStream:
        def __init__(self) -> None:
            self.messages: list[Any] = []
            self.tool_calls: list[Any] = []
            self.subagents: list[Any] = []
            self.values: list[Any] = []
            self.interrupted = False
            self.interrupts: list[Any] = []
            self.output: dict = {}

        def __aiter__(self) -> _FakeStream:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    fake_agent = MagicMock()

    async def _capture(*_a: Any, **kw: Any) -> _FakeStream:
        captured.update(kw)
        return _FakeStream()

    fake_agent.astream_events = _capture

    with patch("core.api.build_agent_async") as ba:

        class _CM:
            async def __aenter__(self):
                return fake_agent

            async def __aexit__(self, *_):
                return False

        ba.return_value = _CM()

        with TestClient(app) as client:
            r = client.post(
                "/v1/chat/stream",
                json={"message": "hi", "thread_id": "t1"},
                headers={"X-API-Key": "test-api-key", "X-Request-ID": "rid-2"},
            )
    assert r.status_code == 200, r.text
    assert "context" in captured
    ctx = captured["context"]
    assert isinstance(ctx, OssiaContext)
    assert ctx.request_id == "rid-2"


# ---------------------------------------------------------------------------
# grade_response: backward compat + ToolRuntime injection
# ---------------------------------------------------------------------------


def test_grade_response_callable_without_runtime() -> None:
    """``grade_response`` works without a ``ToolRuntime`` (e.g. from
    tests or one-off scripts).
    """
    from core.tools import grade_response

    # Response includes the query word "what" so the token-match
    # check passes, and is long enough (>= 40 chars) and has a
    # non-"No" context. Score = 3/3.
    result = grade_response.invoke(
        {
            "query": "what is X?",
            "response": "X is a placeholder concept; the answer to what you asked is short but on point.",
            "context": "ctx",
        }
    )
    assert result.passes is True
    assert result.score >= 0.67


def test_grade_response_reads_runtime_context() -> None:
    """``grade_response`` reads ``runtime.context.caller`` when injected
    by the deepagent ToolNode at call time.

    We call the underlying function directly with a fake ``runtime``;
    the public ``StructuredTool.invoke`` strips non-schema args, so the
    runtime path is only exercised by the deepagent ToolNode at runtime.
    """
    from core.tools import grade_response

    fake_runtime = MagicMock()
    fake_runtime.context.caller = "test-caller-42"

    with patch("core.tools.logger") as log:
        result = grade_response.func(
            query="what is X?",
            response=(
                "X is a placeholder concept; the answer to what you asked is short but on point."
            ),
            context="ctx",
            runtime=fake_runtime,
        )
    assert result.passes is True
    log.debug.assert_called()
    args, kwargs = log.debug.call_args
    # logger.debug is called with (format, *args): the format string
    # plus positional substitutions. The caller is the second
    # positional arg.
    assert "test-caller-42" in (str(args) + str(kwargs))
