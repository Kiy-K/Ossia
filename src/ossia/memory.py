"""Postgres-backed checkpointing and cross-session memory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

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
