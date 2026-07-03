"""Tests for the URL-loaded, Redis-backed knowledge base.

Covers URL parsing, the loader's fetch + Redis write path, the
manifest format, and the tool's read path. The loader's HTTP fetcher
is a mock lambda; the Redis client is a fake in-process stub.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core import tools as tools_module
from core.kb_loader import (
    load_kb_into_redis,
    parse_source_urls,
    read_kb_from_redis,
    reset_kb_cache,
)
from core.tools import KnowledgeBase, _build_kb, create_kb

# ── Stubs (shared with test_cache.py shape) ────────────────────────────────


class FakeAsyncRedis:
    """Minimal async Redis stub: in-memory dict + SET semantics."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.sets: dict[str, set[str]] = {}
        # ``execute_command`` is mocked per test via ``set_exec``.
        # Default: succeed and return ``[]`` (covers FT.CREATE IFNX
        # which returns nothing on success).
        self._exec_response: Any = []

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def set_exec(self, response: Any) -> None:
        """Set the canned response for the next ``execute_command`` call."""
        self._exec_response = response

    async def execute_command(self, *args: Any, **kwargs: Any) -> Any:
        return self._exec_response

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool | None:
        self.store[key] = bytes(value) if not isinstance(value, bytes) else value
        return True

    async def delete(self, key: str) -> int:
        removed = 1 if self.store.pop(key, None) is not None else 0
        self.sets.pop(key, None)
        return removed

    async def sadd(self, key: str, *members: str) -> int:
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def aclose(self) -> None:
        pass


class FakePipeline:
    def __init__(self, parent: FakeAsyncRedis) -> None:
        self.parent = parent
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        def op(*args: Any) -> FakePipeline:
            self._ops.append((name, args))
            return self

        return op

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args in self._ops:
            method = getattr(self.parent, name)
            results.append(await method(*args))
        return results


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeAsyncRedis:
    client = FakeAsyncRedis()
    # tools.create_kb calls get_async_redis() at runtime to read the
    # KB snapshot from Redis. Patch the import in tools so it returns
    # the fake. kb_loader.load_kb_into_redis takes the client as a
    # parameter — no patch needed.
    monkeypatch.setattr(tools_module, "get_async_redis", lambda: client)
    reset_kb_cache()
    yield client
    reset_kb_cache()


# ── URL parsing ─────────────────────────────────────────────────────────────


def test_parse_source_urls_splits_and_strips() -> None:
    """Comma-separated config value is split and whitespace-stripped."""
    assert parse_source_urls("") == []
    assert parse_source_urls("   ") == []
    assert parse_source_urls("https://a.md") == ["https://a.md"]
    assert parse_source_urls("https://a.md, https://b.md") == [
        "https://a.md",
        "https://b.md",
    ]
    assert parse_source_urls("  https://a.md  ,, https://b.md  ") == [
        "https://a.md",
        "https://b.md",
    ]


# ── Loader: HTTP fetch + Redis write ────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_kb_into_redis_writes_docs_from_plain_url(
    fake_redis: FakeAsyncRedis,
) -> None:
    """A plain markdown body becomes one doc; manifest + per-doc key written."""

    async def fetcher(url: str) -> str:
        return "# Guide\n\nUse the grader tool to evaluate responses."

    count = await load_kb_into_redis(fake_redis, ["https://example.com/guide.md"], fetcher=fetcher)
    assert count == 1
    assert "kb:manifest" in fake_redis.sets
    manifest = fake_redis.sets["kb:manifest"]
    assert len(manifest) == 1
    doc_key = "kb:doc:" + next(iter(manifest))
    assert doc_key in fake_redis.store
    doc = json.loads(fake_redis.store[doc_key])
    assert doc["title"] == "Guide"
    assert doc["source"] == "https://example.com/guide.md"
    assert "grader" in doc["content"]


