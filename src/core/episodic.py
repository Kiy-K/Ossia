"""Episodic memory tool.

Episodic memory is "records of past experiences: what happened, in what
order, and what the outcome was. Unlike semantic memory (facts and
preferences stored in files like ``AGENTS.md``), episodic memory
preserves the full conversational context so the agent can recall
*how* a problem was solved, not just *what* was learned from it."
(Deep Agents memory docs.)

The supported primitive on a bare ``BaseCheckpointSaver`` is
**per-thread turn recall** (``checkpointer.list(config_with_thread_id)``).
This module implements two tools:

- ``recall_thread_turns`` — the per-thread primitive, given a
  ``thread_id`` returns recent turns.
- ``search_threads`` — cross-thread content search, the missing
  primitive the LangGraph SDK fills with ``client.threads.search`` for
  managed deployments. Implemented here as a Postgres ILIKE query on
  the ``checkpoints`` table, scoped to the current caller.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from psycopg import AsyncConnection

from core.config import Settings
from core.memory import _connect
from core.request_context import caller_var

_DEFAULT_LIMIT = 5
_SNIPPET_CHARS = 240

# Type alias for the cross-thread search backend. Returns one row per
# matching thread: ``{"thread_id": str, "snippet": str}``. The tool
# layer filters by the current caller's namespace prefix.
SearchFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


def _messages_from_checkpoint(checkpoint: Any) -> list[Any]:
    """Pull the messages channel from a checkpoint."""
    if not isinstance(checkpoint, dict):
        return []
    values = checkpoint.get("channel_values", {}) or {}
    return list(values.get("messages", []) or [])


def _summarize_turns(turns: list[Any]) -> list[dict[str, Any]]:
    """Convert raw messages to a compact ``{role, content}`` shape.

    Roles are normalized to the closed set used by the public API's
    ``ChatMessage`` schema: ``user``, ``assistant``, ``tool``, ``system``.
    LangChain's ``HumanMessage.type == "human"`` and ``AIMessage.type
    == "ai"`` are mapped to the API's vocabulary.
    """
    out: list[dict[str, Any]] = []
    for m in turns:
        role = getattr(m, "type", None) or "unknown"
        if role in {"human", "user"}:
            role = "user"
        elif role in {"ai", "assistant"}:
            role = "assistant"
        elif role in {"tool", "function"}:
            role = "tool"
        elif role in {"system"}:
            role = "system"
        content = str(getattr(m, "content", ""))[:_SNIPPET_CHARS]
        out.append({"role": role, "content": content})
    return out


def _extract_snippet(ckpt_text: str, query: str, span: int = _SNIPPET_CHARS) -> str:
    """Return a window of text around the first match of ``query``."""
    idx = ckpt_text.lower().find(query.lower())
    if idx < 0:
        return ckpt_text[:span]
    start = max(0, idx - span // 2)
    end = min(len(ckpt_text), idx + len(query) + span // 2)
    return ckpt_text[start:end]


def make_episodic_recall_tool(
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> BaseTool | None:
    """Build a ``recall_thread_turns`` tool, or ``None`` if no
    checkpointer is configured.

    The tool is a no-op (returns ``None``) when the agent runs without
    persistence — e.g. one-off scripts, ephemeral test setups. With a
    checkpointer it returns a LangChain tool that the agent can call
    to recall recent turns of a specific thread.

    Args:
        checkpointer: The agent's checkpointer. ``None`` (no
            persistence configured) yields ``None`` so the caller
            skips wiring the tool.

    Returns:
        A LangChain tool or ``None``.
    """
    if checkpointer is None:
        return None

    @tool
    async def recall_thread_turns(
        thread_id: str,
        limit: int = _DEFAULT_LIMIT,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Return the most recent turns of a thread.

        Args:
            thread_id: The thread to recall. Pass the current
                thread_id to recall this conversation; pass a
                previously-used thread_id to recall across sessions.
            limit: Maximum number of past checkpoints to surface
                (most recent first).
            runtime: Injected by deepagents; not part of the model's
                argument schema. Carries ``runtime.store`` (the agent's
                ``BaseStore``) and ``runtime.context`` (the
                ``OssiaContext``).

        Returns:
            A dict ``{"thread_id": str, "turns": [{role, content}, ...]}``.
            Empty ``turns`` when the thread has no recorded
            checkpoints.
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        try:
            # Use ``anyio.to_thread.run_sync`` to run the checkpointer's
            # sync ``list()`` method on a thread-pool worker. This
            # avoids the ``InvalidStateError`` that ``AsyncPostgresSaver``
            # raises when its sync methods are called from the event-loop
            # thread. It also works with purely sync checkpointers like
            # ``InMemorySaver`` — they simply run on the thread pool.
            raw = await anyio.to_thread.run_sync(  # pyright: ignore[reportAttributeAccessIssue]
                lambda: list(checkpointer.list(config, limit=limit))  # type: ignore[union-attr]
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "thread_id": thread_id,
                "turns": [],
                "error": f"recall failed: {exc!r}",
            }
        # ``list`` returns newest-first; reverse to chronological for
        # human/LLM readability.
        raw.reverse()
        turns: list[dict[str, Any]] = []
        for tup in raw[-limit:]:
            turns.extend(_summarize_turns(_messages_from_checkpoint(tup.checkpoint)))
        return {"thread_id": thread_id, "turns": turns}

    return recall_thread_turns


async def _postgres_search_threads(
    settings: Settings,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Search the Postgres ``checkpoints`` table for the current caller's
    threads whose latest checkpoint blob contains ``query`` (case-insensitive).

    Ponytail: one ILIKE on the JSON blob. Add a real index on
    ``(thread_id, checkpoint_id)`` and a tsvector column when traffic
    warrants; switch to Redis-Iris ANN search when the corpus grows past
    a few thousand threads.
    """
    caller = caller_var.get()
    if not caller:
        return []
    prefix = f"{caller}:"
    # ponytail: opens a fresh psycopg connection per call. Wrap in
    # psycopg_pool.AsyncConnectionPool when search_threads becomes hot.
    conn: AsyncConnection = await _connect(settings)
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT ON (thread_id) thread_id, checkpoint::text "
                "FROM checkpoints "
                "WHERE thread_id LIKE %s AND checkpoint::text ILIKE %s "
                "ORDER BY thread_id, checkpoint_id DESC",
                (prefix + "%", f"%{query}%"),
            )
            rows = await cur.fetchall()
    finally:
        await conn.close()
    return [{"thread_id": r[0], "snippet": _extract_snippet(r[1], query)} for r in rows[:limit]]


