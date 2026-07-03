"""Ollama-backed embedding function for the Redis vector index.

The :func:`make_ollama_embedder` factory returns an async
``AEmbeddingsFunc`` that satisfies the ``embed`` field of
:data:`langgraph.store.base.IndexConfig`. It calls Ollama's
``POST /api/embeddings`` endpoint, which is available on any
Ollama server (default ``http://localhost:11434``).

Ponytail: one function, no batching, no retries. The Ollama
endpoint is local and fast; if a single call fails, the agent's
``store.put`` raises and the upstream caller surfaces the error.
Wrap in retry middleware when the Ollama server is on a remote
host with flaky network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import httpx

from core.config import Settings, get_settings

# Type alias matching langgraph.store.base.IndexConfig.embed.
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


async def _ollama_embed_one(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    text: str,
) -> list[float]:
    """Single-text embed call to Ollama. Ponytail: not batched —
    the simple endpoint takes one prompt at a time."""
    response = await client.post(
        f"{base_url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=30.0,
    )
    response.raise_for_status()
    return cast(list[float], response.json()["embedding"])


def make_ollama_embedder(settings: Settings | None = None) -> Embedder:
    """Return an async embedder backed by the configured Ollama server.

    The returned function takes a list of texts and returns a list
    of float vectors. Calls fire concurrently via ``asyncio.gather``
    so batched calls are roughly as fast as one.
    """
    settings = settings or get_settings()

    async def embed(texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient() as client:
            vectors = await asyncio.gather(
                *[
                    _ollama_embed_one(
                        client,
                        settings.ollama_base_url,
                        settings.embedding_model,
                        text,
                    )
                    for text in texts
                ]
            )
        return vectors

    return embed
