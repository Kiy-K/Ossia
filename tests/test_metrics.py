"""Tests for the LLM usage / cost counters in ``core.metrics``."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from core.metrics import estimate_cost_usd_micros


def _msg(
    *,
    usage_metadata: dict[str, Any] | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> Any:
    """Build a fake LangChain ``AIMessage`` carrying usage info."""
    m = MagicMock()
    m.usage_metadata = usage_metadata
    m.response_metadata = response_metadata or {}
    return m


def test_estimate_cost_known_model() -> None:
    """Cost in micro-USD for a known model (allow ±1 for float rounding)."""
    # gpt-4o-mini: 0.00015 prompt, 0.0006 completion per 1K
    # 1000 prompt + 1000 completion = 0.00015 + 0.0006 ≈ 0.00075 USD ≈ 750 micro
    micros = estimate_cost_usd_micros("openai/gpt-4o-mini", 1000, 1000)
    assert abs(micros - 750) <= 1


def test_estimate_cost_unknown_model_is_zero() -> None:
    assert estimate_cost_usd_micros("some/future-model", 1_000_000, 1_000_000) == 0


def test_estimate_cost_sub_cent_resolution() -> None:
    """Tiny responses resolve to single-digit micro-USD."""
    # 1 prompt + 1 completion at haiku: 0.0000008 + 0.000004 = 0.0000048 USD ≈ 5 micros
    micros = estimate_cost_usd_micros("anthropic/claude-haiku-4-5", 1, 1)
    assert 0 < micros < 10


def test_record_usage_modern_path() -> None:
    """AIMessage with usage_metadata populates tokens + cost."""
    from unittest.mock import patch

    from core.api import _record_llm_usage

    msgs = [
        _msg(usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}),
    ]
    # _record_llm_usage does a function-local import from core.metrics;
    # patch the names there.
    with (
        patch("core.metrics.LLM_REQUESTS", new=MagicMock()) as reqs,
        patch("core.metrics.LLM_TOKENS", new=MagicMock()) as toks,
        patch("core.metrics.LLM_COST_USD", new=MagicMock()) as costs,
    ):
        _record_llm_usage(msgs, provider="openrouter", model="openai/gpt-4o-mini")
    reqs.labels.assert_called_once_with(provider="openrouter", model="openai/gpt-4o-mini")
    reqs.labels.return_value.inc.assert_called_once()
    # 3 kinds: prompt / completion / total
    assert toks.labels.call_count == 3
    costs.labels.assert_called_once_with(provider="openrouter", model="openai/gpt-4o-mini")


def test_record_usage_legacy_path() -> None:
    """response_metadata.token_usage is read when usage_metadata is missing."""
    from unittest.mock import patch

    from core.api import _record_llm_usage

    msgs = [
        _msg(
            usage_metadata=None,
            response_metadata={
                "token_usage": {"prompt_tokens": 200, "completion_tokens": 80}
            },
        ),
    ]
    with (
        patch("core.metrics.LLM_REQUESTS", new=MagicMock()),
        patch("core.metrics.LLM_TOKENS", new=MagicMock()) as toks,
        patch("core.metrics.LLM_COST_USD", new=MagicMock()),
    ):
        _record_llm_usage(msgs, provider="openai", model="openai/gpt-4o")
    prompt_inc = toks.labels.return_value.inc.call_args_list
    assert len(prompt_inc) == 3
    # Last call: total = 200 + 80 = 280
    total_call = prompt_inc[2]
    assert total_call.args[0] == 280


def test_record_usage_unknown_model_skips_cost() -> None:
    """Unknown models still count tokens but skip cost."""
    from unittest.mock import patch

    from core.api import _record_llm_usage

    msgs = [_msg(usage_metadata={"input_tokens": 10, "output_tokens": 5})]
    with (
        patch("core.metrics.LLM_REQUESTS", new=MagicMock()),
        patch("core.metrics.LLM_TOKENS", new=MagicMock()),
        patch("core.metrics.LLM_COST_USD", new=MagicMock()) as costs,
    ):
        _record_llm_usage(msgs, provider="custom", model="vendor/secret-model")
    costs.labels.assert_not_called()


def test_record_usage_empty_messages_still_counts_request() -> None:
    """A request with no usage data still bumps the request counter."""
    from unittest.mock import patch

    from core.api import _record_llm_usage

    with (
        patch("core.metrics.LLM_REQUESTS", new=MagicMock()) as reqs,
        patch("core.metrics.LLM_TOKENS", new=MagicMock()) as toks,
        patch("core.metrics.LLM_COST_USD", new=MagicMock()),
    ):
        _record_llm_usage([], provider="openrouter", model="openai/gpt-4o-mini")
    reqs.labels.return_value.inc.assert_called_once()
    toks.labels.assert_not_called()
