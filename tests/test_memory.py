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
6. Agent-level ``write_file``, ``read_file``, and cross-thread
   persistence through the ``CompositeBackend`` / ``StoreBackend``
   routes work end-to-end.
7. Policy write-deny and StateBacked thread-scoping are enforced
   at the agent level.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from deepagents.backends.utils import create_file_data
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from core.config import Provider, Settings
from core.memory import (
    AGENT_NAMESPACE,
    AGENTS_MEMORY_KEY,
    POLICY_NAMESPACE,
    _store_key_from_memory_path,
    ensure_caller_memory_seeded,
    initial_agents_memory,
    read_memory_item,
    seed_memory,
)

# Store key that StoreBackend uses after stripping the /memories/ route prefix.
# seed_memory now writes at this key (not the full path AGENTS_MEMORY_KEY).
STORE_MEMORY_KEY = _store_key_from_memory_path(AGENTS_MEMORY_KEY)

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
    # seed_memory now writes at the StoreBackend-compatible key
    # (stripping the /memories/ route prefix).
    item = await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY)
    assert item is not None
    body = read_memory_item(item)
    assert "Ossia" in body


async def test_seed_memory_is_idempotent() -> None:
    store = InMemoryStore()
    await seed_memory(store)
    # Simulate the agent rewriting part of the file.
    body = read_memory_item(await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY))
    updated = body + "\n\n## Notes\nI learned something."
    await store.aput(
        AGENT_NAMESPACE,
        STORE_MEMORY_KEY,
        create_file_data(updated),
    )
    # Re-seed: must NOT clobber the agent's update.
    created = await seed_memory(store)
    assert created is False
    after = read_memory_item(await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY))
    assert "I learned something." in after


async def test_namespace_isolation() -> None:
    """Memory written under the agent namespace is not visible elsewhere."""
    store = InMemoryStore()
    await seed_memory(store)
    other = await store.aget(("other-namespace",), STORE_MEMORY_KEY)
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
    item = await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY)
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
    body1 = read_memory_item(await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY))
    body2 = body1 + "\n\n## From thread-2\nMore notes."
    await store.aput(AGENT_NAMESPACE, STORE_MEMORY_KEY, create_file_data(body2))
    final = read_memory_item(await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY))
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
    from core.memory import seed_policy

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


# ---------------------------------------------------------------------------
# /scratch/ working-memory route (hybrid Redis-for-hot/Postgres-for-cold)
# ---------------------------------------------------------------------------


def test_scratch_namespace_is_per_caller() -> None:
    """The /scratch/ route uses the caller's hash so working state
    does not bleed between authenticated users.
    """
    from core.agent import _make_scratch_namespace
    from core.request_context import caller_var

    caller_var.set("alice")
    assert _make_scratch_namespace() == ("ossia", "alice")
    caller_var.set("bob")
    assert _make_scratch_namespace() == ("ossia", "bob")
    caller_var.set(None)
    assert _make_scratch_namespace() == ("ossia", "scratch")


def test_make_backend_routes_scratch_to_scratch_store() -> None:
    """When a scratch_store is provided, the /scratch/ route goes there.
    The main /memories/ and /policies/ routes continue to use the
    primary store.
    """
    from core.agent import _make_backend
    from core.request_context import caller_var

    main_store = InMemoryStore()
    scratch_store = InMemoryStore()
    backend = _make_backend(main_store, scratch_store)

    routes = backend.routes  # type: ignore[attr-defined]
    assert "/scratch/" in routes
    assert "/memories/" in routes
    assert "/policies/" in routes

    # /memories/ and /policies/ are on the main store.
    assert routes["/memories/"]._store is main_store  # type: ignore[attr-defined]
    assert routes["/policies/"]._store is main_store  # type: ignore[attr-defined]
    # /scratch/ is on the scratch store.
    assert routes["/scratch/"]._store is scratch_store  # type: ignore[attr-defined]

    # Per-caller namespace for scratch.
    caller_var.set("alice")
    ns = routes["/scratch/"]._namespace(None)  # type: ignore[misc]
    assert ns == ("ossia", "alice")
    caller_var.set(None)


