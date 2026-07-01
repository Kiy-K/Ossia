"""Redis-backed cache and lock helpers.

Three primitives, all no-op when Redis is unset:

- :func:`cached_fetch` — async; for async tool wrappers.
- :func:`cached_fetch_sync` — sync; for sync tool wrappers.
- :func:`redis_lock` — async context manager; serializes the
  ``seed_memory`` get-then-put critical section (surface #4).

Cache contract: both cache helpers store and return **bytes**. The
caller is responsible for serializing on write and deserializing on
hit (``model_dump_json().encode()`` / ``model_validate_json(b)``).
This keeps the cache typed concretely; the cache does not know
about Pydantic.

Ponytail: one helper per primitive, no decorator framework, no
abstraction over the Redis API. Add a TTL knob per call site; do not
add a config knob.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from core.redis_client import get_async_redis, get_sync_redis

_DEFAULT_CACHE_TTL = 60
_DEFAULT_LOCK_TTL = 5


def _key(prefix: str, *parts: str) -> str:
    """Build a stable, namespaced cache/lock key.

    Hashes long parts so the key stays short and avoids weird
    characters in the value being included in the key name.
    """
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return f"{prefix}:{h.hexdigest()[:16]}"


async def cached_fetch(
    prefix: str,
    *key_parts: str,
    ttl: int = _DEFAULT_CACHE_TTL,
    fetch: Callable[[], Awaitable[bytes]],
) -> bytes:
    """Async cache wrapper. See module docstring."""
    client = get_async_redis()
    key = _key(prefix, *key_parts)
    if client is not None:
        with contextlib.suppress(Exception):
            cached = await client.get(key)
            if cached is not None:
                return cast(bytes, cached)
    value = await fetch()
    if client is not None and value is not None:
        with contextlib.suppress(Exception):
            await client.set(key, value, ex=ttl)
    return value


def cached_fetch_sync(
    prefix: str,
    *key_parts: str,
    ttl: int = _DEFAULT_CACHE_TTL,
    fetch: Callable[[], bytes],
) -> bytes:
    """Sync cache wrapper. See module docstring."""
    client = get_sync_redis()
    key = _key(prefix, *key_parts)
    if client is not None:
        with contextlib.suppress(Exception):
            cached = client.get(key)
            if cached is not None:
                return cast(bytes, cached)
    value = fetch()
    if client is not None and value is not None:
        with contextlib.suppress(Exception):
            client.set(key, value, ex=ttl)
    return value


@asynccontextmanager
async def redis_lock(
    name: str,
    *key_parts: str,
    ttl: int = _DEFAULT_LOCK_TTL,
) -> AsyncIterator[bool]:
    """Acquire a short ``SET NX EX`` lock; yield ``True`` when the
    caller may proceed.

    Yield value semantics:

    - ``True`` — caller may proceed (no Redis, lock acquired, or
      lock acquisition failed and we fell through). No contention.
    - ``False`` — caller is contended; another holder owns the
      lock. Body still runs (we never block the request) but the
      caller can choose to early-exit.

    The body always runs. When Redis is unset, the lock is a no-op
    (yield ``True``).

    Args:
        name: Lock name (e.g. ``"seed_memory"``).
        *key_parts: Positional parts that uniquely identify the
            resource (e.g. namespace, key).
        ttl: Lock auto-expiry in seconds. Bounded so a crashed
            holder cannot deadlock the resource.
    """
    client = get_async_redis()
    if client is None:
        yield True
        return
    key = _key(f"lock:{name}", *key_parts)
    acquired = False
    with contextlib.suppress(Exception):
        acquired = bool(await client.set(key, b"1", nx=True, ex=ttl))
    try:
        yield acquired if acquired else False
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                await client.delete(key)