@pytest.mark.asyncio
async def test_load_kb_into_redis_handles_manifest_json(
    fake_redis: FakeAsyncRedis,
) -> None:
    """A URL returning ``{"docs": [...]}`` yields one doc per entry."""
    manifest_body = json.dumps(
        {
            "docs": [
                {
                    "url": "https://example.com/a",
                    "title": "Alpha",
                    "content": "alpha bravo",
                },
                {
                    "url": "https://example.com/b",
                    "content": "# Bravo\n\nbravo charlie",
                },
            ]
        }
    )

    async def fetcher(url: str) -> str:
        return manifest_body

    count = await load_kb_into_redis(
        fake_redis, ["https://example.com/manifest.json"], fetcher=fetcher
    )
    assert count == 2
    assert len(fake_redis.sets["kb:manifest"]) == 2


@pytest.mark.asyncio
async def test_load_kb_into_redis_clears_previous_manifest(
    fake_redis: FakeAsyncRedis,
) -> None:
    """Re-loading replaces the previous manifest (no stale entries)."""
    await fake_redis.sadd("kb:manifest", "stale-id")
    fake_redis.store["kb:doc:stale-id"] = b'{"title":"Old","source":"old","content":"old"}'

    async def fetcher(url: str) -> str:
        return "# New\n\nfresh content"

    count = await load_kb_into_redis(fake_redis, ["https://example.com/new"], fetcher=fetcher)
    assert count == 1
    assert "stale-id" not in fake_redis.sets["kb:manifest"]
    assert "kb:doc:stale-id" not in fake_redis.store


