"""Deep Agents middleware: retry, circuit breaker, revision-loop cap, PII redaction, tool-call limit, model retry, model fallback, and dynamic prompt injection."""

from __future__ import annotations

import asyncio
import enum
import logging
import re
import time
from typing import Any

from langchain.agents.middleware import dynamic_prompt
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langgraph.config import get_config

from core.context import OssiaContext
from core.metrics import (
    CIRCUIT_BREAKER_BLOCKS,
    CIRCUIT_BREAKER_OPENS,
    CIRCUIT_BREAKER_PROBE_SUCCESSES,
    CIRCUIT_BREAKER_PROBES,
)

# Exceptions that indicate a transient model-call failure suitable for retry.
_TRANSIENT_MODEL_EXCEPTIONS: tuple[type[Exception], ...] = ()
"""Populated at module init with available SDK exception types."""


def _discover_transient_exceptions() -> tuple[type[Exception], ...]:
    """Discover transient exception types from installed SDKs.

    Returns a tuple of exception classes that represent transient failures
    (rate limits, timeouts, connection errors, server errors) suitable for
    retry or fallback. This is provider-agnostic: we look for common
    exception types from ``openai`` (used by ChatOpenAI), ``anthropic``,
    ``google``, etc., but only those that are actually installed.
    """
    exc_types: list[type[Exception]] = []
    try:
        import openai

        exc_types.extend(
            [
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ]
        )
    except (ImportError, AttributeError):
        pass
    try:
        import httpx

        exc_types.extend(
            [
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ]
        )
    except (ImportError, AttributeError):
        pass
    return tuple(exc_types)


_TRANSIENT_MODEL_EXCEPTIONS = _discover_transient_exceptions()


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


class _CircuitState(enum.Enum):
    """State of a circuit breaker for a single tool."""

    CLOSED = "closed"  # Normal operation — calls pass through
    OPEN = "open"  # Failing fast — calls blocked
    HALF_OPEN = "half_open"  # Probe — one call allowed to test recovery


class _BreakerEntry:
    """Per-tool, per-thread circuit breaker state."""

    __slots__ = ("state", "failure_count", "last_failure_time")

    def __init__(self) -> None:
        self.state: _CircuitState = _CircuitState.CLOSED
        self.failure_count: int = 0
        self.last_failure_time: float | None = None

    def record_failure(self) -> int:
        """Record a failure, transition to OPEN if threshold exceeded.

        Returns:
            Failure count after incrementing.
        """
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        return self.failure_count

    def record_success(self) -> None:
        """Record a success, resetting to CLOSED."""
        self.state = _CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None

    def should_probe(self, recovery_timeout: float) -> bool:
        """Return True if enough time has passed to try a probe call."""
        if self.last_failure_time is None:
            return True
        return (time.monotonic() - self.last_failure_time) >= recovery_timeout


# Tools that perform external I/O and should be protected by the
# circuit breaker (a superset of ``_EXTERNAL_TOOLS``).
_CIRCUIT_TOOLS: frozenset[str] = frozenset(
    {
        "internet_search",
        "qna_search",
        "fetch_url",
        "search_knowledge_base",
        "search_codebase",
        "fetch_issue",
        "create_pr",
        "run_tests",
    }
)


