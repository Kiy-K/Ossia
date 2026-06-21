"""Postgres-backed checkpointing and cross-session memory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ossia.config import Settings, get_settings


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


class PostgresMemoryStore:
    """Cross-session memory store backed by a LangGraph BaseStore.

    Exposes simple get/put semantics for user-scoped values such as preferences
    and previous conversation summaries.
    """

    def __init__(self, store: BaseStore) -> None:
        """Initialize with a configured BaseStore.

        Args:
            store: A LangGraph BaseStore (e.g., AsyncPostgresStore).
        """
        self._store = store

    async def get(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        """Retrieve a stored value.

        Args:
            namespace: Hierarchical namespace tuple.
            key: Item key.

        Returns:
            Stored value dict, or None when absent.
        """
        item = await self._store.aget(namespace, key)
        if item is None:
            return None
        return item.value

    async def put(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
    ) -> None:
        """Store a value.

        Args:
            namespace: Hierarchical namespace tuple.
            key: Item key.
            value: JSON-serializable value to store.
        """
        await self._store.aput(namespace, key, value)

    async def search(
        self,
        namespace_prefix: tuple[str, ...],
        query: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Semantic search over stored values in a namespace.

        Args:
            namespace_prefix: Namespace prefix to search within.
            query: Optional search query.
            limit: Maximum number of results.

        Returns:
            List of stored value dicts.
        """
        items = await self._store.asearch(namespace_prefix, query=query, limit=limit)
        return [item.value for item in items]