@pytest.mark.asyncio
async def test_load_kb_into_redis_swallows_fetch_errors(
    fake_redis: FakeAsyncRedis,
) -> None:
    """A failing URL is logged and skipped; the rest still load."""

    async def fetcher(url: str) -> str:
        if "bad" in url:
            raise RuntimeError("network down")
        return "# OK\n\ncontent"

    count = await load_kb_into_redis(
        fake_redis,
        ["https://example.com/bad", "https://example.com/ok"],
        fetcher=fetcher,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_load_kb_into_redis_no_op_when_no_redis() -> None:
    """With client=None, the loader is a no-op."""
    count = await load_kb_into_redis(None, ["https://example.com/x"])
    assert count == 0


@pytest.mark.asyncio
async def test_load_kb_into_redis_no_op_when_no_urls(
    fake_redis: FakeAsyncRedis,
) -> None:
    """Empty URL list writes nothing."""
    count = await load_kb_into_redis(fake_redis, [], fetcher=lambda u: "")
    assert count == 0


# ── Read path: KB snapshot from Redis ───────────────────────────────────────


@pytest.mark.asyncio
async def test_read_kb_from_redis_returns_empty_when_no_manifest(
    fake_redis: FakeAsyncRedis,
) -> None:
    assert await read_kb_from_redis(fake_redis) == []


@pytest.mark.asyncio
async def test_read_kb_from_redis_returns_empty_when_no_redis() -> None:
    assert await read_kb_from_redis(None) == []


@pytest.mark.asyncio
async def test_read_kb_from_redis_round_trip(fake_redis: FakeAsyncRedis) -> None:
    """Write via loader, read back, verify the same shape."""

    async def fetcher(url: str) -> str:
        return "# Hello\n\nworld"

    await load_kb_into_redis(fake_redis, ["https://example.com/hello"], fetcher=fetcher)
    docs = await read_kb_from_redis(fake_redis)
    assert len(docs) == 1
    assert docs[0]["title"] == "Hello"
    assert docs[0]["source"] == "https://example.com/hello"


# ── create_kb: process-local cache + Redis snapshot ─────────────────────────


@pytest.mark.asyncio
async def test_create_kb_with_explicit_documents_bypasses_redis() -> None:
    """The explicit-documents path returns a one-off KB without touching Redis."""
    kb = await create_kb(
        documents=[
            {"title": "X", "source": "x", "content": "alpha"},
        ]
    )
    assert isinstance(kb, KnowledgeBase)
    assert len(kb.documents) == 1


@pytest.mark.asyncio
async def test_create_kb_reads_from_redis_then_caches(
    fake_redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First call reads Redis; subsequent calls return the cached snapshot."""
    fetch_calls = {"n": 0}

    async def fetcher(url: str) -> str:
        fetch_calls["n"] += 1
        return "# T\n\ncontent"

    await load_kb_into_redis(fake_redis, ["https://example.com/t"], fetcher=fetcher)
    reset_kb_cache()
    kb1 = await create_kb()
    kb2 = await create_kb()
    # Same cached object on second call.
    assert kb1 is kb2
    assert len(kb1.documents) == 1


@pytest.mark.asyncio
async def test_create_kb_empty_when_redis_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no Redis, the live KB is empty (tool falls back to web)."""
    from core import redis_client

    monkeypatch.setattr(redis_client, "get_async_redis", lambda: None)
    reset_kb_cache()
    kb = await create_kb()
    assert kb.documents == []


# ── search_knowledge_base tool ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_tool_uses_redis_snapshot(
    fake_redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool reads from the Redis snapshot via create_kb."""

    async def fetcher(url: str) -> str:
        return "# Guide\n\nUse the grader tool to evaluate responses."

    await load_kb_into_redis(fake_redis, ["https://example.com/guide"], fetcher=fetcher)
    reset_kb_cache()
    out = await tools_module.search_knowledge_base.ainvoke({"query": "grader tool", "top_k": 3})
    assert out.fallback_used is False
    assert out.results
    assert out.results[0].title == "Guide"


@pytest.mark.asyncio
async def test_search_tool_falls_back_to_web_when_kb_empty(
    fake_redis: FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty KB + web search failure returns a clean no-results output."""
    reset_kb_cache()
    monkeypatch.setattr(
        tools_module, "_ddgs_text", lambda q, n: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    out = await tools_module.search_knowledge_base.ainvoke({"query": "anything", "top_k": 3})
    assert out.results == []
    assert out.fallback_used is True
    assert "web search failed" in out.reasoning


# ── Ranking (pure data, no Redis) ───────────────────────────────────────────


def test_search_ranks_relevant_doc_above_irrelevant() -> None:
    """A doc that matches more query tokens ranks higher."""
    kb = _build_kb(
        [
            {
                "title": "Cats",
                "source": "cats",
                "content": "Cats like fish and naps. They purr when content.",
            },
            {
                "title": "Dogs",
                "source": "dogs",
                "content": "Dogs fetch sticks. They bark.",
            },
        ]
    )
    results = kb.search("cats fish", top_k=2)
    assert results
    assert results[0].title == "Cats"


def test_search_zero_overlap_returns_empty() -> None:
    kb = _build_kb(
        [
            {
                "title": "Space",
                "source": "x",
                "content": "Stars and planets orbit black holes.",
            }
        ]
    )
    assert kb.search("kubernetes pod scheduling", top_k=3) == []


def test_search_dedupes_query_tokens() -> None:
    """Repeating the same query token doesn't inflate the overlap score."""
    kb = _build_kb([{"title": "X", "source": "x", "content": "alpha alpha alpha bravo"}])
    a = kb.search("alpha alpha", top_k=1)
    b = kb.search("alpha", top_k=1)
    assert a and b
    assert a[0].score == pytest.approx(b[0].score, rel=1e-3)


# ── RediSearch index + search ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_kb_index_calls_ft_create() -> None:
    """``ensure_kb_index`` issues ``FT.CREATE`` with ``IFNX`` so the
    call is idempotent across repeated boots."""
    from core.kb_loader import ensure_kb_index

    client = FakeAsyncRedis()
    client.set_exec("OK")
    await ensure_kb_index(client)


@pytest.mark.asyncio
async def test_ensure_kb_index_returns_false_without_redis() -> None:
    """No Redis → no index (the tool falls back to in-process)."""
    from core.kb_loader import ensure_kb_index

    assert await ensure_kb_index(None) is False


@pytest.mark.asyncio
async def test_search_redis_kb_returns_none_without_redis() -> None:
    """No client → ``None`` (caller falls back to in-process)."""
    from core.kb_loader import search_redis_kb

    assert await search_redis_kb(None, "query", top_k=3) is None


@pytest.mark.asyncio
async def test_search_redis_kb_returns_none_for_empty_query() -> None:
    """Empty / whitespace query → ``None`` (avoid returning random docs)."""
    from core.kb_loader import search_redis_kb

    fake = FakeAsyncRedis()
    assert await search_redis_kb(fake, "", top_k=3) is None
    assert await search_redis_kb(fake, "   ", top_k=3) is None


@pytest.mark.asyncio
async def test_search_redis_kb_parses_ft_search_response() -> None:
    """A canned ``FT.SEARCH`` reply is parsed into ``{title, source, content}``."""
    from core.kb_loader import search_redis_kb

    fake = FakeAsyncRedis()
    fake.set_exec(
        [
            2,
            b"kb:doc:abc123",
            [
                b"title",
                b"Guide",
                b"source",
                b"https://example.com/g",
                b"content",
                b"Use the grader",
            ],
            b"kb:doc:def456",
            [
                b"title",
                b"FAQ",
                b"source",
                b"https://example.com/f",
                b"content",
                b"Frequently asked",
            ],
        ]
    )
    out = await search_redis_kb(fake, "grader", top_k=5)
    assert out is not None
    assert len(out) == 2
    assert out[0]["title"] == "Guide"
    assert out[0]["source"] == "https://example.com/g"
    assert out[0]["content"] == "Use the grader"
    assert out[1]["title"] == "FAQ"


@pytest.mark.asyncio
async def test_search_redis_kb_returns_empty_on_no_matches() -> None:
    """An ``FT.SEARCH`` reply with count=0 → empty list, not None."""
    from core.kb_loader import search_redis_kb

    fake = FakeAsyncRedis()
    fake.set_exec([0])
    out = await search_redis_kb(fake, "anything", top_k=5)
    assert out == []


@pytest.mark.asyncio
async def test_search_redis_kb_returns_none_on_error() -> None:
    """Any exception (no index, no module, connection error) → ``None``."""

    class _BrokenRedis(FakeAsyncRedis):
        async def execute_command(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("no RediSearch module")

    from core.kb_loader import search_redis_kb

    out = await search_redis_kb(_BrokenRedis(), "x", top_k=3)
    assert out is None


@pytest.mark.asyncio
async def test_search_redis_kb_handles_missing_fields() -> None:
    """A doc without a ``title`` field falls back to ``"Untitled"``."""
    from core.kb_loader import search_redis_kb

    fake = FakeAsyncRedis()
    fake.set_exec(
        [
            1,
            b"kb:doc:x",
            [b"content", b"orphan doc body"],
        ]
    )
    out = await search_redis_kb(fake, "x", top_k=5)
    assert out is not None
    assert out[0]["title"] == "Untitled"
    assert out[0]["source"] == "kb"
    assert out[0]["content"] == "orphan doc body"


@pytest.mark.asyncio
async def test_search_tool_uses_redis_kb_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool calls ``FT.SEARCH`` first when Redis is available;
    the in-process KB is the fallback (not consulted when Redis
    returns results)."""
    from core import tools as tools_module
    from core.kb_loader import load_kb_into_redis

    client = FakeAsyncRedis()
    monkeypatch.setattr(tools_module, "get_async_redis", lambda: client)

    async def fetcher(url: str) -> str:
        return "# Guide\n\ncontent"

    await load_kb_into_redis(client, ["https://example.com/guide"], fetcher=fetcher)
    client.set_exec(
        [
            1,
            b"kb:doc:abc",
            [b"title", b"Guide", b"source", b"https://example.com/guide", b"content", b"content"],
        ]
    )
    reset_kb_cache()
    out = await tools_module.search_knowledge_base.ainvoke({"query": "guide", "top_k": 3})
    assert out.fallback_used is False
    assert "RediSearch" in out.reasoning
    assert out.results
    assert out.results[0].title == "Guide"
