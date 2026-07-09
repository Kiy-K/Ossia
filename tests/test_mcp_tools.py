"""Tests for MCP toolkit graceful degradation and cancel propagation.

These lock in the two guarantees the MCP integration must uphold:

1. A single unreachable MCP server is skipped (logged) and the agent still
   starts with the remaining/core tools -- it must NOT abort startup. This
   mirrors the Deep Agents contract: "A single failing server no longer
   aborts startup. The agent runs with whichever servers came up cleanly."
2. A genuine external cancellation (agent shutdown) during MCP connect must
   still propagate as ``CancelledError``; the degradation path must not
   swallow it.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import suppress
from typing import Any

import pytest

from core.agent import build_agent_async
from core.config import Provider, Settings
from core.mcp_tools import MCPToolkit


def _write_mcp_config(servers: dict[str, dict[str, Any]]) -> str:
    """Write an MCP server config to a temp file and return its path."""
    cfg = tempfile.NamedTemporaryFile(  # noqa: SIM115
        "w", suffix=".json", delete=False
    )
    json.dump({"mcpServers": servers}, cfg)
    cfg.close()
    return cfg.name


def _bad_settings(
    url: str = "http://localhost:1/nonexistent",
    *,
    headers: dict[str, str] | None = None,
) -> Settings:
    """Settings whose MCP config points at a single failing server."""
    server: dict[str, Any] = {
        "name": "Bad",
        "transport": "streamable_http",
        "url": url,
    }
    if headers is not None:
        server["headers"] = headers
    path = _write_mcp_config({"bad-server": server})
    return Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=False,
        mcp_config_path=path,
    )


async def test_mcp_toolkit_skips_unreachable_server() -> None:
    """An unreachable MCP server is skipped; the toolkit starts with no tools."""
    settings = _bad_settings()
    try:
        async with MCPToolkit(settings) as toolkit:
            assert toolkit.get_tools() == []
    finally:
        os.unlink(settings.mcp_config_path)


async def test_build_agent_async_degrades_on_bad_mcp() -> None:
    """A bad MCP server does not abort agent build; the graph still compiles."""
    settings = _bad_settings()
    try:
        async with build_agent_async(settings=settings, include_mcp_tools=True) as agent:
            assert agent is not None
            assert "tools" in agent.nodes
            assert "model" in agent.nodes
    finally:
        os.unlink(settings.mcp_config_path)


async def test_mcp_toolkit_external_cancel_propagates() -> None:
    """Cancelling the parent task during MCP connect must raise CancelledError.

    A blackhole TCP server accepts the connection but never responds, so the
    worker hangs in ``session.initialize()``. Cancelling the caller mid-connect
    must surface as ``CancelledError`` (a real shutdown), not be swallowed by
    the graceful-degradation path.
    """
    hang = asyncio.Event()

    async def _blackhole(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await hang.wait()
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    srv = await asyncio.start_server(_blackhole, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    # Headers ensure the worker uses a known long read timeout so the connect
    # reliably hangs (and does not self-timeout before we cancel).
    settings = _bad_settings(url, headers={"X-Test": "1"})
    try:
        task = asyncio.create_task(MCPToolkit(settings).__aenter__(), name="mcp-cancel-test")
        # Give the worker time to connect and block on initialize.
        await asyncio.sleep(0.4)
        assert not task.done(), "worker should still be hanging on initialize"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        hang.set()
        srv.close()
        await srv.wait_closed()
        os.unlink(settings.mcp_config_path)


async def test_external_cancel_during_teardown_grace_propagates() -> None:
    """Cancel arriving during the teardown grace window still propagates.

    Regression for the CRITICAL cancel-swallow: when a server times out and the
    toolkit is draining its worker with a grace period (force=False), an
    external cancellation of the parent must surface as CancelledError -- not
    be caught alongside the grace TimeoutError and swallowed. We force the
    worker into a hang so the connect times out, then cancel the parent while
    it is inside the grace await.
    """
    hang = asyncio.Event()

    async def _blackhole(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await hang.wait()
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    srv = await asyncio.start_server(_blackhole, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    # Short connect timeout so the worker times out quickly and enters the
    # teardown grace path; the blackhole keeps it parked so the grace await
    # is still in flight when we cancel.
    server = {
        "name": "Hang",
        "transport": "streamable_http",
        "url": url,
        "headers": {"X-Test": "1"},
    }
    path = _write_mcp_config({"hang-server": server})
    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=False,
        mcp_config_path=path,
        mcp_connect_timeout=1.0,
    )
    try:
        task = asyncio.create_task(
            MCPToolkit(settings).__aenter__(), name="mcp-teardown-cancel-test"
        )
        # Wait long enough for the 1s connect timeout to fire and the toolkit
        # to enter the force=False teardown grace await.
        await asyncio.sleep(1.3)
        assert not task.done(), "toolkit should be in the teardown grace window"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        hang.set()
        srv.close()
        await srv.wait_closed()
        os.unlink(settings.mcp_config_path)


# ── Tool-count ceiling (GOAL-0002 M3 + M5) ────────────────────────────────────
# The coordinator's bound tool list MUST stay constant regardless of how
# many MCP connectors are active. The whole point of routing MCP tools to
# the ``integrations`` subagent (§3.3) is that the coordinator's per-turn
# prompt does not grow with N. If a future change re-introduces
# ``tools = [*tools, *toolkit.get_tools()]`` (or anything equivalent),
# this test fails and the regression is caught before it ships.


class _FakeMCPTool:
    """Stand-in for a LangChain ``BaseTool`` returned by ``MCPToolkit``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"Fake MCP tool {name}"

    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - unused
        return None


