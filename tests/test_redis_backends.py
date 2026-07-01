"""Tests for the Redis backend helpers in core.memory.

The Redis backends (langgraph-checkpoint-redis) require a real
Redis 8+ with the RedisJSON and RediSearch modules. The CI test
env doesn't have one, so we only test the failure-mode
(ValueError when ``REDIS_URL`` is unset) and the import shape.
Integration with a real Redis is exercised in the ``make audit``
path against a live server.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.config import get_settings
from core.memory import get_redis_checkpointer, get_redis_store


def _settings_without_redis() -> Any:
    """Return a Settings instance with ``REDIS_URL`` cleared.

    The cached settings may have been populated by another test
    or by the env; this helper forces a no-Redis configuration.
    """
    settings = get_settings()
    settings.redis_url = None
    return settings


@pytest.mark.asyncio
async def test_get_redis_checkpointer_requires_redis_url() -> None:
    """Without ``REDIS_URL``, the helper raises ``ValueError``."""
    with pytest.raises(ValueError, match="REDIS_URL"):
        async with get_redis_checkpointer(_settings_without_redis()):
            pass


@pytest.mark.asyncio
async def test_get_redis_store_requires_redis_url() -> None:
    """Without ``REDIS_URL``, the helper raises ``ValueError``."""
    with pytest.raises(ValueError, match="REDIS_URL"):
        async with get_redis_store(_settings_without_redis()):
            pass


@pytest.mark.asyncio
async def test_get_redis_store_accepts_index_config() -> None:
    """An IndexConfig can be passed; the helper still fails fast on
    the missing URL (we never reach the Redis connection)."""
    from langgraph.store.base import IndexConfig

    # Minimal valid shape; the helper fails fast on the missing URL
    # before it ever instantiates the embedder, so the embed value
    # never gets called.
    index: IndexConfig = {"dims": 1536, "embed": "openai:text-embedding-3-small"}
    with pytest.raises(ValueError, match="REDIS_URL"):
        async with get_redis_store(_settings_without_redis(), index=index):
            pass
