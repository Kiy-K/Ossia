"""Knowledge base loader.

Fetches documents from URLs configured via ``Settings.kb_source_urls``
and stores them in Redis (when ``REDIS_URL`` is set). Each URL = one
document. The :func:`search_knowledge_base` tool reads from Redis and
runs an in-process ranking; this module is the write path only.

Two URL shapes are supported, auto-detected by content type:

- Plain markdown body (text/plain or text/markdown) — the URL itself
  is the source; the body becomes the content; title is the first H1
  heading (or the URL stem if none).
- JSON manifest (``{"docs": [{"url":..., "content":...}, ...]}``) —
  each entry becomes one document; ``url`` is the source; ``content``
  is the body; title is the first H1 of the content.

Ponytail: one fetch + one Redis write per URL. No retries, no
rate limiting, no caching. Add ``httpx.AsyncClient(retries=...)`` when
the source is flaky.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Redis key layout. Ponytail: a single SET enumerates doc IDs; each
# doc lives at its own hash. No indices, no auxiliary structures —
# the tool reads all docs and ranks in-process. Replace with
# RediSearch/Iris when the corpus exceeds a few hundred docs.
_MANIFEST_KEY = "kb:manifest"
_DOC_KEY_PREFIX = "kb:doc:"


# Type alias for the HTTP fetcher. ``fetcher(url)`` returns the raw
# response body as ``str``. Tests pass a mock; production uses
# ``httpx.get``.
Fetcher = Callable[[str], Awaitable[str]]


def parse_source_urls(raw: str) -> list[str]:
    """Split a comma-separated config value into a clean URL list.

    Strips whitespace, drops empty entries, preserves order. Returns
    ``[]`` when the input is empty or only whitespace.
    """
    return [u.strip() for u in raw.split(",") if u.strip()]


def _doc_id(url: str) -> str:
    """Stable per-URL id used as the Redis key suffix."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _title_from_body(url: str, body: str) -> str:
    """First H1 in the body, or the URL's last path segment as fallback."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    # Fallback: last non-empty path segment, or the URL itself.
    path = url.rsplit("/", 1)[-1]
    return path.rsplit(".", 1)[0] or url


async def _fetch_one(url: str, fetcher: Fetcher) -> list[dict[str, str]]:
    """Fetch ``url`` and return one or more doc dicts.

    A markdown body yields one doc. A JSON manifest yields N docs
    (one per entry). Failures yield zero docs and are logged.
    """
    try:
        body = await fetcher(url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KB fetch failed for %s: %s", url, exc)
        return []
    stripped = body.lstrip()
    if stripped.startswith("{") and '"docs"' in stripped[:200]:
        return _parse_manifest(url, body)
    return [
        {
            "title": _title_from_body(url, body),
            "source": url,
            "content": body,
        }
    ]


def _parse_manifest(url: str, body: str) -> list[dict[str, str]]:
    """Parse ``{"docs": [{"url":..., "content":...}, ...]}``.

    Missing fields fall back to sensible defaults; malformed entries
    are skipped with a warning.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("KB manifest JSON invalid for %s: %s", url, exc)
        return []
    entries = data.get("docs") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content", "")
        if not content:
            continue
        entry_url = entry.get("url", url)
        title = entry.get("title") or _title_from_body(entry_url, content)
        out.append({"title": title, "source": entry_url, "content": content})
    return out