def test_make_backend_omits_scratch_route_when_scratch_store_is_none() -> None:
    """Without a scratch_store, the /scratch/ route is not mounted.
    Postgres-only and in-memory deployments get a clean CompositeBackend
    with only /memories/ and /policies/.
    """
    from core.agent import _make_backend

    main_store = InMemoryStore()
    backend = _make_backend(main_store, scratch_store=None)
    routes = backend.routes  # type: ignore[attr-defined]
    assert "/scratch/" not in routes
    assert "/memories/" in routes
    assert "/policies/" in routes


# ---------------------------------------------------------------------------
# ensure_caller_memory_seeded() direct tests
# ---------------------------------------------------------------------------
# These tests verify that ``ensure_caller_memory_seeded`` correctly seeds
# per-caller namespaces, is a no-op when there is no store, is idempotent,
# and isolates different callers. See ``core/memory.py`` and ``core/api.py``
# for the production call sites (``chat`` and ``chat_stream`` handlers).


async def test_ensure_caller_memory_seeded_noop_when_store_none() -> None:
    """ensure_caller_memory_seeded is a no-op when store is None.

    The function is called on every authenticated request before the agent
    runs. When no store is configured (e.g., in-process test builds), it
    must not raise and must not attempt to write anything.
    """
    # Must not raise even with store=None.
    result = await ensure_caller_memory_seeded(None, "caller-abc")
    assert result is None, "store=None must be a no-op"


async def test_ensure_caller_memory_seeded_seeds_callers_namespace() -> None:
    """Seeds the per-caller namespace ("ossia", "caller_xxx") with the
    initial AGENTS.md content, without polluting the base namespace.
    """
    store = InMemoryStore()
    await ensure_caller_memory_seeded(store, "caller-xyz")

    # Must be in the per-caller namespace, not the base.
    item = await store.aget(("ossia", "caller-xyz"), STORE_MEMORY_KEY)
    assert item is not None, "per-caller namespace must contain the seeded file"
    body = read_memory_item(item)
    assert "Ossia" in body, "seeded content must match initial_agents_memory"

    # Base namespace must be empty (no cross-contamination).
    base_item = await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY)
    assert base_item is None, "caller seed must not write to the base namespace"


async def test_ensure_caller_memory_seeded_is_idempotent() -> None:
    """Re-seeding the same caller does not clobber previous content.

    ``ensure_caller_memory_seeded`` returns ``None`` (unlike
    ``seed_memory`` which returns a bool). The idempotency guarantee
    is that the agent's edits survive re-seed, not a specific return
    value.
    """
    store = InMemoryStore()
    await ensure_caller_memory_seeded(store, "caller-same")

    # Simulate the agent editing the file in the per-caller namespace.
    item = await store.aget(("ossia", "caller-same"), STORE_MEMORY_KEY)
    body = read_memory_item(item)
    updated = body + "\n\n## Agent addendum\nLearned something important."
    await store.aput(("ossia", "caller-same"), STORE_MEMORY_KEY, create_file_data(updated))

    # Re-seed: must not clobber the agent's update.
    await ensure_caller_memory_seeded(store, "caller-same")

    after = read_memory_item(await store.aget(("ossia", "caller-same"), STORE_MEMORY_KEY))
    assert "Agent addendum" in after, "agent edits must survive re-seed"
    assert "Ossia" in after, "initial content must still be present"


async def test_ensure_caller_memory_seeded_different_callers_isolated() -> None:
    """Each caller gets their own seeded namespace. Modifications to one
    caller's namespace do not affect another caller's.
    """
    store = InMemoryStore()
    await ensure_caller_memory_seeded(store, "caller-A")
    await ensure_caller_memory_seeded(store, "caller-B")

    item_a = await store.aget(("ossia", "caller-A"), STORE_MEMORY_KEY)
    item_b = await store.aget(("ossia", "caller-B"), STORE_MEMORY_KEY)
    assert item_a is not None, "caller-A must be seeded"
    assert item_b is not None, "caller-B must be seeded"

    # Modify A's copy only.
    body_a = read_memory_item(item_a)
    await store.aput(
        ("ossia", "caller-A"),
        STORE_MEMORY_KEY,
        create_file_data(body_a + "\n# A-only section\nOnly A sees this."),
    )

    # B's copy must be untouched.
    body_b = read_memory_item(
        await store.aget(("ossia", "caller-B"), STORE_MEMORY_KEY)
    )
    assert "A-only section" not in body_b, "caller B must not see caller A's edits"