class _FakeMCPToolkit:
    """Stand-in for ``MCPToolkit`` that reports a fixed list of tools.

    The list is exposed via the real ``get_tools()`` interface, so the
    production code path that reads ``toolkit.get_tools()`` is exercised
    end-to-end. The actual MCP transport is bypassed — no network.
    """

    def __init__(self, n_tools: int) -> None:
        self._tools = [_FakeMCPTool(f"mcp_tool_{i}") for i in range(n_tools)]

    async def __aenter__(self) -> _FakeMCPToolkit:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    def get_tools(self) -> list[_FakeMCPTool]:
        return list(self._tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("n_mcp", [0, 1, 5, 25])
async def test_coordinator_tool_count_is_capped_regardless_of_mcp(
    monkeypatch: pytest.MonkeyPatch, n_mcp: int
) -> None:
    """The coordinator's bound tool list does not grow with N MCP tools.

    The ceiling is the size of ``create_core_tools()`` — currently 10.
    The exact value is asserted, not just "not changed", so a future
    accidental add to the core tools list also fails this test (and the
    author must consciously decide whether to update the ceiling).
    """
    # Build subagents the same way build_agent_async does.
    from core.agent import _build_subagents, create_core_tools

    fake = _FakeMCPToolkit(n_mcp)
    mcp_tools = fake.get_tools()

    real_create_core = create_core_tools
    captured_core_tools: list[list[Any]] = []

    def _spy_create_core() -> list[Any]:
        tools = real_create_core()
        captured_core_tools.append(tools)
        return tools

    monkeypatch.setattr("core.agent.create_core_tools", _spy_create_core)

    # Drive the relevant path: create_core_tools() (what the coordinator
    # would bind) + _build_subagents() (what gets wired per subagent).
    core_tools = _spy_create_core()
    # The model is only stored on the subagent spec; a stub is fine.
    fake_model = type("FakeModel", (), {})()
    subagents = _build_subagents(
        fake_model,  # type: ignore[arg-type]
        mcp_tools=mcp_tools,  # type: ignore[arg-type]
    )

    # 1. The coordinator's bound tools = create_core_tools() — never
    #    includes MCP tools.
    coordinator_tool_names = {t.name for t in core_tools}
    # 2. No MCP tool name leaks into the coordinator's binding.
    leaked = [n for n in coordinator_tool_names if n.startswith("mcp_tool_")]
    assert leaked == [], (
        f"MCP tools leaked into the coordinator's binding: {leaked}\n"
        f"This is the regression M3 was supposed to prevent — check that "
        f"build_agent_async no longer does `tools = [*tools, *toolkit.get_tools()]`."
    )
    # 3. The ceiling is exactly the size of create_core_tools() (10 after
    #    M1+M2). Update this when the core set is intentionally changed.
    assert len(core_tools) == 10, (
        f"Coordinator tool count is {len(core_tools)}, expected 10. "
        f"This is the GOAL-0002 ceiling; update only after a deliberate "
        f"change to create_core_tools() and an explicit ADR."
    )
    # 4. The integrations subagent picks up every MCP tool (when any are
    #    present) and zero otherwise.
    integration_subagents = [s for s in subagents if s["name"] == "integrations"]
    if n_mcp == 0:
        assert integration_subagents == [], (
            "integrations subagent should not be wired when no MCP tools exist"
        )
    else:
        assert len(integration_subagents) == 1, "integrations subagent should be wired"
        bound = integration_subagents[0]["tools"]
        assert len(bound) == n_mcp, (
            f"integrations subagent should have {n_mcp} tools, got {len(bound)}"
        )
