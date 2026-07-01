"""Tests for the ``make_semantic_recall_tool`` factory.

The tool requires an ``AsyncRedisStore`` instance (the only LangGraph
store that supports vector search) and a Settings with vector
indexing enabled. We use a real ``AsyncRedisStore`` subclass that
overrides ``asearch`` to return canned results; we never connect
to a real Redis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langgraph.store.redis.aio import AsyncRedisStore

from core.config import Settings
from core.episodic import make_semantic_recall_tool
from core.request_context import caller_var


def _settings(**overrides: Any) -> Settings:
    """Return Settings with vector indexing enabled (the default)."""
    overrides.setdefault("enable_vector_index", True)
    overrides.setdefault("embedding_model", "embeddinggemma")
    overrides.setdefault("embedding_dim", 768)
    return Settings(**overrides)


@dataclass
class _FakeItem:
    """Minimal ``SearchItem`` shape: key, namespace, value, score."""

    key: str
    namespace: tuple[str, ...]
    value: Any
    score: float | None = None


class _FakeRedisStore(AsyncRedisStore):
    """Real ``AsyncRedisStore`` subclass that records ``asearch`` calls
    and returns canned results. Skips parent init (no real connection).
    """

    def __init__(self, results: list[_FakeItem] | None = None) -> None:  # type: ignore[no-super-call]
        self.results = results or []
        self.calls: list[dict[str, Any]] = []

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        refresh_ttl: bool | None = None,
    ) -> list[_FakeItem]:
        self.calls.append(
            {
                "namespace_prefix": namespace_prefix,
                "query": query,
                "limit": limit,
            }
        )
        return self.results


# ── Factory behavior ─────────────────────────────────────────────────────────


def test_factory_returns_none_for_none_store() -> None:
    """Without a store, the tool is not built (caller skips wiring)."""
    assert make_semantic_recall_tool(None, _settings()) is None


def test_factory_returns_none_for_non_redis_store() -> None:
    """A non-Redis store (Postgres, in-memory) returns None — no RAG."""

    class _InMemoryStore:
        async def asearch(self, *args: Any, **kwargs: Any) -> Any:
            return []

    assert make_semantic_recall_tool(_InMemoryStore(), _settings()) is None


def test_factory_returns_none_when_vector_index_disabled() -> None:
    """``Settings.enable_vector_index=False`` returns None even for
    a Redis store — the tool would just fail at search time anyway,
    so we skip wiring."""
    store = _FakeRedisStore()
    assert make_semantic_recall_tool(
        store, _settings(enable_vector_index=False)
    ) is None


def test_factory_returns_tool_for_redis_store_with_vector_index() -> None:
    """Happy path: Redis store + vector index enabled → tool returned."""
    store = _FakeRedisStore()
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None
    assert tool.name == "semantic_recall"


# ── Tool behavior ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_passes_query_to_asearch_with_caller_namespace() -> None:
    """The tool calls ``asearch`` with ``("ossia", caller)`` and the
    raw text query (the store embeds it internally)."""
    store = _FakeRedisStore()
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None
    caller_var.set("user-abc")
    try:
        await tool.ainvoke({"query": "what did we deploy last week", "top_k": 3})
    finally:
        caller_var.set(None)

    assert len(store.calls) == 1
    call = store.calls[0]
    assert call["namespace_prefix"] == ("ossia", "user-abc")
    assert call["query"] == "what did we deploy last week"
    assert call["limit"] == 3


@pytest.mark.asyncio
async def test_tool_returns_matches_with_namespace_and_score() -> None:
    """Match items are returned with key, namespace, value, and score."""
    store = _FakeRedisStore(
        results=[
            _FakeItem(
                key="/memories/AGENTS.md",
                namespace=("ossia", "user-abc"),
                value={"content": "deployment notes"},
                score=0.91,
            ),
            _FakeItem(
                key="/memories/learned.md",
                namespace=("ossia", "user-abc"),
                value={"content": "rollback procedure"},
                score=0.78,
            ),
        ]
    )
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None
    caller_var.set("user-abc")
    try:
        out = await tool.ainvoke({"query": "deployment", "top_k": 5})
    finally:
        caller_var.set(None)
    assert len(out["matches"]) == 2
    assert out["matches"][0]["key"] == "/memories/AGENTS.md"
    assert out["matches"][0]["namespace"] == ["ossia", "user-abc"]
    assert out["matches"][0]["score"] == 0.91
    assert out["matches"][1]["score"] == 0.78


@pytest.mark.asyncio
async def test_tool_falls_back_to_default_caller() -> None:
    """No caller context → namespace ``("ossia", "default")``."""
    store = _FakeRedisStore()
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None
    await tool.ainvoke({"query": "x", "top_k": 1})
    assert store.calls[0]["namespace_prefix"] == ("ossia", "default")


@pytest.mark.asyncio
async def test_tool_handles_asearch_failure_gracefully() -> None:
    """If asearch raises, the tool returns an empty match list with
    an error field — does not propagate the exception."""

    class _BrokenStore(_FakeRedisStore):
        async def asearch(
            self, *args: Any, **kwargs: Any
        ) -> list[_FakeItem]:
            raise RuntimeError("redis down")

    store = _BrokenStore()
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None
    out = await tool.ainvoke({"query": "x", "top_k": 1})
    assert out["matches"] == []
    assert "redis down" in out["error"]


@pytest.mark.asyncio
async def test_tool_returns_empty_when_no_matches() -> None:
    store = _FakeRedisStore(results=[])
    tool = make_semantic_recall_tool(store, _settings())
    assert tool is not None
    out = await tool.ainvoke({"query": "x", "top_k": 5})
    assert out["matches"] == []
    assert "error" not in out