class CircuitBreakerMiddleware(AgentMiddleware):
    """Fail-fast circuit breaker for external tool calls.

    Wraps the ``RetryToolMiddleware`` on the outside: when an external service
    is repeatedly failing, this middleware opens the circuit so the retry
    middleware never even gets a chance to hammer it. After a recovery timeout
    a single probe is allowed; if it succeeds the circuit closes, if it fails
    the circuit stays open for another timeout window.

    State machine per tool (per thread):

        CLOSED --consecutive failures >= failure_threshold--> OPEN
        OPEN   --recovery_timeout elapsed--> HALF_OPEN
        HALF_OPEN --call succeeds--> CLOSED
        HALF_OPEN --call fails--> OPEN (back to full timeout)

    Non-external tools pass through unmodified.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        external_tools: frozenset[str] = _CIRCUIT_TOOLS,
    ) -> None:
        """Configure the circuit breaker policy.

        Args:
            failure_threshold: Consecutive failures before opening the circuit.
            recovery_timeout: Seconds to wait before attempting a probe.
            external_tools: Set of tool names covered by the breaker.
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.external_tools = external_tools
        # ``_breakers[thread_id][tool_name] -> _BreakerEntry``
        self._breakers: dict[str, dict[str, _BreakerEntry]] = {}

    def _thread_id(self) -> str:
        """Return the current thread id from the LangGraph config."""
        try:
            config = get_config()
            return str(config.get("configurable", {}).get("thread_id", "default"))
        except Exception:  # noqa: BLE001
            return "default"

    def _get_entry(self, thread_id: str, tool_name: str) -> _BreakerEntry:
        """Get or create the circuit breaker entry for a tool in a thread."""
        return self._breakers.setdefault(thread_id, {}).setdefault(tool_name, _BreakerEntry())

    def _reset(self) -> None:
        """Reset all breakers for the current thread at run start."""
        self._breakers.pop(self._thread_id(), None)

    def _cleanup(self) -> None:
        """Reclaim the breakers for the current thread at run end."""
        self._breakers.pop(self._thread_id(), None)

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reset breakers at the start of each agent run."""
        self._reset()
        return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reset breakers at the start of each agent run (async)."""
        self._reset()
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reclaim breakers at the end of each agent run."""
        self._cleanup()
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reclaim breakers at the end of each agent run (async)."""
        self._cleanup()
        return None

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Enforce the circuit breaker on the wrapped tool call.

        Args:
            request: Tool call request with the call dict and tool.
            handler: Async callable executing the tool.

        Returns:
            ToolMessage from the tool, or a circuit-open message.

        Raises:
            Exception: The underlying tool exception is re-raised to let
                the ``RetryToolMiddleware`` (outer) handle retries. The
                breaker records the failure before re-raising.
        """
        tool_name = request.tool_call.get("name") if isinstance(request.tool_call, dict) else None
        if tool_name not in self.external_tools:
            return await handler(request)

        tid = self._thread_id()
        entry = self._get_entry(tid, tool_name)

        # ── OPEN → maybe transition to HALF_OPEN ─────────────────────────
        if entry.state is _CircuitState.OPEN:
            if entry.should_probe(self.recovery_timeout):
                entry.state = _CircuitState.HALF_OPEN
                CIRCUIT_BREAKER_PROBES.labels(tool=tool_name).inc()
                logger.info(
                    "Circuit %s/%s is HALF_OPEN (recovery timeout elapsed); allowing probe.",
                    tid,
                    tool_name,
                )
            else:
                tool_call_id = (
                    request.tool_call.get("id", "") if isinstance(request.tool_call, dict) else ""
                )
                CIRCUIT_BREAKER_BLOCKS.labels(tool=tool_name).inc()
                logger.info(
                    "Circuit %s/%s is OPEN; blocking call (failure_count=%d).",
                    tid,
                    tool_name,
                    entry.failure_count,
                )
                return ToolMessage(
                    content=(
                        f"The service backing `{tool_name}` is currently unavailable. "
                        "The circuit breaker is open due to repeated failures. "
                        f"Skipping this call. Try again later or use an alternative approach."
                    ),
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )

        # ── Attempt the call (CLOSED or HALF_OPEN) ───────────────────────
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            entry.record_failure()
            if entry.failure_count >= self.failure_threshold:
                entry.state = _CircuitState.OPEN
                CIRCUIT_BREAKER_OPENS.labels(tool=tool_name).inc()
                logger.warning(
                    "Circuit %s/%s OPEN after %d consecutive failures: %s",
                    tid,
                    tool_name,
                    entry.failure_count,
                    exc,
                )
            else:
                logger.debug(
                    "Circuit %s/%s failure %d/%d: %s",
                    tid,
                    tool_name,
                    entry.failure_count,
                    self.failure_threshold,
                    exc,
                )
            raise

        # ── Success ──────────────────────────────────────────────────────
        if entry.state is _CircuitState.HALF_OPEN:
            CIRCUIT_BREAKER_PROBE_SUCCESSES.labels(tool=tool_name).inc()
            logger.info(
                "Circuit %s/%s CLOSED (probe succeeded).",
                tid,
                tool_name,
            )
        entry.record_success()
        return result


# ── Model Retry Middleware ───────────────────────────────────────────────────


class ModelRetryMiddleware(AgentMiddleware):
    """Retry model (LLM) calls with exponential backoff on transient failures.

    Unlike :class:`RetryToolMiddleware` which retries tool calls, this
    middleware wraps ``awrap_model_call`` to handle transient LLM provider
    errors (rate limits, timeouts, connection resets, server errors).
    These are not retried by the tool-level retry because they happen
    before any tool is invoked.

    Retries are limited to ``max_attempts`` (default 2) with exponential
    backoff. Only exceptions matching :data:`_TRANSIENT_MODEL_EXCEPTIONS`
    trigger a retry; non-transient errors (auth, bad request, etc.) are
    re-raised immediately.
    """

    def __init__(
        self,
        max_attempts: int = 2,
        initial_interval: float = 0.5,
        backoff_factor: float = 2.0,
    ) -> None:
        """Configure the model retry policy.

        Args:
            max_attempts: Maximum number of attempts per model call.
            initial_interval: Base delay between attempts in seconds.
            backoff_factor: Multiplier applied after each failure.
        """
        self.max_attempts = max_attempts
        self.initial_interval = initial_interval
        self.backoff_factor = backoff_factor

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        delay = self.initial_interval
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await handler(request)
            except _TRANSIENT_MODEL_EXCEPTIONS as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    break
                logger.warning(
                    "Model call failed on attempt %d/%d: %s. Retrying in %.2fs",
                    attempt,
                    self.max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= self.backoff_factor
        assert last_exc is not None
        raise last_exc


# ── Model Fallback Middleware ────────────────────────────────────────────────


class ModelFallbackMiddleware(AgentMiddleware):
    """Fall back to a secondary model when the primary model call fails.

    On the first transient failure, the middleware replaces
    ``request.model`` with ``fallback_model`` and retries the handler.
    If the fallback also fails, the exception is re-raised.

    The fallback model is a pre-configured ``BaseChatModel`` instance
    (typically a cheaper/simpler model from a different provider) that
    can serve requests when the primary provider is degraded.
    """

    def __init__(self, fallback_model: BaseChatModel) -> None:
        """Configure the fallback model.

        Args:
            fallback_model: A pre-configured ``BaseChatModel`` instance
                to use when the primary model call fails. This should
                be a different model (potentially from a different
                provider) to maximise availability.
        """
        self.fallback_model = fallback_model

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        try:
            return await handler(request)
        except _TRANSIENT_MODEL_EXCEPTIONS:
            original_model = request.model
            request.model = self.fallback_model
            logger.info(
                "Primary model %s failed; falling back to %s",
                getattr(original_model, "model", "unknown"),
                getattr(self.fallback_model, "model", "unknown"),
            )
            try:
                return await handler(request)
            except _TRANSIENT_MODEL_EXCEPTIONS:
                logger.warning(
                    "Fallback model %s also failed; collapsing to original model",
                    getattr(self.fallback_model, "model", "unknown"),
                )
                request.model = original_model
                raise


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
            tool_call_id = (
                request.tool_call.get("id", "") if isinstance(request.tool_call, dict) else ""
            )
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


def make_caller_context_middleware(base_prompt: str) -> AgentMiddleware[Any, Any, Any]:
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
        return f"{base_prompt}\n\n## Current session\n- Caller ID: {caller}\n"

    return _inject_caller


# ── PII Redaction Middleware ─────────────────────────────────────────────────

# Regex patterns for sensitive data that should be redacted from tool inputs.
# Each pattern is a compiled regex that matches the entire sensitive value so it
# can be replaced with a placeholder. Keep patterns narrow to avoid false
# positives on legitimate content.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # URLs containing credentials (user:pass@host) — must come before email
    # pattern so the email regex does not match the `user:pass@host` segment
    # and consume the `@` before the credential URL pattern sees it.
    (re.compile(r"\bhttps?://[^:@/\s]+:[^@/\s]+@"), "https://***REDACTED***@"),
    # API keys, tokens, secrets (standard formats like "api_key=sk-abc...")
    (
        re.compile(
            r"""(?ix)                    # case-insensitive + verbose
            (                               # capture group for replacement
                (?:api[_-]?key|apikey|secret|token|password|passwd|credential)
                \s*[:=]\s*
            )
            (\S{8,})                       # the secret value itself
            """
        ),
        r"\1***REDACTED***",
    ),
    # Bearer tokens in headers or inline ("Bearer eyJ..." or "token ghp_...")
    (
        re.compile(
            r"""(?ix)
            (                               # capture group
                (?:bearer|token)\s+
            )
            ([\w\-._~+/]{20,})             # base64/jwt/token value
            """
        ),
        r"\1***REDACTED***",
    ),
    # Email addresses
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.,-]+"), "***EMAIL***"),
    # US phone numbers (with optional country code and separators)
    (re.compile(r"\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}"), "***PHONE***"),
    # Social Security / SIN numbers (9 digits with separators)
    (re.compile(r"\b\d{3}[-]\d{2}[-]\d{4}\b"), "***SSN***"),
    # Private / internal IP addresses (RFC 1918, localhost)
    (
        re.compile(
            r"""(?x)
            \b
            (                               # capture the full IP
                127\.\d{1,3}\.\d{1,3}\.\d{1,3}
                |
                10\.\d{1,3}\.\d{1,3}\.\d{1,3}
                |
                172\.1[6-9]\.\d{1,3}\.\d{1,3}
                |
                192\.168\.\d{1,3}\.\d{1,3}
            )
            \b
            """
        ),
        "***IP***",
    ),
]


def _redact_pii(text: str) -> str:
    """Redact personally identifiable information and secrets from a string.

    Applies each PII pattern to the input text, replacing sensitive values
    with placeholder markers. The order of patterns matters: broader patterns
    (like API keys) are applied before narrower ones (like emails) to avoid
    double-redaction artifacts.

    Args:
        text: The input string to redact.

    Returns:
        The redacted string with sensitive values replaced.
    """
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_dict(value: Any, depth: int = 0) -> Any:
    """Recursively redact PII from strings in a nested dict/list structure.

    Stops at ``_MAX_REDACT_DEPTH`` to avoid infinite recursion on circular
    references. Numbers and booleans are passed through as-is.

    Args:
        value: The value to redact (dict, list, str, or scalar).
        depth: Current recursion depth (internal, starts at 0).

    Returns:
        The redacted value with all PII fields sanitized.
    """
    max_redact_depth = 20
    if depth > max_redact_depth:
        return value
    if isinstance(value, dict):
        return {k: _redact_dict(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_dict(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _redact_pii(value)
    return value


class PIIRedactionMiddleware(AgentMiddleware):
    """Redact sensitive data from tool call inputs before execution.

    Strips emails, API keys, phone numbers, SSNs, IP addresses, and
    credential-bearing URLs from tool arguments before they reach the
    LLM or appear in logs. The middleware intercepts ``awrap_tool_call``
    and applies regex-based redaction to string fields in the request
    before passing it to the handler.

    Redaction is applied to:
        - ``input`` dict values in the tool call request
        - Any string field that could contain user-provided data

    This is a defense-in-depth measure. The primary protection is that
    the system prompt instructs the agent not to expose secrets.
    """

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Redact PII from tool call inputs before executing the tool.

        Args:
            request: Tool call request with the call dict and tool.
            handler: Async callable executing the tool.

        Returns:
            The handler's result (unmodified after redaction).
        """
        if isinstance(request.tool_call, dict):
            inp = request.tool_call.get("args", request.tool_call.get("input", {}))
            if isinstance(inp, dict):
                request.tool_call["args"] = _redact_dict(inp)
        return await handler(request)


