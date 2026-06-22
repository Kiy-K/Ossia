"""Postgres-backed checkpointing and cross-session memory."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from deepagents.backends.utils import create_file_data
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ossia.config import Settings, get_settings

AGENT_NAMESPACE: tuple[str, ...] = ("ossia",)
"""Default agent-scoped memory namespace.

Single-tenant agent identity: every conversation shares the same memory
files. Per the docs' ``agent-scoped memory`` pattern. We do NOT use
``(user_id,)`` scoping here (no per-user isolation); add a separate
``(user_id,)`` StoreBackend route if per-user memory is needed.
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
        "I am **Ossia**, a dev-concierge support agent built on LangChain Deep Agents.\n"
        "I help engineers triage, debug, and fix issues in the Nebius / Ossia ecosystem.\n"
        "\n"
        "## How I work\n"
        "- I read the user's question carefully, then look up relevant context in the knowledge base.\n"
        "- I delegate to specialist subagents when a question needs focused work\n"
        "  (code research, bug diagnosis, fix proposals, test runs).\n"
        "- Every response goes through a grading pass; I revise up to MAX_REVISION_LOOPS times before finalizing.\n"
        "- All outbound responses are gated on human review.\n"
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
        row_factory=dict_row,
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
            saver = AsyncPostgresSaver(conn)
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
            store = AsyncPostgresStore(conn)
            await store.setup()
            yield store
    finally:
        await conn.close()


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
    existing = await store.aget(namespace, key)
    if existing is not None:
        return False
    file_data = create_file_data(content or initial_agents_memory())
    await store.aput(namespace, key, file_data)
    return True


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