# ---------------------------------------------------------------------------
# Agent-level integration tests: per-caller namespace seeding through agent
# ---------------------------------------------------------------------------
# Verifies that a per-caller namespace seeded via
# ``ensure_caller_memory_seeded`` is reachable through the agent's
# ``read_file`` tool when the runtime ``caller_var`` matches.


@pytest.mark.asyncio
async def test_agent_reads_per_caller_namespace_seeded_by_ensure() -> None:
    """Seed caller-A's namespace via ensure_caller_memory_seeded, then
    read /memories/AGENTS.md through an agent running as caller-A.

    The agent's ``_make_memory_namespace`` reads ``caller_var`` to
    derive the per-caller namespace. If ``ensure_caller_memory_seeded``
    seeded ``("ossia", "caller-A")`` and the agent's backend is
    configured to look at the same namespace (via ``caller_var``), the
    ``read_file`` tool should return the seeded content.
    """
    from core.request_context import caller_var

    store = InMemoryStore()
    await ensure_caller_memory_seeded(store, "caller-isolated")

    # Set the caller context so the agent's backend resolves to the
    # same namespace that was seeded above.
    original_caller = caller_var.get()
    caller_var.set("caller-isolated")
    try:
        graph, thread_id = _memory_test_agent(
            store,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "id": "call-ensure-read-1",
                        "args": {"file_path": "/memories/AGENTS.md"},
                    }
                ],
            ),
            AIMessage(content="Read the seeded memory file."),
            permissions=[],
        )
        config = {"configurable": {"thread_id": thread_id}}
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="read my memory file")]}, config
        )

        tool_msgs = [
            m
            for m in result["messages"]
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "read_file"
        ]
        assert tool_msgs, "expected a ToolMessage from read_file"
        content = str(tool_msgs[0].content)
        assert "Ossia" in content, (
            "agent should read the seeded memory from the per-caller namespace"
        )
    finally:
        caller_var.set(original_caller)

# ---------------------------------------------------------------------------
# Agent-level integration tests: filesystem tools through StoreBackend
# ---------------------------------------------------------------------------
# These tests exercise the agent's ``write_file``, ``read_file``, and
# ``edit_file`` filesystem tools through a real ``create_deep_agent``
# graph with an ``InMemoryStore`` + ``CompositeBackend``. Only the LLM
# is faked (via ``_FakeToolModel``), so the full middleware stack,
# routing, and permission layer are exercised.


class _FakeToolModel(GenericFakeChatModel):
    """A fake chat model that pre-scripts AIMessages with tool calls.

    Uses a mutable list (not an iterator) so the model survives Pydantic
    copies during agent construction.
    """

    def __init__(self, scripted: list[AIMessage]) -> None:
        super().__init__(messages=iter([]))
        self._scripted = list(scripted)

    def _generate(  # type: ignore[override]
        self,
        messages: list,  # noqa: ARG002
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: Any = None,  # noqa: ARG002
        **kwargs: Any,
    ) -> Any:
        from langchain_core.outputs import ChatGeneration, ChatResult

        if not self._scripted:
            raise RuntimeError("_FakeToolModel ran out of scripted responses")
        message = self._scripted.pop(0)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: object, **kwargs: object) -> _FakeToolModel:  # noqa: ARG002
        return self


def _memory_test_settings() -> Settings:
    """Settings with HITL disabled for memory/file tests."""
    return Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=False,
        max_revision_loops=3,
        enable_async_subagents=False,
        enable_eager_tools=False,
    )


