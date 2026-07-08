"""Mem0-based long-term memory tools for the Ossia agent.

Per ADR-0016, Mem0 integrates as a **tool layer**, not as a DeepAgents
`Backend`. The ``Backend`` protocol is a virtual filesystem; Mem0's API is
semantic memory CRUD+search. The two interfaces are incompatible, and
adapting one to the other would discard semantic search — the entire point
of using Mem0. Instead, wrap Mem0 in ``@tool``-decorated functions and pass
them via ``tools=`` to ``create_deep_agent``.

Storage configuration:

- **Vector store**: pgvector on the existing Postgres instance (``PGVectorConfig``).
  No separate vector database (Qdrant, Chroma, etc.).
- **LLM**: Same provider/model as the main agent (OpenRouter by default).
  Mem0 uses this for fact extraction. No OpenAI defaults.
- **Embedder**: Local Ollama server (``Settings.embedding_model``, default
  ``embeddinggemma``). Mem0 uses this for vector embeddings. No OpenAI defaults.
- **Graph store**: Excluded for v1 (no Neo4j). Mem0 works as a pure
  vector-memory system.
- **Redis**: Stays out of Mem0's storage path entirely.

The ``Memory`` instance is lazily initialized on first tool call so a
misconfigured Mem0 setup doesn't block agent startup.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Module-level singleton — lazily built on first tool call.
_memory: Any = None
_mem0_config_error: str | None = None


class SearchMemoryInput(BaseModel):
    """Input for searching long-term memory."""

    query: str = Field(description="Natural-language query to search stored memories.")
    user_id: str = Field(
        description=(
            "User identifier scoping the search. Use the caller's "
            "stable hash (the value from the whoami endpoint) or a "
            "project-specific id."
        ),
    )


class AddMemoryInput(BaseModel):
    """Input for adding a fact to long-term memory."""

    content: str = Field(description="Fact or memory to store, in natural language.")
    user_id: str = Field(
        description=(
            "User identifier scoping the memory. Use the caller's "
            "stable hash (the value from the whoami endpoint) or a "
            "project-specific id."
        ),
    )


def _parse_postgres_url(url: str) -> dict[str, Any]:
    """Parse a ``postgresql://...`` URL into Mem0 ``PGVectorConfig`` kwargs.

    Returns an empty dict when the URL is not parseable so the caller can
    fall back to no Mem0 initialization.
    """
    m = re.match(
        r"postgres(?:ql)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(\S+)",
        url,
    )
    if not m:
        return {}
    user, password, host, port_str, dbname = m.groups()
    port = int(port_str) if port_str else 5432
    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
    }


def _build_mem0_config(settings: Any) -> dict[str, Any] | None:
    """Build a Mem0 ``MemoryConfig``-compatible dict from Ossia settings.

    Returns ``None`` when a required component is not configured
    (``POSTGRES_URL`` or ``OLLAMA_BASE_URL``) — Mem0 degrades
    cleanly to not-loaded.
    """
    pg_kwargs = _parse_postgres_url(settings.postgres_url or "")
    if not pg_kwargs:
        logger.debug("Mem0 not configured: POSTGRES_URL missing or unparseable")
        return None

    ollama_base = getattr(settings, "ollama_base_url", None) or os.environ.get(
        "OLLAMA_BASE_URL", "http://localhost:11434"
    )

    # Embedding dims from Settings, defaulting to what embeddinggemma uses.
    embedding_dims = getattr(settings, "embedding_dim", 768)

    # LLM config: use the main agent's provider/model, exposed to Mem0
    # as an OpenAI-compatible endpoint. OpenRouter, Nebius, and NIM all
    # speak the OpenAI chat protocol.
    provider = str(getattr(settings, "provider", "openrouter"))
    model = str(getattr(settings, "model", "openai/gpt-4o-mini"))

    # Determine the API key and base URL for the LLM based on provider.
    if provider == "openrouter":
        api_key = getattr(settings, "openrouter_api_key", None) or os.environ.get(
            "OPENROUTER_API_KEY", ""
        )
        base_url = "https://openrouter.ai/api/v1"
    elif provider == "openai":
        api_key = getattr(settings, "openai_api_key", None) or os.environ.get("OPENAI_API_KEY", "")
        base_url = "https://api.openai.com/v1"
    elif provider == "nebius":
        api_key = getattr(settings, "nebius_api_key", None) or os.environ.get(
            "NEBIUS_API_KEY", ""
        )
        base_url = "https://api.studio.nebius.com/v1"
    elif provider == "nim":
        api_key = getattr(settings, "nim_api_key", None) or os.environ.get(
            "NVIDIA_API_KEY", ""
        )
        base_url = getattr(settings, "nim_base_url", None) or os.environ.get(
            "NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
        )
    else:
        # For Anthropic, Google, etc. — Mem0 v2 uses OpenAI-compatible under
        # the hood for the LLM path. The main agent's provider isn't
        # OpenAI-compatible, so skip LLM-based fact extraction (Mem0 will
        # fall back to store-as-is mode without the LLM).
        api_key = ""
        base_url = None

    config: dict[str, Any] = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                **pg_kwargs,
                "collection_name": "mem0",
                "embedding_model_dims": embedding_dims,
            },
        },
        "embedder": {
            "provider": "ollama",
            "config": {
                "model": getattr(settings, "embedding_model", "embeddinggemma"),
                "ollama_base_url": ollama_base,
                "embedding_dims": embedding_dims,
            },
        },
        "history_db_path": os.path.join(
            os.environ.get("MEM0_DIR", os.path.expanduser("~/.mem0")),
            "history.db",
        ),
        "version": "v1.1",
    }

    if base_url:
        config["llm"] = {
            "provider": "openai",
            "config": {
                "model": model,
                "api_key": api_key,
                "openai_base_url": base_url,
            },
        }
    else:
        # No OpenAI-compatible base URL — skip the LLM. Mem0 stores
        # memories as-is without fact extraction. Ponytail: v1
        # limitation; add provider-specific LLM backends as Mem0
        # adds native support.
        config["llm"] = {
            "provider": "openai",
            "config": {
                "model": model,
                "api_key": api_key or "",
            },
        }
        logger.info(
            "Mem0 LLM: provider '%s' is not OpenAI-compatible; "
            "Mem0 will store memories without LLM-based fact extraction."
        )

    return config


def _get_memory() -> Any | None:
    """Return the module-level Mem0 ``Memory`` instance, building it lazily.

    Returns ``None`` when Mem0 is not configured or initialization fails.
    The error is logged once and then memoized so subsequent tool calls
    also get ``None`` without repeated init attempts.
    """
    global _memory, _mem0_config_error

    if _memory is not None:
        return _memory
    if _mem0_config_error is not None:
        return None

    try:
        from core.config import get_settings

        settings = get_settings()
        config = _build_mem0_config(settings)
        if config is None:
            _mem0_config_error = "Mem0 not configured (POSTGRES_URL required)"
            return None

        from mem0 import Memory

        _memory = Memory.from_config(config)
        logger.info("Mem0 initialized with pgvector store")
        return _memory
    except Exception as exc:  # noqa: BLE001
        _mem0_config_error = f"Mem0 initialization failed: {exc}"
        logger.warning("Mem0 init failed: %s; memory tools will be unavailable", exc)
        return None


@tool(args_schema=SearchMemoryInput)
def search_memory(query: str, user_id: str) -> str:
    """Search stored long-term memory for relevant facts about a user or project.

    Use this when you need to recall past decisions, preferences, or context
    that may have been stored across previous sessions. Returns a JSON list
    of matching memories with relevance scores.

    Args:
        query: Natural-language query describing what to recall.
        user_id: User identifier scoping the search.

    Returns:
        JSON string with matching memories, or an error message.
    """
    memory = _get_memory()
    if memory is None:
        return json.dumps({
            "error": "Mem0 is not configured. Set POSTGRES_URL and OLLAMA_BASE_URL to enable long-term memory.",
            "results": [],
        })
    try:
        results = memory.search(query, user_id=user_id)
        return json.dumps(results, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mem0 search failed: %s", exc)
        return json.dumps({"error": str(exc), "results": []})


@tool(args_schema=AddMemoryInput)
def add_memory(content: str, user_id: str) -> str:
    """Store a new fact, decision, or preference in long-term memory.

    Use this when the user explicitly asks you to remember something across
    sessions — preferences, project conventions, decisions, or important
    context. The memory will be searchable in future sessions.

    Args:
        content: The fact to store, in natural language.
        user_id: User identifier scoping the memory.

    Returns:
        Confirmation or error message.
    """
    memory = _get_memory()
    if memory is None:
        return json.dumps({
            "error": "Mem0 is not configured. Set POSTGRES_URL and OLLAMA_BASE_URL to enable long-term memory.",
        })
    try:
        memory.add(content, user_id=user_id)
        return json.dumps({"status": "stored", "content": content[:200]})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Mem0 add failed: %s", exc)
        return json.dumps({"error": str(exc)})
