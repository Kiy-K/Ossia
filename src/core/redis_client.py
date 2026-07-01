"""Process-wide Redis clients (sync + async).

Lazy singletons; both return ``None`` when ``REDIS_URL`` is unset so
call sites can degrade gracefully (cache miss = recompute, lock
acquire = proceed without lock).

Lifespan (``core.api``) calls :func:`close_redis` on shutdown. Tests
call :func:`reset_redis` to force re-init after mutating ``Settings``.

Two clients because the cache wraps a sync tool (``fetch_url``) and
the lock guards an async function (``seed_memory``). The sync and
async clients share a single connection pool per process, so the
cost is one pool, not two.
"""

from __future__ import annotations

import redis
import redis.asyncio as aioredis

from core.config import Settings, get_settings

_sync_client: redis.Redis | None = None
_async_client: aioredis.Redis | None = None
_initialized: bool = False


def get_async_redis(settings: Settings | None = None) -> aioredis.Redis | None:
    """Return the lazy async Redis client, or ``None`` when unset."""
    _ensure_initialized(settings)
    return _async_client


def get_sync_redis(settings: Settings | None = None) -> redis.Redis | None:
    """Return the lazy sync Redis client, or ``None`` when unset."""
    _ensure_initialized(settings)
    return _sync_client


def _ensure_initialized(settings: Settings | None) -> None:
    global _sync_client, _async_client, _initialized
    if _initialized:
        return
    settings = settings or get_settings()
    if settings.redis_url:
        _sync_client = redis.Redis.from_url(settings.redis_url, decode_responses=False)
        _async_client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=False
        )
    _initialized = True


async def close_redis() -> None:
    """Close and forget both Redis clients. Safe to call when unset."""
    global _sync_client, _async_client, _initialized
    if _async_client is not None:
        await _async_client.aclose()
    if _sync_client is not None:
        _sync_client.close()
    _sync_client = None
    _async_client = None
    _initialized = False


def reset_redis() -> None:
    """Forget cached clients without closing. Test-only helper."""
    global _sync_client, _async_client, _initialized
    _sync_client = None
    _async_client = None
    _initialized = False