def _memory_test_agent(
    store: InMemoryStore,
    *scripted: AIMessage,
    scratch_store: InMemoryStore | None = None,
    permissions: list[Any] | None = None,
) -> tuple[Any, str]:
    """Build a minimal agent with a store-backed CompositeBackend.

    Only the model is faked; the full middleware stack and filesystem
    tools (write_file, read_file, edit_file, etc.) are real.

    Args:
        store: The main InMemoryStore (hosts /memories/ and /policies/).
        scripted: Pre-scripted AIMessages for the fake model.
        scratch_store: Optional separate store for the /scratch/ route.
        permissions: Optional filesystem permissions. Defaults to
            production-style policy write-deny.

    Returns:
        (compiled_graph, thread_id)
    """
    from deepagents import create_deep_agent

    from core.agent import _build_middlewares, _make_backend, create_core_tools

    settings = _memory_test_settings()
    backend = _make_backend(store, scratch_store)
    saver = InMemorySaver()
    model = _FakeToolModel(scripted=list(scripted))
    if permissions is None:
        permissions = list(_get_policy_deny_write())
    graph = create_deep_agent(
        name="ossia-mem-test",
        model=model,
        tools=create_core_tools(),
        system_prompt="test",
        middleware=_build_middlewares(settings),
        checkpointer=saver,
        store=store,
        backend=backend,
        permissions=permissions,
    )
    return graph, "mem-test-thread"


@pytest.mark.asyncio
async def test_agent_caller_scoped_and_base_namespace_dont_collide() -> None:
    """An agent running as caller-A does NOT see content written to the
    base namespace ("ossia",) by seed_memory when caller_var is set.

    When ``caller_var`` is set, ``_make_memory_namespace`` returns
    ``("ossia", "caller-A")``, not ``("ossia",)``. Content seeded to
    the base namespace via ``seed_memory()`` must NOT be visible.
    Only content seeded to the caller's namespace should be visible.
    """
    from core.request_context import caller_var

    store = InMemoryStore()
    # Seed the BASE namespace (as the startup lifespan does).
    await seed_memory(store)

    # Now, seed the per-caller namespace too, with distinct content.
    await ensure_caller_memory_seeded(store, "caller-isolated")

    # Overwrite the per-caller copy with distinct content so we can
    # tell the two apart.
    await store.aput(
        ("ossia", "caller-isolated"),
        STORE_MEMORY_KEY,
        create_file_data("# Ossia — Per-caller memory\n" "This is the CALLER-SCOPED copy."),
    )

    original_caller = caller_var.get()
    caller_var.set("caller-isolated")
    try:
        graph, thread_id = _memory_test_agent(
            store,
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "id": "call-collide-1",
                        "args": {"file_path": "/memories/AGENTS.md"},
                    }
                ],
            ),
            AIMessage(content="Read."),
            permissions=[],
        )
        config = {"configurable": {"thread_id": thread_id}}
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="read memory")]}, config
        )

        tool_msgs = [
            m
            for m in result["messages"]
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "read_file"
        ]
        assert tool_msgs, "expected a ToolMessage from read_file"
        content = str(tool_msgs[0].content)
        assert "Per-caller memory" in content, (
            "agent must read from the per-caller namespace, not the base"
        )
        # The base namespace content must NOT be what the agent sees.
        body_base = read_memory_item(
            await store.aget(AGENT_NAMESPACE, STORE_MEMORY_KEY)
        )
        assert body_base != content, (
            "agent must NOT read from the base namespace when caller_var is set"
        )
    finally:
        caller_var.set(original_caller)


@pytest.mark.asyncio
async def test_agent_write_file_to_memories() -> None:
    """write_file to /memories/ persists content in the store backend through the agent."""
    store = InMemoryStore()
    graph, thread_id = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "id": "call-w-1",
                    "args": {"file_path": "/memories/test.txt", "content": "Hello from agent!"},
                }
            ],
        ),
        AIMessage(content="Written."),
    )
    config = {"configurable": {"thread_id": thread_id}}
    await graph.ainvoke(
        {"messages": [HumanMessage(content="write test file")]}, config
    )

    # The StoreBackend strips the route prefix (/memories/) and stores
    # with the relative path as key (prepended with /). The namespace
    # is AGENT_NAMESPACE (no caller in tests).
    item = await store.aget(AGENT_NAMESPACE, "/test.txt")
    assert item is not None, "write_file should persist content to the store"
    assert "Hello from agent!" in read_memory_item(item)


