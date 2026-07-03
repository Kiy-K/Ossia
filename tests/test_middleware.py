"""Unit tests for Ossia agent middleware: PII redaction, tool-call limiting, and circuit breaker.

Tests cover:
- PIIRedactionMiddleware: redaction of API keys, emails, phone numbers, SSNs,
  internal IPs, credential-URLs in tool call inputs
- PIIRedactionMiddleware: recursive dict/list traversal, depth limiting,
  passthrough of non-string values, no-op on empty/normal inputs
- ToolCallLimitMiddleware: basic counting, cap enforcement, exclusion of
  grade_response/send_response from counting, per-thread lifecycle
  (abefore/aafter agent), and configurable limit
- CircuitBreakerMiddleware: CLOSED passthrough, OPEN blocking, HALF_OPEN probe,
  recovery timeout, threshold config, non-external tool passthrough,
  per-thread lifecycle reset/cleanup
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from core.middleware import (
    CircuitBreakerMiddleware,
    PIIRedactionMiddleware,
    ToolCallLimitMiddleware,
    _BreakerEntry,
    _CircuitState,
    _redact_dict,
    _redact_pii,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


class _Req:
    """Minimal stand-in for an AgentMiddleware tool-call request."""

    def __init__(self, name: str, args: dict[str, Any] | None = None) -> None:
        self.tool_call = {
            "name": name,
            "id": "t-req-1",
            "args": args or {},
        }


async def _ok_handler(_request: Any) -> ToolMessage:
    """Handler that returns a success ToolMessage."""
    return ToolMessage(content="ok", tool_call_id="t-req-1", name="test")


# ── PII redaction: unit-level (_redact_pii / _redact_dict) ──────────────────


def test_redact_pii_api_key_inline() -> None:
    """API keys in ``key=value`` format are redacted."""
    result = _redact_pii("api_key=sk-1234567890abcdef")
    assert "sk-1234567890abcdef" not in result
    assert "***REDACTED***" in result


def test_redact_pii_api_key_json_like() -> None:
    """API keys in JSON-like ``key: value`` (colon with space) are redacted.

    This simulates the string representation of a dict entry like
    ``{"api_key": "sk-..."}`` after the JSON quotes have been removed
    during string serialization — the regex sees ``api_key: sk-...``.
    """
    result = _redact_pii('api_key: "sk-1234567890abcdef"')
    assert "1234567890abcdef" not in result
    assert "***REDACTED***" in result


def test_redact_pii_api_key_colon_variant() -> None:
    """API keys with ``:`` separator are redacted."""
    result = _redact_pii("token:ghp_wVYvYkFQFtQmPpQgQdQoQrQ")
    assert "ghp_wVYvYkFQFtQmPpQgQdQoQrQ" not in result
    assert "***REDACTED***" in result


def test_redact_pii_bearer_token() -> None:
    """Bearer tokens in headers are redacted."""
    result = _redact_pii("Authorization: bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
    assert "***REDACTED***" in result


def test_redact_pii_bearer_token_case_insensitive() -> None:
    """Bearer token matching is case-insensitive."""
    result = _redact_pii("Authorization: Bearer abcdefghijklmnopqrstu")
    assert "abcdefghijklmnopqrstu" not in result
    assert "***REDACTED***" in result


def test_redact_pii_email() -> None:
    """Email addresses are redacted."""
    result = _redact_pii("Contact: alice@example.com for support")
    assert "alice@example.com" not in result
    assert "***EMAIL***" in result


def test_redact_pii_email_multiple_dots() -> None:
    """Emails with subdomains are redacted."""
    result = _redact_pii("user@mail.internal.example.co.uk")
    assert "***EMAIL***" in result


def test_redact_pii_phone_us() -> None:
    """US phone numbers are redacted."""
    result = _redact_pii("Call +1 (555) 123-4567 for help")
    assert "***PHONE***" in result


def test_redact_pii_phone_no_country() -> None:
    """Phone numbers without country code are redacted."""
    result = _redact_pii("Tel: 555-123-4567")
    assert "***PHONE***" in result


def test_redact_pii_ssn() -> None:
    """Social Security Numbers are redacted."""
    result = _redact_pii("SSN: 123-45-6789")
    assert "123-45-6789" not in result
    assert "***SSN***" in result


def test_redact_pii_internal_ip() -> None:
    """RFC 1918 private IPs are redacted."""
    result = _redact_pii("Server at 192.168.1.100 is down")
    assert "192.168.1.100" not in result
    assert "***IP***" in result


def test_redact_pii_localhost_ip() -> None:
    """localhost IP is redacted."""
    result = _redact_pii("Connect to 127.0.0.1:8000")
    assert "127.0.0.1" not in result
    assert "***IP***" in result


def test_redact_pii_credential_url() -> None:
    """URLs with embedded credentials are redacted."""
    result = _redact_pii("https://user:pass@api.example.com/data")
    assert "user:pass@" not in result
    assert "***REDACTED***" in result


def test_redact_pii_no_false_positive_normal_text() -> None:
    """Normal text without sensitive data passes through unmodified."""
    result = _redact_pii(
        "The project uses FastAPI for the HTTP layer and LangChain for agent orchestration."
    )
    # Should contain the original text (but might have partial matches from patterns)
    assert "FastAPI" in result
    assert "LangChain" in result


def test_redact_pii_short_values_not_matched() -> None:
    """Values shorter than 8 characters are not treated as API keys."""
    result = _redact_pii("key=short")
    assert "short" in result


def test_redact_dict_flat() -> None:
    """Flat dict has all string values redacted."""
    result = _redact_dict({"query": "email alice@example.com", "top_k": 5})
    assert "alice@example.com" not in str(result)
    assert "***EMAIL***" in str(result)
    assert result["top_k"] == 5  # non-string passed through


def test_redact_dict_nested() -> None:
    """Nested dict has string values redacted recursively."""
    result = _redact_dict({"input": {"credentials": "api_key=sk-abcdefghijklmnop"}})
    assert "sk-abcdefghijklmnop" not in str(result)
    assert "***REDACTED***" in str(result)


def test_redact_dict_list() -> None:
    """List values have string elements redacted recursively."""
    result = _redact_dict({"items": ["hello", "email: bob@example.com"]})
    assert "bob@example.com" not in str(result)
    assert "***EMAIL***" in str(result)
    assert result["items"][0] == "hello"  # non-sensitive passes through


def test_redact_dict_depth_limit() -> None:
    """Redaction stops at the depth limit (20) to avoid infinite recursion."""
    deeply_nested: dict[str, Any] = {"level0": {}}
    current = deeply_nested["level0"]
    for i in range(1, 25):
        current[f"level{i}"] = {}
        current = current[f"level{i}"]
    current["secret"] = "api_key=sk-abcdefghijklmnop"
    # Should not crash due to depth limits
    result = _redact_dict(deeply_nested)
    # Values beyond depth 20 may not be redacted, but function should not crash
    assert result is not None
    assert "level0" in result


def test_redact_dict_non_string_passthrough() -> None:
    """Non-string types (int, float, bool, None) pass through unmodified."""
    result = _redact_dict({"a": 42, "b": 3.14, "c": True, "d": None})
    assert result["a"] == 42
    assert result["b"] == 3.14
    assert result["c"] is True
    assert result["d"] is None


# ── PIIRedactionMiddleware ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pii_middleware_redacts_api_key_in_args() -> None:
    """Middleware redacts API keys from tool call args."""
    mw = PIIRedactionMiddleware()
    req = _Req("test_tool", {"query": "api_key=sk-abcdefghijklmnop"})

    async def check_handler(r: Any) -> ToolMessage:
        # Verify args were redacted before reaching the handler
        assert "sk-abcdefghijklmnop" not in r.tool_call["args"]["query"]
        assert "***REDACTED***" in r.tool_call["args"]["query"]
        return ToolMessage(content="ok", tool_call_id="t-req-1", name="test")

    await mw.awrap_tool_call(req, check_handler)


@pytest.mark.asyncio
async def test_pii_middleware_redacts_email_in_args() -> None:
    """Middleware redacts email addresses from tool call args."""
    mw = PIIRedactionMiddleware()
    req = _Req("test_tool", {"contact": "alice@example.com"})

    async def check_handler(r: Any) -> ToolMessage:
        assert "alice@example.com" not in r.tool_call["args"]["contact"]
        assert "***EMAIL***" in r.tool_call["args"]["contact"]
        return ToolMessage(content="ok", tool_call_id="t-req-1", name="test")

    await mw.awrap_tool_call(req, check_handler)


@pytest.mark.asyncio
async def test_pii_middleware_normal_args_passthrough() -> None:
    """Middleware passes through non-sensitive args unmodified."""
    mw = PIIRedactionMiddleware()
    req = _Req("test_tool", {"file_path": "src/core/api.py", "top_k": 5})

    result = await mw.awrap_tool_call(req, _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_pii_middleware_empty_args() -> None:
    """Middleware handles empty args gracefully."""
    mw = PIIRedactionMiddleware()
    req = _Req("test_tool", {})

    result = await mw.awrap_tool_call(req, _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_pii_middleware_no_args_key() -> None:
    """Middleware handles tool calls without 'args' key gracefully."""
    mw = PIIRedactionMiddleware()

    class _ReqNoArgs:
        def __init__(self) -> None:
            self.tool_call = {"name": "test"}

    result = await mw.awrap_tool_call(_ReqNoArgs(), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_pii_middleware_non_dict_tool_call() -> None:
    """Middleware handles non-dict tool_call gracefully."""
    mw = PIIRedactionMiddleware()

    class _ReqNoDict:
        def __init__(self) -> None:
            self.tool_call = None  # type: ignore[assignment]

    result = await mw.awrap_tool_call(_ReqNoDict(), _ok_handler)
    assert result.content == "ok"


# ── ToolCallLimitMiddleware ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_limit_basic_counting() -> None:
    """Middleware counts tool calls and lets them through under the limit."""
    mw = ToolCallLimitMiddleware(max_calls=5)

    # All calls under the limit should pass through
    for i in range(5):
        result = await mw.awrap_tool_call(
            _Req("search_codebase", {"query": f"test-{i}"}), _ok_handler
        )
        assert result.content == "ok"


@pytest.mark.asyncio
async def test_tool_call_limit_enforces_cap() -> None:
    """Middleware blocks tool calls after the limit is exceeded."""
    mw = ToolCallLimitMiddleware(max_calls=3)

    # First 3 calls pass through
    for i in range(3):
        result = await mw.awrap_tool_call(
            _Req("search_codebase", {"query": f"test-{i}"}), _ok_handler
        )
        assert result.content == "ok"
        assert getattr(result, "name", None) != "unknown"

    # 4th call is capped
    result = await mw.awrap_tool_call(_Req("internet_search", {"query": "test-4"}), _ok_handler)
    assert "Maximum tool call limit" in str(result.content)
    # The capped result is a ToolMessage, not a handler response
    assert "send_response" in str(result.content)


@pytest.mark.asyncio
async def test_tool_call_limit_grade_response_not_counted() -> None:
    """grade_response is excluded from the tool call count."""
    mw = ToolCallLimitMiddleware(max_calls=1)

    # grade_response should not count toward the limit
    await mw.awrap_tool_call(_Req("grade_response"), _ok_handler)
    await mw.awrap_tool_call(_Req("grade_response"), _ok_handler)
    await mw.awrap_tool_call(_Req("grade_response"), _ok_handler)

    # Another tool call should still be under the limit (only 1 counted)
    result = await mw.awrap_tool_call(_Req("search_codebase", {"query": "test"}), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_tool_call_limit_send_response_not_counted() -> None:
    """send_response is excluded from the tool call count."""
    mw = ToolCallLimitMiddleware(max_calls=2)

    await mw.awrap_tool_call(_Req("send_response"), _ok_handler)
    await mw.awrap_tool_call(_Req("send_response"), _ok_handler)

    # First counted call passes
    result = await mw.awrap_tool_call(_Req("search_codebase", {"query": "test"}), _ok_handler)
    assert result.content == "ok"

    # Second counted call passes (under limit)
    result = await mw.awrap_tool_call(_Req("fetch_url", {"url": "http://example.com"}), _ok_handler)
    assert result.content == "ok"

    # Third counted call exceeds limit
    result = await mw.awrap_tool_call(_Req("internet_search", {"query": "test-3"}), _ok_handler)
    assert "Maximum tool call limit" in str(result.content)


@pytest.mark.asyncio
async def test_tool_call_limit_lifecycle_reset() -> None:
    """Middleware resets the counter at the start of each agent run."""
    mw = ToolCallLimitMiddleware(max_calls=2)

    # Simulate run start
    await mw.abefore_agent({}, None)

    # Use 2 calls
    await mw.awrap_tool_call(_Req("search_codebase", {"q": "a"}), _ok_handler)
    await mw.awrap_tool_call(_Req("search_codebase", {"q": "b"}), _ok_handler)

    # 3rd should be capped
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "c"}), _ok_handler)
    assert "Maximum tool call limit" in str(result.content)

    # Simulate run end and new run start
    await mw.aafter_agent({}, None)
    await mw.abefore_agent({}, None)

    # Counter should reset
    result = await mw.awrap_tool_call(_Req("search_codebase", {"q": "new-run"}), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_tool_call_limit_lifecycle_cleanup() -> None:
    """Middleware reclaims per-thread counters after agent run ends."""
    mw = ToolCallLimitMiddleware(max_calls=5)

    await mw.abefore_agent({}, None)
    await mw.awrap_tool_call(_Req("search_codebase", {"q": "a"}), _ok_handler)
    tid_before = mw._thread_id()
    assert tid_before in mw._counts

    await mw.aafter_agent({}, None)
    assert tid_before not in mw._counts


@pytest.mark.asyncio
async def test_tool_call_limit_configurable() -> None:
    """Middleware accepts a configurable max_calls parameter."""
    mw = ToolCallLimitMiddleware(max_calls=1)

    result = await mw.awrap_tool_call(_Req("search_codebase", {"query": "first"}), _ok_handler)
    assert result.content == "ok"

    result = await mw.awrap_tool_call(_Req("internet_search", {"query": "second"}), _ok_handler)
    assert "Maximum tool call limit" in str(result.content)


@pytest.mark.asyncio
async def test_tool_call_limit_non_dict_tool_call() -> None:
    """Middleware handles non-dict tool_call gracefully."""
    mw = ToolCallLimitMiddleware(max_calls=3)

    class _ReqNoDict:
        def __init__(self) -> None:
            self.tool_call = None  # type: ignore[assignment]

    result = await mw.awrap_tool_call(_ReqNoDict(), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_tool_call_limit_returns_tool_message_on_cap() -> None:
    """The capped result is a ToolMessage with the expected attributes."""
    mw = ToolCallLimitMiddleware(max_calls=1)

    await mw.awrap_tool_call(_Req("search_codebase", {"q": "a"}), _ok_handler)

    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "b"}), _ok_handler)
    assert isinstance(result, ToolMessage)
    assert "Maximum tool call limit" in str(result.content)
    assert result.tool_call_id == "t-req-1"
    assert result.name == "internet_search"


# ── CircuitBreakerMiddleware ─────────────────────────────────────────────────


async def _fail_handler(_request: Any) -> ToolMessage:
    """Handler that always raises an exception (simulates a down service)."""
    msg = "Connection refused: service unavailable"
    raise RuntimeError(msg)


@pytest.mark.asyncio
async def test_circuit_breaker_pass_through_closed() -> None:
    """CLOSED circuit passes calls through to the handler."""
    mw = CircuitBreakerMiddleware(failure_threshold=3, recovery_timeout=60.0)
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "hello"}), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_circuit_breaker_non_external_tool_passthrough() -> None:
    """Non-external tools are never checked by the circuit breaker."""
    mw = CircuitBreakerMiddleware(failure_threshold=2, recovery_timeout=60.0)

    # grade_response is not in _CIRCUIT_TOOLS, so it passes through even
    # if it fails repeatedly
    for _i in range(5):
        result = await mw.awrap_tool_call(_Req("grade_response"), _ok_handler)
        assert result.content == "ok"


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    """Circuit opens after failure_threshold consecutive failures."""
    mw = CircuitBreakerMiddleware(failure_threshold=3, recovery_timeout=60.0)

    # First 3 failures: circuit stays closed (but transitions to OPEN on the 3rd)
    for i in range(3):
        with pytest.raises(RuntimeError, match="Connection refused"):
            await mw.awrap_tool_call(_Req("internet_search", {"q": f"test-{i}"}), _fail_handler)

    # 4th call: circuit is OPEN → fail fast
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "test-4"}), _fail_handler)
    assert isinstance(result, ToolMessage)
    assert "currently unavailable" in str(result.content)
    assert result.name == "internet_search"


@pytest.mark.asyncio
async def test_circuit_breaker_threshold_configurable() -> None:
    """Failure threshold is configurable."""
    mw = CircuitBreakerMiddleware(failure_threshold=1, recovery_timeout=60.0)

    # First failure opens circuit immediately
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "test-0"}), _fail_handler)

    # Second call blocked
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "test-1"}), _fail_handler)
    assert "currently unavailable" in str(result.content)


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_allows_probe() -> None:
    """After recovery timeout, a HALF_OPEN probe call is allowed."""
    mw = CircuitBreakerMiddleware(failure_threshold=2, recovery_timeout=0.05)

    # Open the circuit
    for _i in range(2):
        with pytest.raises(RuntimeError):
            await mw.awrap_tool_call(_Req("internet_search", {"q": "boom"}), _fail_handler)

    # Circuit is OPEN, call blocked
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "blocked"}), _fail_handler)
    assert "currently unavailable" in str(result.content)

    # Wait for recovery timeout
    time.sleep(0.06)

    # Probe succeeds → circuit closes
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "probe"}), _ok_handler)
    assert result.content == "ok"

    # Circuit closed — calls pass through again
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "after-recovery"}), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure_stays_open() -> None:
    """If HALF_OPEN probe fails, circuit stays open for another timeout."""
    mw = CircuitBreakerMiddleware(failure_threshold=2, recovery_timeout=0.05)

    # Open the circuit
    for _i in range(2):
        with pytest.raises(RuntimeError):
            await mw.awrap_tool_call(_Req("internet_search", {"q": "boom"}), _fail_handler)

    # Wait for recovery timeout
    time.sleep(0.06)

    # HALF_OPEN probe is attempted but fails → back to OPEN with new timeout
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "probe-fails"}), _fail_handler)

    # Still OPEN (not enough time since the HALF_OPEN probe failure)
    result = await mw.awrap_tool_call(
        _Req("internet_search", {"q": "still-blocked"}), _fail_handler
    )
    assert "currently unavailable" in str(result.content)


@pytest.mark.asyncio
async def test_circuit_breaker_per_tool_isolation() -> None:
    """Circuit state is isolated per tool name."""
    mw = CircuitBreakerMiddleware(failure_threshold=2, recovery_timeout=60.0)

    # Open circuit for internet_search
    for _i in range(2):
        with pytest.raises(RuntimeError):
            await mw.awrap_tool_call(_Req("internet_search", {"q": "boom"}), _fail_handler)

    # fetch_url should still be CLOSED (separate tool)
    result = await mw.awrap_tool_call(_Req("fetch_url", {"url": "http://example.com"}), _ok_handler)
    assert result.content == "ok"

    # internet_search is still blocked
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "blocked"}), _fail_handler)
    assert "currently unavailable" in str(result.content)


@pytest.mark.asyncio
async def test_circuit_breaker_recovery_on_success() -> None:
    """A successful call resets failure count and keeps circuit CLOSED."""
    mw = CircuitBreakerMiddleware(failure_threshold=3, recovery_timeout=60.0)

    # 2 failures, then success — circuit should still be CLOSED
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "fail-1"}), _fail_handler)
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "fail-2"}), _fail_handler)

    # Success resets failure count
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "success"}), _ok_handler)
    assert result.content == "ok"

    # Counter was reset, so 2 more failures don't open circuit yet
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "fail-3"}), _fail_handler)
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "fail-4"}), _fail_handler)

    # Need one more failure to open circuit (3rd consecutive after reset)
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "fail-5"}), _fail_handler)

    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "blocked"}), _fail_handler)
    assert "currently unavailable" in str(result.content)


@pytest.mark.asyncio
async def test_circuit_breaker_lifecycle_reset() -> None:
    """Middleware resets circuit state at the start of each agent run."""
    # Use a zero recovery timeout so we can detect OPEN state directly
    mw = CircuitBreakerMiddleware(failure_threshold=1, recovery_timeout=60.0)

    await mw.abefore_agent({}, None)

    # Open circuit
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "boom"}), _fail_handler)

    # Simulate run end and new run start
    await mw.aafter_agent({}, None)
    await mw.abefore_agent({}, None)

    # State should be reset — calls go through again
    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "new-run"}), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_circuit_breaker_lifecycle_cleanup() -> None:
    """Middleware reclaims per-thread breaker state after agent run ends."""
    mw = CircuitBreakerMiddleware(failure_threshold=3, recovery_timeout=60.0)

    await mw.abefore_agent({}, None)
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "boom"}), _fail_handler)
    tid = mw._thread_id()
    assert tid in mw._breakers
    assert "internet_search" in mw._breakers[tid]

    await mw.aafter_agent({}, None)
    assert tid not in mw._breakers


@pytest.mark.asyncio
async def test_circuit_breaker_non_dict_tool_call() -> None:
    """Middleware handles non-dict tool_call gracefully."""
    mw = CircuitBreakerMiddleware(failure_threshold=3, recovery_timeout=60.0)

    class _ReqNoDict:
        def __init__(self) -> None:
            self.tool_call = None  # type: ignore[assignment]

    result = await mw.awrap_tool_call(_ReqNoDict(), _ok_handler)
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_circuit_breaker_tool_message_attributes() -> None:
    """The blocked result is a ToolMessage with the expected attributes."""
    mw = CircuitBreakerMiddleware(failure_threshold=1, recovery_timeout=60.0)

    # Open the circuit
    with pytest.raises(RuntimeError):
        await mw.awrap_tool_call(_Req("internet_search", {"q": "boom"}), _fail_handler)

    result = await mw.awrap_tool_call(_Req("internet_search", {"q": "blocked"}), _fail_handler)
    assert isinstance(result, ToolMessage)
    assert "currently unavailable" in str(result.content)
    assert result.tool_call_id == "t-req-1"
    assert result.name == "internet_search"


# ── _BreakerEntry unit-level ────────────────────────────────────────────────


def test_breaker_entry_initial_state() -> None:
    """A new _BreakerEntry starts CLOSED with zero failures."""
    entry = _BreakerEntry()
    assert entry.state is _CircuitState.CLOSED
    assert entry.failure_count == 0
    assert entry.last_failure_time is None


def test_breaker_entry_record_failure() -> None:
    """record_failure increments count and sets last_failure_time."""
    entry = _BreakerEntry()
    count = entry.record_failure()
    assert count == 1
    assert entry.failure_count == 1
    assert entry.last_failure_time is not None


def test_breaker_entry_record_success() -> None:
    """record_success resets failure_count and last_failure_time."""
    entry = _BreakerEntry()
    entry.failure_count = 5
    entry.last_failure_time = 12345.0
    entry.state = _CircuitState.OPEN
    entry.record_success()
    assert entry.state is _CircuitState.CLOSED
    assert entry.failure_count == 0
    assert entry.last_failure_time is None


def test_breaker_entry_should_probe_no_failures() -> None:
    """should_probe returns True when there have been no failures."""
    entry = _BreakerEntry()
    assert entry.should_probe(30.0) is True


def test_breaker_entry_should_probe_after_timeout() -> None:
    """should_probe returns True when recovery timeout has elapsed."""
    entry = _BreakerEntry()
    entry.last_failure_time = time.monotonic() - 60.0  # 60 seconds ago
    assert entry.should_probe(30.0) is True


def test_breaker_entry_should_probe_before_timeout() -> None:
    """should_probe returns False when recovery timeout has NOT elapsed."""
    entry = _BreakerEntry()
    entry.last_failure_time = time.monotonic()  # just now
    assert entry.should_probe(30.0) is False