# ── Tool Call Limit Middleware ───────────────────────────────────────────────


class ToolCallLimitMiddleware(AgentMiddleware):
    """Hard-cap the total number of tool calls per agent run.

    Counts every tool invocation (except ``grade_response`` which is
    already capped by ``RevisionLoopCapMiddleware``) within a single
    agent run. Once the configured limit is reached, subsequent tool
    calls receive a ``ToolMessage`` instructing the agent to finalize
    and respond. This prevents runaway agents that spin on external
    I/O (web searches, code searches) and controls LLM token costs.

    The per-thread counter is reset at run start via ``abefore_agent``
    and reclaimed at run end via ``aafter_agent``.
    """

    def __init__(self, max_calls: int = 25) -> None:
        """Configure the tool call limit.

        Args:
            max_calls: Maximum total tool calls per run before forcing
                finalization. Default 25. Subtract the expected number
                of internal middleware calls (grade_response, send_response)
                when tuning.
        """
        self.max_calls = max_calls
        self._counts: dict[str, int] = {}

    def _thread_id(self) -> str:
        """Return the current thread id from the LangGraph config."""
        try:
            config = get_config()
            return str(config.get("configurable", {}).get("thread_id", "default"))
        except Exception:  # noqa: BLE001
            return "default"

    def _reset(self) -> None:
        """Reset the counter for the current thread at run start."""
        self._counts[self._thread_id()] = 0

    def _cleanup(self) -> None:
        """Reclaim the counter for the current thread at run end."""
        self._counts.pop(self._thread_id(), None)

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reset the counter at the start of each agent run."""
        self._reset()
        return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reset the counter at the start of each agent run (async)."""
        self._reset()
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reclaim the counter at the end of each agent run."""
        self._cleanup()
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """Reclaim the counter at the end of each agent run (async)."""
        self._cleanup()
        return None

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """Cap total tool calls once the limit is exceeded.

        Args:
            request: Tool call request.
            handler: Async callable executing the tool.

        Returns:
            ToolMessage from the tool, or a capped message.
        """
        tool_name = request.tool_call.get("name") if isinstance(request.tool_call, dict) else None
        # Don't count grade_response or send_response — they are capped
        # by RevisionLoopCapMiddleware and are not external I/O.
        if tool_name in ("grade_response", "send_response"):
            return await handler(request)

        tid = self._thread_id()
        count = self._counts.get(tid, 0) + 1
        self._counts[tid] = count

        if count > self.max_calls:
            tool_call_id = (
                request.tool_call.get("id", "") if isinstance(request.tool_call, dict) else ""
            )
            logger.info(
                "Tool call limit reached (%d > %d) for thread %s; blocking call to %s.",
                count,
                self.max_calls,
                tid,
                tool_name,
            )
            return ToolMessage(
                content=(
                    "Maximum tool call limit reached. You have exceeded the "
                    f"allowed {self.max_calls} tool calls for this run. "
                    "Do not attempt further tool calls. Summarize what you "
                    "have so far and call send_response to deliver your answer."
                ),
                tool_call_id=tool_call_id,
                name=tool_name or "unknown",
            )

        return await handler(request)