async def load_kb_into_redis(
    client: aioredis.Redis | None,
    urls: list[str],
    *,
    fetcher: Fetcher | None = None,
) -> int:
    """Fetch each URL and write docs into Redis under the KB namespace.

    Clears any previous manifest, writes one hash per doc, and re-adds
    the doc IDs to the manifest set. Returns the number of docs
    successfully written.

    When ``client`` is ``None`` (no Redis), returns 0 and does nothing.
    """
    if client is None or not urls:
        return 0
    if fetcher is None:

        async def default_fetcher(url: str) -> str:
            response = httpx.get(url, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
            return response.text

        fetcher = default_fetcher

    all_docs: list[dict[str, str]] = []
    for url in urls:
        docs = await _fetch_one(url, fetcher)
        all_docs.extend(docs)

    # Ensure the RediSearch index exists BEFORE writing the docs
    # so writes auto-populate the index. IFNX makes this idempotent
    # across repeated boot loads.
    await ensure_kb_index(client)

    # Read old doc IDs so we can delete their per-doc keys after the
    # manifest is gone. The pipeline is one round trip.
    old_ids: set[str] = set()
    raw_old = await client.smembers(_MANIFEST_KEY)  # type: ignore[misc]
    for raw in raw_old:
        old_ids.add(raw.decode() if isinstance(raw, bytes) else str(raw))

    pipe = client.pipeline()
    pipe.delete(_MANIFEST_KEY)
    for old_id in old_ids:
        pipe.delete(_DOC_KEY_PREFIX + old_id)
    for doc in all_docs:
        doc_id = _doc_id(doc["source"])
        key = _DOC_KEY_PREFIX + doc_id
        pipe.set(key, json.dumps(doc).encode("utf-8"))
        pipe.sadd(_MANIFEST_KEY, doc_id)
    await pipe.execute()
    logger.info(
        "Loaded %d KB doc(s) from %d URL(s); cleared %d stale doc(s)",
        len(all_docs),
        len(urls),
        len(old_ids),
    )
    return len(all_docs)


async def read_kb_from_redis(client: aioredis.Redis | None) -> list[dict[str, Any]]:
    """Read all KB docs from Redis. Returns ``[]`` when client is None
    or the manifest is empty.
    """
    if client is None:
        return []
    ids: set[bytes] = await client.smembers(_MANIFEST_KEY)  # type: ignore[misc]
    if not ids:
        return []
    pipe = client.pipeline()
    for doc_id in ids:
        suffix = doc_id.decode() if isinstance(doc_id, bytes) else str(doc_id)
        pipe.get(_DOC_KEY_PREFIX + suffix)
    raw_docs = await pipe.execute()
    docs: list[dict[str, Any]] = []
    for raw in raw_docs:
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "content" in data:
            docs.append(data)
    return docs


# ── RediSearch index over the KB ────────────────────────────────────────────
# Ponytail: one index, two fields (title weighted 2x, content 1x), one
# RETURN clause. The search path falls back to in-process ranking
# when RediSearch is unavailable — no error path on the read side.

_KB_INDEX = "kb:idx"


async def ensure_kb_index(client: aioredis.Redis | None) -> bool:
    """Create the ``kb:idx`` RediSearch index over ``kb:doc:*`` hashes.

    Idempotent via ``IFNX``: re-creating an existing index is a
    no-op. Returns ``True`` when the index was newly created,
    ``False`` when it already existed, ``False`` when the client
    is ``None`` (no Redis). Errors propagate — the caller can
    decide whether the KB is usable without a server-side index.
    """
    if client is None:
        return False
    await client.execute_command(  # type: ignore[no-untyped-call]
        "FT.CREATE",
        _KB_INDEX,
        "ON",
        "HASH",
        "PREFIX",
        "1",
        _DOC_KEY_PREFIX,
        "SCHEMA",
        "title",
        "TEXT",
        "WEIGHT",
        "2.0",
        "content",
        "TEXT",
        "IFNX",
    )
    return True


def _parse_ft_search_result(raw: Any) -> list[dict[str, str]]:
    """Parse ``FT.SEARCH`` reply into a list of ``{title, source, content}``.

    ``FT.SEARCH`` returns ``[count, key1, [f1, v1, f2, v2, ...], key2, ...]``.
    Values are bytes (the client is configured with
    ``decode_responses=False``); we decode on the way out.
    """
    if not raw:
        return []
    if int(raw[0]) == 0:
        return []
    out: list[dict[str, str]] = []
    i = 1
    while i < len(raw):
        key = raw[i]
        if isinstance(key, bytes):
            key = key.decode("utf-8", errors="replace")
        fields = raw[i + 1] if i + 1 < len(raw) else []
        i += 2
        field_dict: dict[str, str] = {}
        for j in range(0, len(fields), 2):
            fname = fields[j]
            fval = fields[j + 1] if j + 1 < len(fields) else b""
            if isinstance(fname, bytes):
                fname = fname.decode("utf-8", errors="replace")
            if isinstance(fval, bytes):
                fval = fval.decode("utf-8", errors="replace")
            field_dict[fname] = fval
        out.append(
            {
                "key": key,
                "title": field_dict.get("title", "Untitled"),
                "source": field_dict.get("source", "kb"),
                "content": field_dict.get("content", "")[:800],
            }
        )
    return out


async def search_redis_kb(
    client: aioredis.Redis | None,
    query: str,
    top_k: int = 3,
) -> list[dict[str, str]] | None:
    """Run ``FT.SEARCH`` against the KB index. Returns parsed
    results, or ``None`` when the index isn't available (caller
    falls back to in-process ranking).

    The result order is RediSearch's relevance score (TF-IDF
    by default). When the query is empty, the search returns
    the most recently indexed docs — useful for "list all"
    semantics but not the intended use here.
    """
    if client is None or not query.strip():
        return None
    try:
        raw = await client.execute_command(  # type: ignore[no-untyped-call]
            "FT.SEARCH",
            _KB_INDEX,
            query,
            "LIMIT",
            "0",
            str(top_k),
            "RETURN",
            "3",
            "title",
            "source",
            "content",
        )
    except Exception:  # noqa: BLE001
        # Index missing, RediSearch module not loaded, connection
        # error — caller falls back to in-process search.
        return None
    return _parse_ft_search_result(raw)


def reset_kb_cache() -> None:
    """Clear the in-process KB cache. Test-only helper."""
    from core import tools as _tools  # local import to avoid cycle

    _tools._kb_cache = None
