"""Tests for Mem0 long-term memory tools.

Per ADR-0016, Mem0 integrates as a tool layer — not a DeepAgents
``Backend``. These tests verify:

1. Configuration parsing from Ossia settings.
2. The tools degrade gracefully when Mem0 is not configured.
3. Cross-session recall: write in one thread, search in another (requires
   pgvector on a reachable Postgres — skipped in CI).
"""

from __future__ import annotations

import contextlib
import json
import os

import pytest

from core.mem0_tools import (
    _build_mem0_config,
    _parse_postgres_url,
    add_memory,
    search_memory,
)

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def test_parse_postgres_url_standard() -> None:
    cfg = _parse_postgres_url("postgres://user:pass@localhost:5432/mydb")
    assert cfg == {
        "host": "localhost",
        "port": 5432,
        "dbname": "mydb",
        "user": "user",
        "password": "pass",
    }


def test_parse_postgres_url_postgresql_prefix() -> None:
    cfg = _parse_postgres_url("postgresql://u:p@host:5433/db")
    assert cfg["host"] == "host"
    assert cfg["port"] == 5433
    assert cfg["dbname"] == "db"


def test_parse_postgres_url_no_port_defaults_5432() -> None:
    cfg = _parse_postgres_url("postgres://u:p@host/db")
    assert cfg["port"] == 5432


def test_parse_postgres_url_invalid_returns_empty() -> None:
    assert _parse_postgres_url("not-a-url") == {}
    assert _parse_postgres_url("") == {}


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


class _FakeSettings:
    """Minimal settings stub for _build_mem0_config."""

    postgres_url: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    provider: str = "openrouter"
    model: str = "openai/gpt-4o-mini"
    openrouter_api_key: str = ""
    embedding_model: str = "embeddinggemma"
    embedding_dim: int = 768


def test_build_mem0_config_returns_none_without_postgres() -> None:
    s = _FakeSettings()
    s.postgres_url = None
    assert _build_mem0_config(s) is None


def test_build_mem0_config_returns_none_with_unparseable_url() -> None:
    s = _FakeSettings()
    s.postgres_url = "garbage"
    assert _build_mem0_config(s) is None


def test_build_mem0_config_returns_dict_with_valid_url() -> None:
    s = _FakeSettings()
    s.postgres_url = "postgres://u:p@host:5432/db"
    s.openrouter_api_key = "sk-test"
    cfg = _build_mem0_config(s)
    assert cfg is not None
    assert cfg["vector_store"]["provider"] == "pgvector"
    assert cfg["vector_store"]["config"]["host"] == "host"
    assert cfg["vector_store"]["config"]["port"] == 5432
    assert cfg["vector_store"]["config"]["dbname"] == "db"
    assert cfg["embedder"]["provider"] == "ollama"
    assert cfg["embedder"]["config"]["model"] == "embeddinggemma"
    # OpenRouter should point LLM at OpenAI-compatible endpoint
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["llm"]["config"]["openai_base_url"] == "https://openrouter.ai/api/v1"
    assert cfg["llm"]["config"]["model"] == "openai/gpt-4o-mini"


def test_build_mem0_config_non_openai_compatible_provider() -> None:
    s = _FakeSettings()
    s.provider = "anthropic"
    s.postgres_url = "postgres://u:p@host:5432/db"
    cfg = _build_mem0_config(s)
    assert cfg is not None
    # Anthropic is not OpenAI-compatible; should still produce a config
    # without openai_base_url.
    assert "openai_base_url" not in cfg["llm"]["config"]


# ---------------------------------------------------------------------------
# Tool graceful degradation (no Postgres available)
# ---------------------------------------------------------------------------


def test_search_memory_returns_error_when_not_configured() -> None:
    result = search_memory.invoke({"query": "test", "user_id": "u1"})
    data = json.loads(result)
    assert "error" in data
    assert "not configured" in data["error"]


def test_add_memory_returns_error_when_not_configured() -> None:
    result = add_memory.invoke({"content": "test fact", "user_id": "u1"})
    data = json.loads(result)
    assert "error" in data
    assert "not configured" in data["error"]


# ---------------------------------------------------------------------------
# Tool integration: cross-session recall
# ---------------------------------------------------------------------------
# These tests require a running Postgres with pgvector. They are skipped
# unless POSTGRES_URL is set to a valid connection string and the pgvector
# extension is available. Run manually during development and in CI.


@pytest.mark.skipif(
    not os.environ.get("POSTGRES_URL"),
    reason="POSTGRES_URL not set — skipping Mem0 integration test",
)
def test_cross_session_recall() -> None:
    """Write a memory in one user context, read it back in another call."""
    # Clear the lazy singleton so we re-init with current env.
    import core.mem0_tools as mod
    from core.mem0_tools import _get_memory

    mod._memory = None
    mod._mem0_config_error = None

    mem = _get_memory()
    if mem is None:
        pytest.skip("Mem0 not configured — check POSTGRES_URL and OLLAMA_BASE_URL")

    user_id = "test-cross-session"

    # Clean up from any prior run
    with contextlib.suppress(Exception):
        mem.delete_all(user_id=user_id)

    # Write
    add_result = add_memory.invoke({
        "content": "User prefers dark mode and tabs over spaces",
        "user_id": user_id,
    })
    assert "stored" in add_result

    # Read — wait a beat for pgvector async index
    import time

    time.sleep(0.5)

    search_result = search_memory.invoke({
        "query": "What are the user's coding preferences?",
        "user_id": user_id,
    })
    data = json.loads(search_result)
    # Mem0 should return some results — at minimum the fact we just stored
    assert isinstance(data, list) or "results" in data
    results = data if isinstance(data, list) else data.get("results", [])
    assert len(results) > 0, f"Expected at least one memory recall result, got {results}"

    # Clean up
    with contextlib.suppress(Exception):
        mem.delete_all(user_id=user_id)
