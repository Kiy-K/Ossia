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

from core.config import Settings, get_settings

AGENT_NAMESPACE: tuple[str, ...] = ("ossia", "default")
"""Default agent-scoped memory namespace.

Single-tenant fallback used when no caller context is available (e.g.,
tests, one-off scripts). In production the ``_make_backend`` namespace
factory prepends the ``user_id`` (caller hash) to create a per-user
namespace like ``("ossia", "abc123def456")`` so memory files never bleed
between authenticated callers.

See :func:`agent._make_memory_namespace` for the production path.
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
