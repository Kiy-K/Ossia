"""Deep Agents middleware: retry, revision-loop cap, and dynamic prompt injection."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import dynamic_prompt
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage
from langgraph.config import get_config

from core.context import OssiaContext

logger = logging.getLogger(__name__)

# Tool names that perform external I/O and should be retried on failure.
_EXTERNAL_TOOLS: frozenset[str] = frozenset(
    {
        "search_knowledge_base",
        "search_codebase",
        "send_response",
        "fetch_issue",
    }
)


class RetryToolMiddleware(AgentMiddleware):
    """Retry external tool calls with exponential backoff.

    Implements the required RetryPolicy semantics (3 attempts, exponential
    backoff) for tools that perform external I/O, since Deep Agents does not
    expose per-node retry configuration.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        initial_interval: float = 1.0,
        backoff_factor: float = 2.0,
        jitter: bool = True,
        external_tools: frozenset[str] = _EXTERNAL_TOOLS,
    ) -> None:
        """Configure the retry policy.

        Args:
            max_attempts: Maximum number of attempts per tool call.
            initial_interval: Base delay between attempts in seconds.
            backoff_factor: Multiplier applied to the delay after each failure.
            jitter: When True, add a small random jitter to the delay.
            external_tools: Set of tool names that should be retried.
        """
        self.max_attempts = max_attempts
        self.initial_interval = initial_interval
        self.backoff_factor = backoff_factor
        self.jitter = jitter
        self.external_tools = external_tools

    def _wait_seconds(self, delay: float) -> float:
        """Return the delay before the next attempt, with optional jitter.

        Args:
            delay: Current base delay.

        Returns:
            Seconds to wait (always >= delay, never zero due to jitter misuse).
        """
        jitter_factor = (asyncio.get_running_loop().time() % 1) if self.jitter else 0.0
        return delay * (1.0 + jitter_factor)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Retry the wrapped tool call on exception.

        Args:
            request: Tool call request with the call dict and tool.
            handler: Async callable executing the tool.

        Returns:
            ToolMessage or Command produced by the tool.
        """
        tool_name = request.tool_call.get("name") if isinstance(request.tool_call, dict) else None
        if tool_name not in self.external_tools:
            return await handler(request)

        delay = self.initial_interval
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await handler(request)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.max_attempts:
                    break
                wait = self._wait_seconds(delay)
                logger.warning(
                    "Tool %s failed on attempt %d/%d: %s. Retrying in %.2fs",
                    tool_name,
                    attempt,
                    self.max_attempts,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
                delay *= self.backoff_factor

        assert last_exc is not None
        raise last_exc


class RevisionLoopCapMiddleware(AgentMiddleware):
    """Hard-cap the number of response revision loops.

    Counts ``grade_response`` invocations within a single agent run. After the
    configured cap is reached, the grade is short-circuited to force the agent
    to finalize via ``send_response`` instead of looping forever. The per-thread
    counter is reset at run start and reclaimed at run end to avoid unbounded
    growth in long-running servers.
    """

    def __init__(self, max_loops: int = 3) -> None:
        """Configure the revision cap.

        Args:
            max_loops: Maximum number of revision loops before forcing finalization.
        """
        self.max_loops = max_loops
        self._counts: dict[str, int] = {}

    def _thread_id(self) -> str:
        """Return the current thread id from the LangGraph config."""
        try:
            config = get_config()
            return str(config.get("configurable", {}).get("thread_id", "default"))
        except Exception:  # noqa: BLE001
            return "default"

    def _reset(self) -> None:
        """Reset the revision counter for the current thread at run start."""
        self._counts[self._thread_id()] = 0

    def _cleanup(self) -> None:
        """Reclaim the revision counter for the current thread at run end."""
        self._counts.pop(self._thread_id(), None)

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reset the revision counter at the start of each agent run."""
        self._reset()
        return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reset the revision counter at the start of each agent run (async)."""
        self._reset()
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reclaim the revision counter at the end of each agent run."""
        self._cleanup()
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reclaim the revision counter at the end of each agent run (async)."""
        self._cleanup()
        return None

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Force finalization once the revision cap is exceeded.

        Args:
            request: Tool call request.
            handler: Async callable executing the tool.

        Returns:
            ToolMessage from the tool, or a forced-finalize message.
        """
        tool_name = request.tool_call.get("name") if isinstance(request.tool_call, dict) else None
        if tool_name != "grade_response":
            return await handler(request)

        tid = self._thread_id()
        count = self._counts.get(tid, 0) + 1
        self._counts[tid] = count

        if count > self.max_loops:
            tool_call_id = request.tool_call.get("id", "") if isinstance(request.tool_call, dict) else ""
            logger.info(
                "Revision cap reached (%d > %d) for thread %s; forcing finalization.",
                count,
                self.max_loops,
                tid,
            )
            return ToolMessage(
                content=(
                    "Maximum revision attempts reached. Do not revise again. "
                    "Call send_response immediately with the latest draft."
                ),
                tool_call_id=tool_call_id,
                name="grade_response",
            )

        return await handler(request)


def make_caller_context_middleware(base_prompt: str) -> AgentMiddleware:
    """Create a dynamic-prompt middleware that injects runtime caller context.

    Uses the ``@dynamic_prompt`` pattern from Deep Agents context engineering:
    the decorated function receives a ``ModelRequest`` with ``runtime.context``
    (an ``OssiaContext`` instance) and returns the system prompt text with the
    caller identity appended.

    Args:
        base_prompt: The static system prompt content (loaded from
            ``system.md``) that the caller context is appended to.

    Returns:
        An ``AgentMiddleware`` that wraps model calls to inject
        caller-specific instructions.

    The resulting prompt looks like::

        <base_prompt>

        ## Current session
        - Caller ID: <caller_hash>
    """

    @dynamic_prompt
    def _inject_caller(request: ModelRequest[OssiaContext]) -> str:
        caller = request.runtime.context.caller if request.runtime.context else "unknown"
        return (
            f"{base_prompt}\n\n"
            f"## Current session\n"
            f"- Caller ID: {caller}\n"
        )

    return _inject_caller