def make_postgres_search_fn(settings: Settings) -> SearchFn | None:
    """Build a Postgres-backed :data:`SearchFn`, or ``None`` when no
    ``POSTGRES_URL`` is configured.
    """
    if not settings.postgres_url:
        return None

    async def search_fn(query: str, limit: int) -> list[dict[str, Any]]:
        return await _postgres_search_threads(settings, query, limit)

    return search_fn


def make_search_threads_tool(search_fn: SearchFn | None) -> BaseTool | None:
    """Build a ``search_threads`` tool, or ``None`` if no search backend
    is configured.

    The tool is caller-scoped: the ``search_fn`` is expected to honor
    the current caller's thread prefix, and the tool applies a second
    filter pass as defense-in-depth so a misbehaving backend cannot leak
    another caller's threads.
    """
    if search_fn is None:
        return None

    @tool
    async def search_threads(
        query: str,
        limit: int = _DEFAULT_LIMIT,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Search past threads by content keyword.

        Returns the most recent matching threads for the current
        caller, with a snippet of the matched text. Use to recall how
        a previous problem was solved across sessions when you do not
        know the ``thread_id`` ahead of time.

        Args:
            query: Keyword or phrase to search for (case-insensitive
                substring match).
            limit: Maximum number of matching threads to return.
            runtime: Injected by deepagents; not part of the model's
                argument schema. Carries ``runtime.store`` (the agent's
                ``BaseStore``) and ``runtime.context`` (the
                ``OssiaContext``).

        Returns:
            A dict ``{"threads": [{"thread_id": str, "snippet": str}, ...]}``.
            Empty list when no caller context is set or no matches.
        """
        caller = caller_var.get()
        if not caller:
            return {"threads": []}
        results = await search_fn(query, limit)
        scoped = [r for r in results if str(r.get("thread_id", "")).startswith(f"{caller}:")]
        return {"threads": scoped[:limit]}

    return search_threads


# ── semantic_recall ─────────────────────────────────────────────────────────
# Cross-thread semantic search over the store's vector index. Mirrors
# `make_episodic_recall_tool`: a factory that returns a tool or
# ``None`` when the store does not support vector search.
#
# When ``REDIS_URL`` is set and ``enable_vector_index=True`` (the
# default), the agent's store is `AsyncRedisStore` with an Ollama
# embedder. The tool embeds the query and calls ``asearch`` with
# the vector — sub-ms server-side ranking, scoped to the caller's
# namespace.
#
# Returns ``None`` for non-Redis stores (Postgres, in-memory) or
# when vector indexing is disabled. The caller skips wiring the
# tool; the agent just doesn't have semantic recall in that mode.


def make_semantic_recall_tool(
    store: BaseStore | None,
    settings: Settings,
) -> BaseTool | None:
    """Build a ``semantic_recall`` tool, or ``None`` when the store
    does not support vector search.

    The tool is caller-scoped: it searches the caller's namespace
    in the store (``("ossia", <caller>)``) so one user's queries
    cannot surface another user's content. Defense in depth on
    top of the store's own namespace isolation.

    Args:
        store: The agent's ``BaseStore``. When ``None`` (in-memory
            setup) or not a Redis store, returns ``None``.
        settings: Application settings. The store's vector index
            uses the configured Ollama embedder internally; we
            do not embed here.

    Returns:
        A LangChain tool, or ``None``.
    """
    from langgraph.store.redis.aio import AsyncRedisStore

    if not isinstance(store, AsyncRedisStore):
        return None
    if not settings.enable_vector_index:
        return None

    @tool
    async def semantic_recall(
        query: str,
        top_k: int = 5,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Semantically search cross-thread memory for similar content.

        Use this when the user asks a question that might have been
        answered in a previous conversation but you don't know the
        ``thread_id``. The store's vector index embeds the query
        via the local Ollama model and returns the most semantically
        similar items the agent wrote to memory.

        Args:
            query: Natural-language query to search for.
            top_k: Maximum number of similar items to return.
            runtime: Injected by deepagents; not part of the model's
                argument schema. Carries ``runtime.store`` (the agent's
                ``BaseStore``) and ``runtime.context`` (the
                ``OssiaContext``).

        Returns:
            A dict ``{"matches": [{"key": str, "namespace": list, "value": dict, "score": float}, ...]}``.
            Empty list when the caller context is not set or no
            matches are found above the index threshold.
        """
        caller = caller_var.get() or "default"
        # Read the store from the injected runtime, falling back to
        # the closure for backward compatibility (tests that call
        # ``tool.ainvoke`` directly without a runtime).
        _store = getattr(runtime, "store", None) if runtime is not None else None
        if _store is None:
            _store = store
        try:
            # The store embeds the query internally using the
            # IndexConfig's embedder (Ollama via the integration).
            # We do NOT embed here — passing the raw text lets
            # the library cache the embedding per-query and use
            # the same backend the index was built with.
            results = await _store.asearch(
                ("ossia", caller),
                query=query,
                limit=top_k,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "matches": [],
                "error": f"vector search failed: {exc!r}",
            }
        return {
            "matches": [
                {
                    "key": item.key,
                    "namespace": list(item.namespace),
                    "value": item.value,
                    "score": getattr(item, "score", None),
                }
                for item in results
            ]
        }

    return semantic_recall
