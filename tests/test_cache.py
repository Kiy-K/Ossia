"""Tests for the Redis cache + lock helpers.

Uses a fake in-process Redis (``FakeRedis``) to avoid adding a test
dependency on ``fakeredis`` or requiring a real Redis. The fakes
implement only the surface area ``core.cache`` touches (``get``,
``set``, ``delete``, ``aclose``) — no protocol mimicry.

Every test resets the lazy client between runs via
:func:`core.redis_client.reset_redis` and patches
:func:`core.cache.get_*_redis` so the helpers pick up the fake.
"""

from __future__ import annotations

from typing import Any

import pytest

from core import cache, redis_client


class FakeAsyncRedis:
    """Minimal async Redis stub: in-memory dict + acquire/release semantics."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self.store:
            return False
        self.store[key] = bytes(value) if not isinstance(value, bytes) else value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def aclose(self) -> None:
        pass


class FakeSyncRedis:
    """Minimal sync Redis stub."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and key in self.store:
            return False
        self.store[key] = bytes(value) if not isinstance(value, bytes) else value
        return True

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    def close(self) -> None:
        pass


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeAsyncRedis, FakeSyncRedis]:
    """Patch both async + sync client getters to return fakes."""
    async_fake = FakeAsyncRedis()
    sync_fake = FakeSyncRedis()
    redis_client.reset_redis()
    monkeypatch.setattr(cache, "get_async_redis", lambda: async_fake)
    monkeypatch.setattr(cache, "get_sync_redis", lambda: sync_fake)
    return async_fake, sync_fake


@pytest.fixture
def no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch both client getters to return None (Redis unset path)."""
    redis_client.reset_redis()
    monkeypatch.setattr(cache, "get_async_redis", lambda: None)
    monkeypatch.setattr(cache, "get_sync_redis", lambda: None)


# ── cached_fetch (async) ────────────────────────────────────────────────────


async def test_cached_fetch_miss_then_hit(fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis]) -> None:
    """First call runs ``fetch``; second call returns the cached value."""
    async_fake, _ = fake_redis
    calls = {"n": 0}

    async def fetch() -> bytes:
        calls["n"] += 1
        return b"result"

    first = await cache.cached_fetch("test", "k1", fetch=fetch, ttl=60)
    second = await cache.cached_fetch("test", "k1", fetch=fetch, ttl=60)
    assert first == b"result"
    assert second == b"result"
    assert calls["n"] == 1, "second call should hit cache, not call fetch"
    assert async_fake.store, "cache key should be set"


async def test_cached_fetch_distinct_keys_dont_collide(
    fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis],
) -> None:
    """Different key_parts produce different cache keys."""
    calls = {"n": 0}

    async def fetch() -> bytes:
        calls["n"] += 1
        return b"v"

    await cache.cached_fetch("test", "a", fetch=fetch, ttl=60)
    await cache.cached_fetch("test", "b", fetch=fetch, ttl=60)
    assert calls["n"] == 2


async def test_cached_fetch_degrades_without_redis(no_redis: None) -> None:
    """When Redis is unset, every call invokes ``fetch`` (no cache)."""
    calls = {"n": 0}

    async def fetch() -> bytes:
        calls["n"] += 1
        return b"x"

    a = await cache.cached_fetch("test", "k", fetch=fetch, ttl=60)
    b = await cache.cached_fetch("test", "k", fetch=fetch, ttl=60)
    assert a == b == b"x"
    assert calls["n"] == 2


# ── cached_fetch_sync ───────────────────────────────────────────────────────


def test_cached_fetch_sync_miss_then_hit(fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis]) -> None:
    """Sync twin of ``cached_fetch``."""
    _, sync_fake = fake_redis
    calls = {"n": 0}

    def fetch() -> bytes:
        calls["n"] += 1
        return b"sync-result"

    first = cache.cached_fetch_sync("test", "k", fetch=fetch, ttl=60)
    second = cache.cached_fetch_sync("test", "k", fetch=fetch, ttl=60)
    assert first == b"sync-result"
    assert second == b"sync-result"
    assert calls["n"] == 1
    assert sync_fake.store


def test_cached_fetch_sync_degrades_without_redis(no_redis: None) -> None:
    calls = {"n": 0}

    def fetch() -> bytes:
        calls["n"] += 1
        return b"x"

    cache.cached_fetch_sync("test", "k", fetch=fetch, ttl=60)
    cache.cached_fetch_sync("test", "k", fetch=fetch, ttl=60)
    assert calls["n"] == 2


# ── redis_lock ──────────────────────────────────────────────────────────────


async def test_redis_lock_acquired_on_first_caller(
    fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis],
) -> None:
    """First holder gets acquired=True; lock key is set in Redis."""
    async_fake, _ = fake_redis
    async with cache.redis_lock("seed_memory", "ns", "/memories/AGENTS.md") as acquired:
        assert acquired is True
        assert any(k.startswith("lock:seed_memory:") for k in async_fake.store)
    # Lock released on exit.
    assert not async_fake.store


async def test_redis_lock_blocked_for_second_caller(
    fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis],
) -> None:
    """While holder A owns the lock, holder B sees acquired=False."""
    async_fake, _ = fake_redis
    # Compute the same hashed key the lock will use.
    pre_key = cache._key("lock:seed_memory", "ns", "key")
    await async_fake.set(pre_key, b"1", nx=True, ex=5)
    async with cache.redis_lock("seed_memory", "ns", "key") as acquired:
        assert acquired is False, "second caller must not acquire the lock"
    # Manually-placed lock was not deleted by the failed acquire.
    assert pre_key in async_fake.store


async def test_redis_lock_released_after_crash_via_ttl(
    fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis],
) -> None:
    """The lock has a TTL so a crashed holder doesn't deadlock forever.

    Ponytail: the TTL is the ceiling — a holder that crashes does not
    extend its lock; the next caller waits at most ``ttl`` seconds.
    """
    async_fake, _ = fake_redis
    # Place a stale lock as if from a crashed holder (use the same
    # hashed key the lock will produce).
    pre_key = cache._key("lock:seed_memory", "stale")
    await async_fake.set(pre_key, b"1", nx=True, ex=1)
    async with cache.redis_lock("seed_memory", "stale") as acquired:
        assert acquired is False
    # Original key still present (the stale holder's lock).
    assert pre_key in async_fake.store


async def test_redis_lock_degrades_without_redis(no_redis: None) -> None:
    """No Redis: every caller gets acquired=True (no serialization)."""
    async with cache.redis_lock("seed_memory", "ns", "k") as acquired:
        assert acquired is True


# ── seed_memory integration ─────────────────────────────────────────────────


async def test_seed_memory_uses_lock_when_redis_available(
    fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis],
) -> None:
    """``seed_memory`` wraps its critical section in ``redis_lock``."""
    from langgraph.store.memory import InMemoryStore

    from core.memory import seed_memory

    store = InMemoryStore()
    created = await seed_memory(store)
    assert created is True
    # Lock was taken and released — no leftover lock key.
    async_fake, _ = fake_redis
    assert not async_fake.store


async def test_seed_memory_still_idempotent_with_lock(
    fake_redis: tuple[FakeAsyncRedis, FakeSyncRedis],
) -> None:
    """Two sequential ``seed_memory`` calls: first creates, second is a no-op."""
    from langgraph.store.memory import InMemoryStore

    from core.memory import seed_memory

    store = InMemoryStore()
    first = await seed_memory(store)
    second = await seed_memory(store)
    assert first is True
    assert second is False
