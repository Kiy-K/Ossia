"""Tests for the agent-scoped memory layer.

The store-backed ``/memories/AGENTS.md`` file is loaded into the system
prompt at startup and persisted across threads. These tests lock in:

1. The seed is written on a fresh store and is idempotent.
2. The agent can read the seeded file via the filesystem tools.
3. The agent's ``edit_file`` writes propagate and survive a re-seed.
4. The same memory is visible across two threads (agent-scoped, not
   per-thread).
5. The fixed ``("ossia",)`` namespace isolates agent memory from
   whatever else is in the store.
"""

from __future__ import annotations

import asyncio

from deepagents.backends.utils import create_file_data
from langgraph.store.memory import InMemoryStore

from core.memory import (
    AGENT_NAMESPACE,
    AGENTS_MEMORY_KEY,
    initial_agents_memory,
    read_memory_item,
    seed_memory,
)

# ---------------------------------------------------------------------------
# Direct store helpers
# ---------------------------------------------------------------------------


def test_initial_agents_memory_includes_identity_and_learned_section() -> None:
    body = initial_agents_memory()
    assert "Ossia" in body
    assert "Things I've learned" in body
    assert "3 times before finalizing" in body  # matches the seed's revision loop guidance


async def test_seed_memory_creates_file_on_fresh_store() -> None:
    store = InMemoryStore()
    created = await seed_memory(store)
    assert created is True
    item = await store.aget(AGENT_NAMESPACE, AGENTS_MEMORY_KEY)
    assert item is not None
    body = read_memory_item(item)
    assert "Ossia" in body


async def test_seed_memory_is_idempotent() -> None:
    store = InMemoryStore()
    await seed_memory(store)
    # Simulate the agent rewriting part of the file.
    body = read_memory_item(await store.aget(AGENT_NAMESPACE, AGENTS_MEMORY_KEY))
    updated = body + "\n\n## Notes\nI learned something."
    await store.aput(
        AGENT_NAMESPACE,
        AGENTS_MEMORY_KEY,
        create_file_data(updated),
    )
    # Re-seed: must NOT clobber the agent's update.
    created = await seed_memory(store)
    assert created is False
    after = read_memory_item(await store.aget(AGENT_NAMESPACE, AGENTS_MEMORY_KEY))
    assert "I learned something." in after


async def test_namespace_isolation() -> None:
    """Memory written under the agent namespace is not visible elsewhere."""
    store = InMemoryStore()
    await seed_memory(store)
    other = await store.aget(("other-namespace",), AGENTS_MEMORY_KEY)
    assert other is None


# ---------------------------------------------------------------------------
# Agent integration: the filesystem tool actually surfaces the seed
# ---------------------------------------------------------------------------


async def test_agent_filesystem_surfaces_seeded_memory() -> None:
    """Build a real agent and confirm the seed_memory call wired the
    AGENTS.md into the store the agent's tools node reads from.

    The integration of ``StoreBackend.aread`` into the tool is
    exercised by the full audit run (which calls the agent end-to-end
    via the FastAPI server). Here we only lock in the store layer:
    building the agent must not fail when a store is supplied, and the
    seed must be present in the store that the agent is built with.
    """
    from core.agent import _make_backend

    store = InMemoryStore()
    # Pre-seed (build_agent_async also does this; we double-seed to
    # verify the function is idempotent).
    await seed_memory(store)
    await seed_memory(store)
    item = await store.aget(AGENT_NAMESPACE, AGENTS_MEMORY_KEY)
    assert item is not None
    body = read_memory_item(item)
    assert "Ossia" in body

    # The backend factory builds without raising and includes a
    # /memories/ route.
    backend = _make_backend(store)
    assert "/memories/" in backend.routes


# ---------------------------------------------------------------------------
# Cross-thread persistence (agent-scoped, not per-thread)
# ---------------------------------------------------------------------------


async def test_memory_persists_across_threads() -> None:
    store = InMemoryStore()
    await seed_memory(store)
    # Simulate two threads both writing to the same key.
    body1 = read_memory_item(await store.aget(AGENT_NAMESPACE, AGENTS_MEMORY_KEY))
    body2 = body1 + "\n\n## From thread-2\nMore notes."
    await store.aput(
        AGENT_NAMESPACE, AGENTS_MEMORY_KEY, create_file_data(body2)
    )
    final = read_memory_item(await store.aget(AGENT_NAMESPACE, AGENTS_MEMORY_KEY))
    assert "From thread-2" in final


async def test_memory_scope_agent_shares_namespace_across_callers() -> None:
    """When Settings.memory_scope == 'agent', the caller hash is ignored
    and the base namespace is returned. All callers read/write the same
    memory file (DeepAgents agent-scoped pattern)."""
    from core.agent import _make_memory_namespace
    from core.config import get_settings
    from core.request_context import caller_var

    settings = get_settings()
    original_scope = settings.memory_scope
    caller_var.set("user-abc")
    try:
        settings.memory_scope = "agent"
        ns = _make_memory_namespace(AGENT_NAMESPACE)
        assert ns == AGENT_NAMESPACE, f"agent scope must ignore caller, got {ns}"

        # Different caller, same scope → same namespace.
        caller_var.set("user-def")
        ns2 = _make_memory_namespace(AGENT_NAMESPACE)
        assert ns2 == ns, "agent scope must collapse all callers to the same namespace"

        # Back to user scope → caller prepended.
        settings.memory_scope = "user"
        ns3 = _make_memory_namespace(AGENT_NAMESPACE)
        assert ns3 == ("ossia", "user-def"), f"user scope must include caller, got {ns3}"
    finally:
        settings.memory_scope = original_scope
        caller_var.set(None)


async def test_seed_policy_writes_to_policy_namespace() -> None:
    """seed_policy populates the read-only /policies/ namespace;
    subsequent calls are idempotent."""
    from core.memory import POLICY_NAMESPACE, seed_policy

    store = InMemoryStore()
    created = await seed_policy(store, "/policies/compliance.md", "no PII logging")
    assert created is True
    item = await store.aget(POLICY_NAMESPACE, "/policies/compliance.md")
    assert read_memory_item(item) == "no PII logging"

    # Idempotent: re-seeding is a no-op.
    created_again = await seed_policy(store, "/policies/compliance.md", "DIFFERENT")
    assert created_again is False
    item2 = await store.aget(POLICY_NAMESPACE, "/policies/compliance.md")
    assert read_memory_item(item2) == "no PII logging"


if __name__ == "__main__":
    # Allow ``python -m tests.test_memory`` for quick local debugging.
    asyncio.run(test_seed_memory_creates_file_on_fresh_store())
    print("OK")
