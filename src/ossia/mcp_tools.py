"""MCP client integration for loading tools from configured MCP servers.

The Ossia agent can optionally load tools from external MCP servers. The default
configuration points to the LangChain Docs MCP server at
https://docs.langchain.com/mcp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import BaseTool
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field, create_model

from ossia.config import Settings, get_settings

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str = Field(description="Human-readable server name.")
    transport: str = Field(
        default="streamable_http",
        description="Transport type: streamable_http or sse.",
    )
    url: str = Field(description="Base URL of the MCP server.")
    headers: dict[str, str] = Field(default_factory=dict, description="Extra HTTP headers.")


class MCPConfig(BaseModel):
    """Top-level MCP configuration file schema."""

    mcp_servers: dict[str, MCPServerConfig] = Field(alias="mcpServers")


async def load_mcp_config(path: str | Path) -> MCPConfig:
    """Load MCP server configuration from a JSON file.

    Args:
        path: Path to the MCP configuration file.

    Returns:
        Parsed MCP configuration.
    """
    content = Path(path).read_text(encoding="utf-8")
    data = json.loads(content)
    return MCPConfig.model_validate(data)


class McpServerConnectionError(Exception):
    """Raised (internally) to represent a single MCP server that could not start.

    The transport's internal task-group cancellation on a connection failure
    surfaces as ``asyncio.CancelledError`` -- a ``BaseException``. Inside a
    worker task we convert that into this regular ``Exception`` so the parent
    can treat it as an ordinary per-server failure via ``await ready`` instead
    of mistaking it for an external cancellation.
    """

    def __init__(self, name: str, url: str, cause: BaseException) -> None:
        super().__init__(f"MCP server {name!r} at {url} unavailable: {cause}")
        self.name = name
        self.url = url
        self.__cause__ = cause


# Grace period given to a worker to tear its session down before forcing a cancel.
_TEARDOWN_GRACE_S: float = 5.0


class _ServerWorker:
    """Owns one MCP server's session for its entire lifetime, in its own task.

    ``streamable_http_client`` runs the consumer code inside an ``anyio`` task
    group whose cancel scope is *task-affine*: it must be entered and exited in
    the same task, and on a connection failure anyio cancels that scope. By
    running the whole connect/initialize/list-tools sequence (and the session's
    parking lifetime) inside a dedicated worker task, the cancel scope never
    crosses into the parent. A failing server therefore cancels only its own
    worker; the parent is never cancelled by a transport-internal failure, so
    ``current_task().cancelling()`` on the parent reliably distinguishes a
    genuine external shutdown from a per-server failure.

    On success the worker resolves ``ready`` with the discovered tools and then
    parks on ``_shutdown`` to keep the session (and the transport's post writer)
    alive for the agent's lifetime. Tool calls from other tasks share the
    session's memory streams and are serviced by the parked worker's transport.
    """

    def __init__(self, name: str, server: MCPServerConfig) -> None:
        self.name = name
        self.server = server
        self.tools: list[BaseTool] = []
        self.ready: asyncio.Future[list[BaseTool]] = asyncio.get_running_loop().create_future()
        self._shutdown = asyncio.Event()
        self._stack: AsyncExitStack | None = None
        self.task: asyncio.Task[None] | None = None

    def shutdown(self) -> None:
        """Signal the worker to tear its session down and exit."""
        self._shutdown.set()

    async def run(self) -> None:
        """Connect, report tools, park until shutdown, then tear down."""
        try:
            self._stack = AsyncExitStack()
            client: httpx.AsyncClient | None = None
            if self.server.headers:
                client = await self._stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=self.server.headers,
                        timeout=httpx.Timeout(30.0),
                    ),
                )
            read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
                streamable_http_client(self.server.url, http_client=client),
            )
            session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream),
            )
            await session.initialize()
            response = await session.list_tools()
            self.tools = [_wrap_mcp_tool(session, info) for info in response.tools]
            if not self.ready.done():
                self.ready.set_result(self.tools)
            await self._shutdown.wait()
        except asyncio.CancelledError as exc:
            # The transport cancelled its internal task group (a connection
            # failure). Convert to a regular exception so the parent's
            # ``await ready`` sees a normal server failure rather than a
            # BaseException that would look like an external cancellation.
            if not self.ready.done():
                self.ready.set_exception(McpServerConnectionError(self.name, self.server.url, exc))
        except Exception as exc:
            if not self.ready.done():
                self.ready.set_exception(exc)
        finally:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except BaseException as exc:  # noqa: BLE001
                    # Teardown errors are secondary to the outcome already
                    # recorded in ``ready``; never let a stray CancelledError or
                    # exception group from the transport's task-group exit leak
                    # out. Genuine process-level signals still propagate.
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    logger.debug(
                        "MCP server %r: suppressed teardown error: %r", self.name, exc
                    )
            if not self.ready.done():
                self.ready.set_exception(
                    McpServerConnectionError(
                        self.name,
                        self.server.url,
                        RuntimeError("worker ended without resolving ready"),
                    )
                )


async def _drain_worker_task(worker: _ServerWorker) -> None:
    """Await an already-cancelled worker task to completion.

    The worker's own ``CancelledError`` (from our cancel) is swallowed, but a
    genuine cancellation of the *parent* task is allowed to propagate. The two
    are distinguished reliably because worker isolation guarantees the parent's
    ``cancelling()`` count is only ever incremented by an external cancel --
    transport-internal cancels stay confined to the worker task.
    """
    if worker.task is None:
        return
    try:
        await asyncio.shield(worker.task)
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling() > 0:
            # The parent itself is being cancelled; let the shutdown propagate.
            raise
        # Otherwise this is the worker's own cancel completing -- swallow it.
    except Exception as exc:  # noqa: BLE001
        logger.debug("MCP worker %r teardown error: %r", worker.name, exc)


async def _await_worker_teardown(worker: _ServerWorker, *, force: bool) -> None:
    """Tear down one worker that did NOT become a live session.

    Args:
        worker: The worker to stop (failed, timed out, or being cancelled).
        force: When True, cancel the worker task immediately (the caller is
            being cancelled); otherwise give it a short grace period to finish
            on its own before cancelling. In both cases a genuine external
            cancellation of the parent is re-raised, never swallowed.
    """
    if worker.task is None:
        return
    worker.shutdown()
    if force:
        worker.task.cancel()
        await _drain_worker_task(worker)
        return
    try:
        await asyncio.wait_for(asyncio.shield(worker.task), _TEARDOWN_GRACE_S)
    except TimeoutError:
        # Grace expired without the worker finishing; force it down.
        worker.task.cancel()
        await _drain_worker_task(worker)
    except asyncio.CancelledError:
        # External cancellation of the parent during the grace wait: bring the
        # worker down, then re-raise so the shutdown propagates.
        worker.task.cancel()
        await _drain_worker_task(worker)
        raise


async def _teardown_workers(workers: list[_ServerWorker], *, force: bool) -> None:
    """Tear down a list of workers concurrently.

    Each worker gets its own grace window, so total teardown time is bounded by
    ``_TEARDOWN_GRACE_S`` rather than ``N * _TEARDOWN_GRACE_S``. Per-worker
    non-cancel exceptions are logged and swallowed so one slow/bad teardown
    cannot abort the rest or leak the remaining sessions.
    """
    for w in workers:
        w.shutdown()
    results = await asyncio.gather(
        *(_await_worker_teardown(w, force=force) for w in workers if w.task is not None),
        return_exceptions=True,
    )
    for w, result in zip(workers, results, strict=False):
        if isinstance(result, BaseException) and not isinstance(
            result, (asyncio.CancelledError, TimeoutError)
        ):
            logger.debug("MCP worker %r teardown error: %r", w.name, result)


class MCPToolkit:
    """Async-context-managed MCP toolkit that owns client session lifetimes.

    Each configured MCP server is connected inside its own worker task (see
    :class:`_ServerWorker`), so a single unreachable server is skipped while the
    remaining servers (and the core tools) still come up. This implements the
    Deep Agents contract: "A single failing server no longer aborts startup."
    Servers connect concurrently, so startup latency is bounded by
    ``mcp_connect_timeout`` rather than ``N * mcp_connect_timeout``. Live
    sessions are kept alive for the lifetime of the context and torn down
    concurrently on exit.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the toolkit with optional settings.

        Args:
            settings: Optional settings instance; defaults to cached settings.
        """
        self.settings = settings or get_settings()
        self._tools: list[BaseTool] = []
        self._workers: list[_ServerWorker] = []

    async def __aenter__(self) -> MCPToolkit:
        """Open all configured MCP server sessions and discover their tools."""
        config = await load_mcp_config(self.settings.mcp_config_path)
        timeout = float(self.settings.mcp_connect_timeout)

        # Start every worker up front so connects run concurrently; total
        # startup is bounded by `timeout`, not N * timeout.
        pending: list[tuple[_ServerWorker, asyncio.Task[Any]]] = []
        try:
            for name, server in config.mcp_servers.items():
                if server.transport not in {"streamable_http", "http"}:
                    logger.warning(
                        "Skipping MCP server %r: unsupported transport %r",
                        name,
                        server.transport,
                    )
                    continue
                worker = _ServerWorker(name, server)
                worker.task = asyncio.create_task(
                    worker.run(), name=f"mcp-connect-{name}"
                )
                ready_task = asyncio.ensure_future(
                    asyncio.wait_for(worker.ready, timeout)
                )
                pending.append((worker, ready_task))

            results = await asyncio.gather(
                *(rt for _, rt in pending), return_exceptions=True
            )

            for (worker, _rt), result in zip(pending, results, strict=True):
                if isinstance(result, TimeoutError):
                    logger.warning(
                        "MCP server %r at %s did not respond within %ss; skipping.",
                        worker.name,
                        worker.server.url,
                        timeout,
                    )
                    await _await_worker_teardown(worker, force=False)
                elif isinstance(result, asyncio.CancelledError):
                    logger.warning(
                        "MCP server %r at %s connect was cancelled; skipping.",
                        worker.name,
                        worker.server.url,
                    )
                    await _await_worker_teardown(worker, force=True)
                elif isinstance(result, McpServerConnectionError):
                    # Transport-internal cancel surfaced as a per-server
                    # failure; log the structured cause for observability.
                    logger.warning(
                        "MCP server %r at %s unavailable (%s); skipping.",
                        result.name,
                        result.url,
                        result.__cause__ or result,
                    )
                    await _await_worker_teardown(worker, force=False)
                elif isinstance(result, Exception):  # noqa: BLE001
                    logger.warning(
                        "MCP server %r at %s failed to initialize (%s); skipping.",
                        worker.name,
                        worker.server.url,
                        result,
                    )
                    await _await_worker_teardown(worker, force=False)
                else:
                    self._tools.extend(result)
                    self._workers.append(worker)
        except BaseException:
            # External cancellation (or unexpected error) during connect: tear
            # down every worker started so far, then propagate.
            await _teardown_workers([w for w, _ in pending], force=True)
            raise
        return self

    def get_tools(self) -> list[BaseTool]:
        """Return the LangChain tools discovered from all MCP servers."""
        return self._tools

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close all MCP sessions and HTTP clients (best-effort)."""
        await self._shutdown_all()

    async def _shutdown_all(self) -> None:
        """Tear down every live worker session concurrently, best-effort."""
        workers = self._workers
        self._workers = []
        self._tools = []
        await _teardown_workers(workers, force=False)


def _wrap_mcp_tool(
    session: ClientSession,
    tool_info: Any,
) -> BaseTool:
    """Wrap an MCP tool into a LangChain BaseTool.

    Args:
        session: Active MCP client session.
        tool_info: Tool metadata returned by the MCP server.

    Returns:
        LangChain-compatible tool.
    """

    class _MCPTool(BaseTool):
        name: str = tool_info.name
        description: str = tool_info.description or ""
        args_schema: type[BaseModel] = _build_args_schema(tool_info.name, tool_info.inputSchema)

        async def _arun(self, **kwargs: Any) -> Any:
            result = await session.call_tool(self.name, kwargs)
            return result

        def _run(self, **kwargs: Any) -> Any:
            raise NotImplementedError("MCP tools only support async execution.")

    return _MCPTool()


def _sanitize_class_name(name: str) -> str:
    """Convert an MCP tool name into a valid Python identifier.

    Args:
        name: Raw tool name (may contain hyphens, dots, etc.).

    Returns:
        A sanitized, capitalized identifier safe for a Pydantic model class name.
    """
    cleaned = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned[:60]


def _build_args_schema(name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """Build a Pydantic model from an MCP JSON schema.

    Args:
        name: Tool name used for the model class name.
        schema: JSON schema describing tool inputs.

    Returns:
        Pydantic model class for tool arguments.
    """
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, tuple[type[Any], Any]] = {}
    for prop_name, prop_schema in properties.items():
        prop_type = _json_schema_type_to_python(prop_schema)
        default = ... if prop_name in required else None
        fields[prop_name] = (prop_type | None if default is None else prop_type, default)

    return create_model(f"{_sanitize_class_name(name)}Input", **fields)


def _json_schema_type_to_python(prop_schema: dict[str, Any]) -> type[Any]:
    """Map a simple JSON schema property to a Python type.

    Args:
        prop_schema: JSON schema property.

    Returns:
        Python type annotation.
    """
    type_map: dict[str, type[Any]] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    json_type = prop_schema.get("type", "string")
    if isinstance(json_type, list):
        json_type = json_type[0]
    return type_map.get(json_type, str)
