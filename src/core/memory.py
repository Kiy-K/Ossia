"""Postgres- and Redis-backed checkpointing and cross-session memory.

The ``get_checkpointer`` / ``get_store`` factories yield Postgres
backends (when ``POSTGRES_URL`` is set). The ``get_redis_checkpointer``
/ ``get_redis_store`` factories yield Redis backends from
``langgraph-checkpoint-redis`` (when ``REDIS_URL`` is set).

Selection is done by the caller (the lifespan in ``core.api`` and the
agent builder in ``core.agent``): Redis is preferred when set, then
Postgres, then in-memory. Both backends implement the same
LangGraph interfaces (``BaseCheckpointSaver`` / ``BaseStore``) so
the rest of the codebase is backend-agnostic.

Ponytail: keep both paths alive, no shared abstraction over the two
backends. Add a wrapper class only when the third backend arrives.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from deepagents.backends.utils import create_file_data
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.store.base import BaseStore, IndexConfig
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.redis.aio import AsyncRedisStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from core.config import Settings, get_settings

AGENT_NAMESPACE: tuple[str, ...] = ("ossia",)
"""Default memory namespace.

Used as the *base* for :func:`core.agent._make_memory_namespace`,
which prepends the caller hash to it for per-caller isolation
(``("ossia", "abc123def456")``) or returns it unchanged for
agent-scoped mode (``Settings.memory_scope == "agent"``).

In tests and one-off scripts where no caller is available, the
factory returns this base unchanged. Two test runs sharing the
same in-process ``InMemoryStore`` would collide on the key — tests
should construct a fresh store per test.

Changed from ``("ossia", "default")`` to ``("ossia",)`` so that
``seed_memory()`` (which writes to this namespace) is visible to
both the agent-scoped read path AND per-caller reads that fall
back to the base namespace.
"""

POLICY_NAMESPACE: tuple[str, ...] = ("ossia", "policies")
"""Org/policy namespace.

Shared across all callers (org-level scoping per the DeepAgents docs).
The ``/policies/`` route in :func:`agent._make_backend` is mounted on
this namespace AND protected by a write-deny ``FilesystemPermission``,
so application code populates it and the agent can read but never
rewrite.
"""

SCRATCH_NAMESPACE: tuple[str, ...] = ("ossia", "scratch")
"""Working-memory namespace.

Backed by a *fast* store (Redis when ``REDIS_URL`` is set, else the
in-process StateBackend) per the hybrid Redis-for-hot/Postgres-for-cold
recommendation in the memory audit. The agent uses ``/scratch/`` for
transient artifacts: last tool output, in-flight search results,
anything the agent wants to keep around for a few turns without
polluting the durable ``/memories/AGENTS.md``.

Per-caller via :func:`core.agent._make_scratch_namespace`.
"""

AGENTS_MEMORY_KEY: str = "/memories/AGENTS.md"
"""Default memory file the agent reads on startup.

