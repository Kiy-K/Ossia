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
        task = asyncio.create_task(
            MCPToolkit(settings).__aenter__(), name="mcp-cancel-test"
        )
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
