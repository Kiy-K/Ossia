"""Tests for the semantic_recall tool.

The tool is the third memory surface (semantic search across
threads) per ADR-0007. It is gated on the store being an
``AsyncRedisStore`` with a RediSearch index, so most tests here
cover the factory's gating logic and the tool's error/namespace
contract using a fake store.

Tests cover:
1. Factory returns ``None`` when the store is not a Redis store
   (``InMemoryStore`` / ``AsyncPostgresStore``).
2. Factory returns ``None`` when ``Settings.enable_vector_index=False``.
3. Factory returns a tool when given a Redis-shaped store.
4. Tool searches the *caller's* namespace ``("ossia", caller)``,
   not the base.
5. Tool returns ``{"matches": [...], "error": ...}`` when
   ``store.asearch`` raises — does not propagate.
6. Tool returns ``{"matches": []}`` when no caller is set.
7. Tool honours ``top_k`` and passes the raw ``query`` to the store
   (the store embeds it internally).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.store.memory import InMemoryStore
from langgraph.store.redis.aio import AsyncRedisStore

# AsyncRedisStore.__del__ complains when we skip __init__ via __new__.
# We only use the instance for isinstance checks; the deallocator is a no-op.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

from core.config import Settings  # noqa: E402
from core.episodic import make_semantic_recall_tool  # noqa: E402
from core.request_context import caller_var  # noqa: E402


class _FakeSearchHit:
    """Mimics the langchain store Item return shape."""

    def __init__(self, key: str, namespace: tuple[str, ...], value: Any, score: float = 0.9) -> None:
        self.key = key
        self.namespace = namespace
        self.value = value
        self.score = score


def _redis_shaped_store() -> AsyncRedisStore:
    """Build a real ``AsyncRedisStore`` instance and overwrite its
    async methods with ``AsyncMock`` so we never touch a real server.

    Ponytail: subclassing + AsyncMock beats reimplementing the type
    hierarchy in a fake class. The isinstance check passes (it IS an
    AsyncRedisStore) and the methods are no-ops.
    """
    store = AsyncRedisStore.__new__(AsyncRedisStore)
    store.asearch = AsyncMock()  # type: ignore[method-assign]
    return store  # type: ignore[return-value]


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "provider": "openrouter",
        "model": "openai/gpt-4o-mini",
        "enable_vector_index": True,
    }
    base.update(overrides)
    return Settings(**base)


def test_factory_returns_none_for_inmemory_store() -> None:
    """InMemoryStore has no vector index — factory skips the tool."""
    tool = make_semantic_recall_tool(InMemoryStore(), _settings())
    assert tool is None


def test_factory_returns_none_when_vector_index_disabled() -> None:
    """The factory short-circuits when ``Settings.enable_vector_index=False``."""
    tool = make_semantic_recall_tool(_redis_shaped_store(), _settings(enable_vector_index=False))
    assert tool is None


def test_factory_returns_tool_for_redis_store() -> None:
    """When the store is a Redis instance and vector indexing is on, factory returns a tool."""
    tool = make_semantic_recall_tool(_redis_shaped_store(), _settings())
    assert tool is not None
    assert tool.name == "semantic_recall"


def test_tool_uses_caller_namespace() -> None:
    """The tool queries ``("ossia", caller)`` so callers can't see each other."""
    store = _redis_shaped_store()
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None

    caller_var.set("alice")
    try:
        asyncio.run(tool.ainvoke({"query": "what is x?", "top_k": 3}))  # type: ignore[arg-type]
    finally:
        caller_var.set(None)

    store.asearch.assert_awaited_once()  # type: ignore[attr-defined]
    call = store.asearch.await_args  # type: ignore[attr-defined]
    assert call.args[0] == ("ossia", "alice")
    assert call.kwargs["query"] == "what is x?"
    assert call.kwargs["limit"] == 3


def test_tool_returns_empty_matches_when_no_caller() -> None:
    """Without a caller context, the tool falls back to the 'default' namespace
    and still returns a well-formed response."""
    store = _redis_shaped_store()
    store.asearch.return_value = []  # type: ignore[attr-defined]
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None

    caller_var.set(None)
    result = asyncio.run(tool.ainvoke({"query": "anything"}))  # type: ignore[arg-type]
    assert result == {"matches": []}
    assert store.asearch.await_args.args[0] == ("ossia", "default")  # type: ignore[attr-defined]


def test_tool_swallows_store_errors() -> None:
    """A store failure becomes ``{matches: [], error: ...}`` — never propagates."""
    store = _redis_shaped_store()
    store.asearch.side_effect = RuntimeError("redis down")  # type: ignore[attr-defined]
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None

    caller_var.set("alice")
    try:
        result = asyncio.run(tool.ainvoke({"query": "x"}))  # type: ignore[arg-type]
    finally:
        caller_var.set(None)

    assert result["matches"] == []
    assert "vector search failed" in result["error"]
    assert "redis down" in result["error"]


def test_tool_normalizes_match_shape() -> None:
    """Hits are returned as ``{key, namespace, value, score}`` dicts."""
    store = _redis_shaped_store()
    store.asearch.return_value = [  # type: ignore[attr-defined]
        _FakeSearchHit(
            key="AGENTS.md",
            namespace=("ossia", "bob"),
            value={"content": ["matched body"], "created_at": "2026-01-01"},
            score=0.87,
        )
    ]
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None

    caller_var.set("bob")
    try:
        result = asyncio.run(tool.ainvoke({"query": "x", "top_k": 1}))  # type: ignore[arg-type]
    finally:
        caller_var.set(None)

    assert len(result["matches"]) == 1
    hit = result["matches"][0]
    assert hit["key"] == "AGENTS.md"
    assert hit["namespace"] == ["ossia", "bob"]
    assert hit["score"] == 0.87
    assert hit["value"]["content"] == ["matched body"]


def test_settings_enable_vector_index_default_is_true() -> None:
    """The default keeps vector recall on for Redis deployments."""
    s = Settings(provider="openrouter", model="openai/gpt-4o-mini")
    assert s.enable_vector_index is True