Path matches what we pass to ``create_deep_agent(memory=...)`` so the
seeded value is loaded into the system prompt at boot.
"""


def initial_agents_memory() -> str:
    """Return the seed content for ``/memories/AGENTS.md``.

    Loaded at startup if the file is absent from the store. The agent
    can read and rewrite this file at runtime; the seed is a sensible
    default for a fresh deployment.
    """
    return (
        "# Ossia Agent — Identity\n"
        "\n"
        "## Who I am\n"
        "I am **Ossia**, a dev-concierge agent built on LangChain Deep Agents.\n"
        "I help engineers triage, debug, and fix code issues.\n"
        "\n"
        "## How I work\n"
        "- I read the user's question carefully, inspect any multimodal artifacts\n"
        "  (screenshots, diagrams, images), and look up relevant context with\n"
        "  search_codebase / search_knowledge_base.\n"
        "- I delegate to subagents for focused work: code-researcher,\n"
        "  bug-diagnostician, fix-proposer, test-runner, ui-debugger,\n"
        "  diagram-analyzer, visual-regression-reviewer.\n"
        "- I can run programmatic pipelines: bugfix, refactor, audit.\n"
        "- Every response goes through a grading pass; I revise up to\n"
        "  3 times before finalizing. Outbound responses are gated on approval.\n"
        "\n"
        "## What I know\n"
        "- Code research and symbol mapping\n"
        "- Bug diagnosis and root-cause analysis\n"
        "- Fix proposal and patch design\n"
        "- Test running and validation\n"
        "- Screenshot / UI debugging\n"
        "- Architecture diagram analysis\n"
        "- Visual regression comparison\n"
        "- Automated bugfix, refactor, and audit pipelines\n"
        "\n"
        "## Response style\n"
        "- Concise, technical, no fluff.\n"
        "- Code blocks where they help; otherwise prose.\n"
        "- Cite file paths and snippets when referencing code.\n"
        "\n"
        "## Things I've learned\n"
        "<!-- Update this section as you accumulate knowledge across conversations. -->\n"
    )


async def _connect(settings: Settings) -> AsyncConnection:
    """Create a configured async Postgres connection.

    Args:
        settings: Application settings.

    Returns:
        An open async connection with dict rows and autocommit enabled.
    """
    if not settings.postgres_url:
        raise ValueError("POSTGRES_URL must be set to use Postgres persistence.")
    return await AsyncConnection.connect(
        settings.postgres_url,
        row_factory=dict_row,  # type: ignore[arg-type]
        autocommit=True,
    )


@asynccontextmanager
async def get_checkpointer(
    settings: Settings | None = None,
) -> AsyncGenerator[AsyncPostgresSaver, None]:
    """Yield an initialized Postgres checkpoint saver.

    Args:
        settings: Optional settings instance; defaults to cached settings.

    Yields:
        AsyncPostgresSaver ready for use with a LangGraph graph.
    """
    settings = settings or get_settings()
    conn = await _connect(settings)
    try:
        async with conn:
            saver = AsyncPostgresSaver(conn)  # type: ignore[arg-type]
            await saver.setup()
            yield saver
    finally:
        await conn.close()


@asynccontextmanager
async def get_store(
    settings: Settings | None = None,
) -> AsyncGenerator[BaseStore, None]:
    """Yield an initialized Postgres store for cross-session memory.

    Args:
        settings: Optional settings instance; defaults to cached settings.

    Yields:
        AsyncPostgresStore ready for use as a LangGraph BaseStore.
    """
    settings = settings or get_settings()
    conn = await _connect(settings)
    try:
        async with conn:
            store = AsyncPostgresStore(conn)  # type: ignore[arg-type]
            await store.setup()
            yield store
    finally:
        await conn.close()


@asynccontextmanager
async def get_redis_checkpointer(
    settings: Settings | None = None,
) -> AsyncGenerator[AsyncRedisSaver, None]:
    """Yield an initialized Redis checkpointer (langgraph-checkpoint-redis).

    Requires ``REDIS_URL`` and a Redis server with the RedisJSON and
    RediSearch modules (Redis 8+ ships them by default; Redis Stack
    is the equivalent for older Redis). The library raises
    ``ResponseError`` on ``setup()`` if the modules are missing —
    we let it propagate so the lifespan fails fast with a clear
    message.

    Args:
        settings: Optional settings instance; defaults to cached settings.

    Yields:
        AsyncRedisSaver ready for use with a LangGraph graph.
    """
    settings = settings or get_settings()
    if not settings.redis_url:
        raise ValueError("REDIS_URL must be set to use the Redis checkpointer.")
    async with AsyncRedisSaver.from_conn_string(settings.redis_url) as saver:
        await saver.asetup()
        yield saver


@asynccontextmanager
async def get_redis_store(
    settings: Settings | None = None,
    *,
    index: IndexConfig | None = None,
) -> AsyncGenerator[AsyncRedisStore, None]:
    """Yield an initialized Redis store (langgraph-checkpoint-redis).

    When ``index`` is provided, the store is created with a RediSearch
    vector index using the supplied config. When ``index`` is
    ``None`` and ``Settings.enable_vector_index`` is ``True`` (the
    default), the index is auto-built with the local Ollama
    embedder (``Settings.embedding_model`` / ``embedding_dim``).
    Pass ``index={}`` (empty dict) to force key-value-only.

    Args:
        settings: Optional settings instance; defaults to cached settings.
        index: Explicit RediSearch index config. When ``None`` and
            vector indexing is enabled, the Ollama-backed default
            is used. When the empty dict ``{}`` is passed, no index
            is created (key-value only).

    Yields:
        AsyncRedisStore ready for use as a LangGraph BaseStore.
    """
    settings = settings or get_settings()
    if not settings.redis_url:
        raise ValueError("REDIS_URL must be set to use the Redis store.")
    resolved_index: IndexConfig | None
    if index is None and settings.enable_vector_index:
        from core.embeddings import make_ollama_embedder

        resolved_index = {  # type: ignore[assignment]
            "dims": settings.embedding_dim,
            "embed": make_ollama_embedder(settings),
        }
    else:
        resolved_index = index
    async with AsyncRedisStore.from_conn_string(settings.redis_url, index=resolved_index) as store:
        await store.setup()
        yield store


async def seed_memory(
    store: BaseStore,
    *,
    namespace: tuple[str, ...] = AGENT_NAMESPACE,
    key: str = AGENTS_MEMORY_KEY,
    content: str | None = None,
) -> bool:
    """Seed a memory file in the store if it does not already exist.

    Used at agent startup to bootstrap ``/memories/AGENTS.md`` for fresh
    deployments. The seed is only written if the key is absent; the
    agent's own ``edit_file`` writes are never overwritten by a re-seed.

    Args:
        store: The LangGraph ``BaseStore`` to seed.
        namespace: Memory namespace (default: agent-scoped).
        key: Memory file path (default: ``/memories/AGENTS.md``).
        content: Optional override for the seed body. Defaults to
            :func:`initial_agents_memory`.

    Returns:
        True when a new file was created, False when the key already
        existed (idempotent re-seed).
    """
    from core.cache import redis_lock

    # Surface #4: serialize the get-then-put critical section via a
    # short Redis lock. Two concurrent first boots both see "absent"
    # otherwise and both write; lock collapses them to a single
    # writer. When Redis is unset, the lock is a no-op and we fall
    # back to last-write-wins (the previous behavior).
    async with redis_lock("seed_memory", *namespace, key):
        existing = await store.aget(namespace, key)
        if existing is not None:
            return False
        file_data = create_file_data(content or initial_agents_memory())
        await store.aput(namespace, key, file_data)  # type: ignore[arg-type]
        return True


async def seed_policy(
    store: BaseStore,
    key: str,
    content: str,
) -> bool:
    """Seed a read-only policy file in :data:`POLICY_NAMESPACE`.

    Companion to :func:`seed_memory` for the ``/policies/`` route, which
    is mounted on :data:`POLICY_NAMESPACE` and protected by a write-deny
    ``FilesystemPermission``. Application code populates these files at
    startup; the agent can read them via ``read_file`` but cannot edit
    or delete them.

    Args:
        store: The LangGraph ``BaseStore`` to seed.
        key: Policy file path, e.g. ``"/policies/compliance.md"``.
        content: Policy body.

    Returns:
        True when a new file was created, False when the key already
        existed.
    """
    existing = await store.aget(POLICY_NAMESPACE, key)
    if existing is not None:
        return False
    await store.aput(POLICY_NAMESPACE, key, create_file_data(content))  # type: ignore[arg-type]
    return True


async def ensure_caller_memory_seeded(
    store: BaseStore | None,
    caller: str,
) -> None:
    """Ensure the caller's per-caller memory namespace is seeded.

    Called on each authenticated request before the agent runs. Seeds
    the caller's namespace ``("ossia", caller)`` with the initial
    ``/memories/AGENTS.md`` if and only if the key does not already
    exist (idempotent). When the store is ``None`` (in-process test
    builds) this is a no-op.

    The startup ``seed_memory()`` call seeds the base namespace
    ``("ossia",)``; this function seeds the per-caller namespace so
    the agent's user-scoped backend finds the memory file on the
    first request from each caller.

    Args:
        store: The agent's ``BaseStore``. ``None`` is a no-op.
        caller: The current caller's hash.
    """
    if store is None:
        return
    await seed_memory(store, namespace=("ossia", caller))


def read_memory_item(item: Any) -> str:
    """Best-effort decode a BaseStore item into a string.

    The store keeps memory files as ``FileData`` dicts (see
    ``deepagents.backends.utils.create_file_data``); this helper is
    used by tests and the FastAPI app to render memory contents.
    """
    if item is None:
        return ""
    value = item.value if hasattr(item, "value") else item
    if isinstance(value, dict):
        content = value.get("content", "")
        if isinstance(content, list):
            return "\n".join(str(line) for line in content)
        if isinstance(content, bytes):
            return content.decode("utf-8", errors="replace")
        return str(content)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, indent=2)
