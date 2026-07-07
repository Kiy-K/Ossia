"""Adapters bridging community middlewares into the DeepAgents / LangChain ecosystem.

- :class:`EagerToolAdapter` — wraps a LangChain ``BaseTool`` into the
  ``eager_tools.Tool`` Protocol so eager dispatch works without tool changes.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


# ── Eager-tools adapter ──────────────────────────────────────────────────────


# Tools the eager dispatch path must NEVER fire eagerly — they have side
# effects (outbound messages, PR creation, destructive mutations) or
# depend on strict ordering (send_response after grading).
_EAGER_DENY: frozenset[str] = frozenset(
    {
        "send_response",
        "create_pr",
        "grade_response",
        "run_bugfix_pipeline",
        "run_audit_pipeline",
        "run_refactor_pipeline",
        "start_async_task",
        "cancel_async_task",
        "evaluate",
    }
)


class EagerToolAdapter:
    """Wrap a LangChain ``BaseTool`` to satisfy the ``eager_tools.Tool`` Protocol.

    The eager-tools middleware dispatches tools the moment their
    ``tool_call_chunk`` seals (i.e. the ``tool_use`` JSON block is
    complete in the streaming response). This adapter translates the
    LangChain ``ainvoke(dict)`` call signature into ``__call__(dict)``
    which is what the ``Tool`` Protocol expects.

    Idempotent tools (read_file, search_codebase, etc.) benefit from
    eager dispatch because they can run while the model still streams
    subsequent blocks. Non-idempotent tools (send_response, create_pr)
    are marked ``idempotent=False`` and skipped by the eager path —
    they only execute in the normal agent tool step.

    The deny-list :data:`_EAGER_DENY` covers tools whose side effects
    or ordering constraints make eager dispatch unsafe regardless of
    idempotency.
    """

    def __init__(self, tool: BaseTool) -> None:
        self._tool = tool

    @property
    def name(self) -> str:
        """Tool name (required by the ``Tool`` Protocol)."""
        return self._tool.name

    @property
    def idempotent(self) -> bool:
        """Return True when this tool is safe for eager dispatch.

        Checks the deny-list first, then falls back to the tool's
        own ``idempotent`` attribute (defaults to True for read-only
        tools, False for write tools).
        """
        if self._tool.name in _EAGER_DENY:
            return False
        return getattr(self._tool, "idempotent", True)

    async def __call__(self, arguments: dict[str, Any]) -> Any:
        """Invoke the underlying LangChain tool with the given arguments.

        Args:
            arguments: Tool arguments as a ``dict`` (matches the
                ``ToolCall.arguments`` shape from the streaming block).

        Returns:
            The tool's result (string or dict).
        """
        try:
            return await self._tool.ainvoke(arguments)
        except Exception:
            logger.exception("Eager tool %s failed; falling back to normal tool step", self.name)
            raise


def eager_tool_map(tools: list[BaseTool]) -> dict[str, EagerToolAdapter]:
    """Build the ``{name: Tool}`` dict that ``eager_middleware`` expects.

    Every tool in the list is wrapped in an :class:`EagerToolAdapter`.
    Non-idempotent tools are still included in the map — the eager
    middleware skips them via ``Tool.idempotent`` and the agent's normal
    tool step picks them up.

    Args:
        tools: The agent's tool list (including MCP and plugin tools).

    Returns:
        A dict suitable for ``eager_middleware(tools=...)``.
    """
    return {t.name: EagerToolAdapter(t) for t in tools}