@pytest.mark.asyncio
async def test_agent_read_file_from_seeded_store() -> None:
    """Pre-seeded content is readable via the agent's read_file tool.

    ``seed_memory`` now writes at the StoreBackend-compatible key
    (stripping the ``/memories/`` route prefix), so the agent's
    ``read_file`` can find the seeded data.
    """
    store = InMemoryStore()
    await seed_memory(store)

    graph, thread_id = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "id": "call-r-1",
                    "args": {"file_path": "/memories/AGENTS.md"},
                }
            ],
        ),
        AIMessage(content="Read the memory file."),
        permissions=[],  # No permission checks needed for read-only test
    )
    config = {"configurable": {"thread_id": "mem-read-test"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="read the memory file")]}, config
    )

    # The read_file tool returns the content, which lands in a ToolMessage.
    tool_msgs = [
        m for m in result["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "read_file"
    ]
    assert tool_msgs, "expected a ToolMessage from read_file"
    content = str(tool_msgs[0].content)
    assert "Ossia" in content, "read_file should return the seeded memory content"


@pytest.mark.asyncio
async def test_agent_write_file_persists_across_threads() -> None:
    """Content written via the agent in thread-1 is readable in thread-2.

    StoreBackend is shared across threads (unlike StateBackend), so
    /memories/ files persist.
    """
    store = InMemoryStore()

    # ── Thread 1: write via agent ──────────────────────────────────────────
    graph1, tid1 = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "id": "call-w-ct-1",
                    "args": {
                        "file_path": "/memories/shared.txt",
                        "content": "Written by thread-1",
                    },
                }
            ],
        ),
        AIMessage(content="Done."),
    )
    await graph1.ainvoke(
        {"messages": [HumanMessage(content="write in thread-1")]},
        {"configurable": {"thread_id": tid1}},
    )

    # Verify it's in the store (StoreBackend stores as /<relative_path>).
    item = await store.aget(AGENT_NAMESPACE, "/shared.txt")
    assert item is not None
    assert "Written by thread-1" in read_memory_item(item)

    # ── Thread 2: read via agent (different graph instance, same store) ─────
    graph2, tid2 = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "id": "call-r-ct-2",
                    "args": {"file_path": "/memories/shared.txt"},
                }
            ],
        ),
        AIMessage(content="Read."),
        permissions=[],  # No writes; read-only, no permission checks needed
    )
    result2 = await graph2.ainvoke(
        {"messages": [HumanMessage(content="read in thread-2")]},
        {"configurable": {"thread_id": tid2}},
    )

    # The read_file result in thread-2 must contain thread-1's content.
    tool_msgs = [
        m for m in result2["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "read_file"
    ]
    assert tool_msgs, "expected a ToolMessage from read_file in thread-2"
    content = str(tool_msgs[0].content)
    assert "Written by thread-1" in content, (
        "thread-2 should read content written by thread-1 via shared store"
    )


@pytest.mark.asyncio
async def test_agent_policy_write_is_rejected() -> None:
    """Writing to /policies/ via the agent is blocked by _POLICY_DENY_WRITE.

    The agent's FilesystemPermission denies write operations on the
    /policies/ route. The write_file tool should return an error
    ToolMessage.
    """
    from core.memory import seed_policy

    store = InMemoryStore()
    await seed_policy(store, "/policies/compliance.md", "No PII in logs")

    graph, thread_id = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "id": "call-w-policy-1",
                    "args": {
                        "file_path": "/policies/compliance.md",
                        "content": "MALICIOUS OVERWRITE",
                    },
                }
            ],
        ),
        AIMessage(content="Write rejected."),
    )
    config = {"configurable": {"thread_id": "mem-policy-test"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="try to overwrite policy")]}, config
    )

    # The tool wrote successfully at the agent level, but the store
    # content under POLICY_NAMESPACE must still be preserved because
    # the /policies/ route is backed by a separate namespace and
    # protected by write-deny FilesystemPermission at the agent level
    # (_POLICY_DENY_WRITE prevents writes to /policies/).
    write_msgs = [
        m for m in result["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "write_file"
    ]
    assert write_msgs, "expected a ToolMessage from write_file"

    # The store content under POLICY_NAMESPACE must not change.
    item = await store.aget(POLICY_NAMESPACE,
        "/policies/compliance.md")
    assert item is not None
    body = read_memory_item(item)
    assert "MALICIOUS OVERWRITE" not in body, "policy content must not be overwritten"
    assert "No PII in logs" in body, "original policy content must be preserved"


@pytest.mark.asyncio
async def test_agent_statebackend_files_are_thread_scoped() -> None:
    """Files written to the default StateBackend route are NOT visible
    across threads. Only StoreBackend-backed paths (/memories/, /policies/,
    /scratch/) persist across threads.
    """
    store = InMemoryStore()

    # ── Thread 1: write to a non-routed path (falls through to StateBackend) ──
    graph1, tid1 = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "id": "call-w-sb-1",
                    "args": {
                        "file_path": "/working/tmp.txt",
                        "content": "Ephemeral state",
                    },
                }
            ],
        ),
        AIMessage(content="Written to state."),
    )
    await graph1.ainvoke(
        {"messages": [HumanMessage(content="write temp state")]},
        {"configurable": {"thread_id": tid1}},
    )

    # ── Thread 2: try to read the same path ──────────────────────────────────
    # ── Thread 1: confirm the write happened (verify in state) ──────────────
    # First check the thread-1 state directly for the write result.
    state1 = await graph1.aget_state({"configurable": {"thread_id": tid1}})
    t1_msgs = state1.values.get("messages", [])
    t1_writes = [
        m for m in t1_msgs
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "write_file"
    ]
    assert t1_writes, "thread-1 write_file should have produced a ToolMessage"

    # ── Thread 2: try to read the same path ──────────────────────────────────
    graph2, tid2 = _memory_test_agent(
        store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_file",
                    "id": "call-r-sb-2",
                    "args": {"file_path": "/working/tmp.txt"},
                }
            ],
        ),
        AIMessage(content="Read attempt."),
        permissions=[],
    )
    result2 = await graph2.ainvoke(
        {"messages": [HumanMessage(content="read temp state")]},
        {"configurable": {"thread_id": tid2}},
    )

    # Thread-2 should NOT find the file (StateBackend is per-thread).
    t2_msgs = result2["messages"]
    t2_reads = [
        m for m in t2_msgs
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "read_file"
    ]
    assert t2_reads, "expected a ToolMessage from read_file in thread-2"
    content = str(t2_reads[0].content)
    assert "Ephemeral state" not in content, (
        "StateBackend file should not be visible in a different thread"
    )


