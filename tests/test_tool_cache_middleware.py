"""Tests for the ``ToolResultCacheMiddleware`` integration.

The middleware is added to the agent's middleware list when
``REDIS_URL`` is set. We don't run a full agent build (that
requires a chat model); we test the helper function directly.
"""

from __future__ import annotations

from typing import Any

from core.agent import _build_middlewares
from core.config import Settings


def _settings(**overrides: Any) -> Settings:
    """Settings with the test-friendly defaults."""
    overrides.setdefault("provider", "openrouter")
    overrides.setdefault("model", "openai/gpt-4o-mini")
    overrides.setdefault("openrouter_api_key", "sk-test")
    overrides.setdefault("enable_human_review", False)
    return Settings(**overrides)


def test_cache_middleware_absent_when_redis_url_unset() -> None:
    """Without ``REDIS_URL``, no cache middleware is appended."""
    settings = _settings(redis_url=None)
    middlewares = _build_middlewares(settings)
    types = [type(m).__name__ for m in middlewares]
    assert "ToolResultCacheMiddleware" not in types


def test_cache_middleware_added_when_redis_url_set() -> None:
    """With ``REDIS_URL``, the cache middleware is in the stack."""
    settings = _settings(redis_url="redis://localhost:6379/0")
    middlewares = _build_middlewares(settings)
    types = [type(m).__name__ for m in middlewares]
    assert "ToolResultCacheMiddleware" in types


def test_cache_middleware_respects_enable_tool_cache_false() -> None:
    """``Settings.enable_tool_cache=False`` disables the middleware
    even when ``REDIS_URL`` is set."""
    settings = _settings(
        redis_url="redis://localhost:6379/0",
        enable_tool_cache=False,
    )
    middlewares = _build_middlewares(settings)
    types = [type(m).__name__ for m in middlewares]
    assert "ToolResultCacheMiddleware" not in types


def test_cache_middleware_uses_configured_ttl() -> None:
    """The configured ``tool_cache_ttl_seconds`` reaches the config."""

    settings = _settings(
        redis_url="redis://localhost:6379/0",
        tool_cache_ttl_seconds=120,
    )
    middlewares = _build_middlewares(settings)
    cache_mw = next(m for m in middlewares if type(m).__name__ == "ToolResultCacheMiddleware")
    assert cache_mw._config.ttl_seconds == 120  # type: ignore[attr-defined]


def test_cache_middleware_blocks_edit_prefix() -> None:
    """``edit_file`` is a write tool — must not be cacheable. The
    library's ``side_effect_prefixes`` controls this; we add
    ``edit_`` to the defaults. Verify the configured config
    includes the prefix."""
    from langgraph.middleware.redis import (
        DEFAULT_SIDE_EFFECT_PREFIXES,
    )

    settings = _settings(redis_url="redis://localhost:6379/0")
    middlewares = _build_middlewares(settings)
    cache_mw = next(m for m in middlewares if type(m).__name__ == "ToolResultCacheMiddleware")
    prefixes = cache_mw._config.side_effect_prefixes  # type: ignore[attr-defined]
    assert "edit_" in prefixes
    # The default prefixes are preserved.
    for default_prefix in DEFAULT_SIDE_EFFECT_PREFIXES:
        assert default_prefix in prefixes


def test_cache_middleware_uses_configured_redis_url() -> None:
    """The configured ``REDIS_URL`` is passed to the cache config."""

    settings = _settings(redis_url="redis://custom:1234/2")
    middlewares = _build_middlewares(settings)
    cache_mw = next(m for m in middlewares if type(m).__name__ == "ToolResultCacheMiddleware")
    assert cache_mw._config.redis_url == "redis://custom:1234/2"  # type: ignore[attr-defined]


def test_cache_middleware_survives_bad_redis_url() -> None:
    """A bad URL at construction time is caught by the
    try/except in ``_build_middlewares``; the agent runs
    without tool caching. The library validates the URL
    scheme eagerly."""
    settings = _settings(redis_url="not-a-valid-url")
    middlewares = _build_middlewares(settings)
    types = [type(m).__name__ for m in middlewares]
    assert "ToolResultCacheMiddleware" not in types