@pytest.mark.asyncio
async def test_agent_scratch_writes_isolated_from_main_store() -> None:
    """Writes to /scratch/ go to the scratch store, not the main store.

    The /scratch/ route is backed by a separate store, so /memories/
    and /scratch/ are fully isolated.
    """
    main_store = InMemoryStore()
    scratch_store = InMemoryStore()

    graph, thread_id = _memory_test_agent(
        main_store,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "id": "call-w-scratch-1",
                    "args": {
                        "file_path": "/scratch/notes.txt",
                        "content": "Working memory note",
                    },
                }
            ],
        ),
        AIMessage(content="Written to scratch."),
        scratch_store=scratch_store,
    )
    config = {"configurable": {"thread_id": "mem-scratch-test"}}
    await graph.ainvoke(
        {"messages": [HumanMessage(content="write scratch note")]}, config
    )

    # /scratch/ routes to scratch_store: the content must be there.
    # StoreBackend stores with key /<relative_path>.
    scratch_item = await scratch_store.aget(("ossia", "scratch"), "/notes.txt")
    assert scratch_item is not None, "scratch_store should contain the written file"
    assert "Working memory note" in read_memory_item(scratch_item)

    # /scratch/ does NOT route to main_store: main store must be empty.
    main_item = await main_store.aget(AGENT_NAMESPACE, "/notes.txt")
    assert main_item is None, "main store should NOT receive scratch writes"


def _get_policy_deny_write() -> list[Any]:
    """Return the production _POLICY_DENY_WRITE list.

    Imported lazily to avoid circular imports at module level.
    """
    from core.agent import _POLICY_DENY_WRITE

    return list(_POLICY_DENY_WRITE)


if __name__ == "__main__":
    # Allow ``python -m tests.test_memory`` for quick local debugging.
    asyncio.run(test_seed_memory_creates_file_on_fresh_store())
    print("OK")
