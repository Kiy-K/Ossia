"""Tests for the episodic memory tool.

The episodic tool wraps the agent's checkpointer so the model can
recall previous turns of a specific thread. The checkpointer is
thread-scoped (per ``BaseCheckpointSaver`` docs); cross-thread
enumeration requires the LangGraph SDK's ``client.threads.search`` and
is out of scope here.

Tests cover:
1. The factory returns ``None`` without a checkpointer.
2. The factory returns a tool with a checkpointer.
3. The tool returns the recent turns of a thread in chronological
   order, using a real graph run to populate the blob store.
4. The tool returns an empty ``turns`` list for a thread with no
   recorded checkpoints.
5. Recall is thread-isolated: messages on thread A are not in
   thread B's result.
6. ``search_threads`` factory returns ``None`` without a search_fn.
7. ``search_threads`` returns empty when no caller is set.
8. ``search_threads`` filters out other callers' threads.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph

from core.episodic import make_episodic_recall_tool, make_search_threads_tool
from core.request_context import caller_var


def _build_echo_graph(saver: InMemorySaver):
    """Build a minimal graph that echoes back the user's last message.

    Driving a real graph run populates the checkpointer's blob store
    (where ``messages`` are kept as serialized bytes), which is what
    production tooling actually reads on recall.
    """

    def echo(state: MessagesState) -> dict:
        msgs = state["messages"]
        return {"messages": [AIMessage(content=f"echo: {msgs[-1].content}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("echo", echo)
    builder.add_edge(START, "echo")
    return builder.compile(checkpointer=saver)


def test_factory_returns_none_without_checkpointer() -> None:
    """No persistence => no episodic tool. Caller skips wiring."""
    assert make_episodic_recall_tool(None) is None


def test_factory_returns_tool_with_checkpointer() -> None:
    saver = InMemorySaver()
    tool = make_episodic_recall_tool(saver)
    assert tool is not None
    assert tool.name == "recall_thread_turns"


async def test_recall_returns_empty_for_unknown_thread() -> None:
    """A thread with no checkpoints returns an empty ``turns`` list."""
    saver = InMemorySaver()
    tool = make_episodic_recall_tool(saver)
    assert tool is not None
    result = await tool.ainvoke({"thread_id": "never-existed", "limit": 5})
    assert result["thread_id"] == "never-existed"
    assert result["turns"] == []


async def test_recall_returns_recent_turns_in_chronological_order() -> None:
    """Run a real graph turn; the recall tool surfaces the messages."""
    saver = InMemorySaver()
    graph = _build_echo_graph(saver)
    cfg = {"configurable": {"thread_id": "alpha"}}
    await graph.ainvoke({"messages": [HumanMessage(content="first message")]}, config=cfg)
    tool = make_episodic_recall_tool(saver)
    assert tool is not None
    result = await tool.ainvoke({"thread_id": "alpha", "limit": 5})
    assert result["thread_id"] == "alpha"
    assert isinstance(result["turns"], list)
    contents = [t["content"] for t in result["turns"]]
    assert "first message" in contents
    assert any("echo: first message" in c for c in contents)
    # Roles are normalized to the API's closed set.
    for turn in result["turns"]:
        assert turn["role"] in {"user", "assistant", "tool", "system"}


async def test_recall_does_not_leak_across_threads() -> None:
    """Thread-isolated recall: messages on thread A are not in B's result."""
    saver = InMemorySaver()
    graph = _build_echo_graph(saver)
    cfg_a = {"configurable": {"thread_id": "alpha"}}
    cfg_b = {"configurable": {"thread_id": "beta"}}
    await graph.ainvoke({"messages": [HumanMessage(content="alpha only")]}, config=cfg_a)
    await graph.ainvoke({"messages": [HumanMessage(content="beta only")]}, config=cfg_b)

    tool = make_episodic_recall_tool(saver)
    assert tool is not None
    res_a = await tool.ainvoke({"thread_id": "alpha", "limit": 5})
    res_b = await tool.ainvoke({"thread_id": "beta", "limit": 5})
    a_contents = [t["content"] for t in res_a["turns"]]
    b_contents = [t["content"] for t in res_b["turns"]]
    assert "alpha only" in a_contents
    assert "beta only" not in a_contents
    assert "beta only" in b_contents
    assert "alpha only" not in b_contents


# ── E2E agent integration test ────────────────────────────────────────────
# Tests that ``recall_thread_turns`` works correctly when invoked through the
# Deep Agents runtime (not just via direct ``tool.ainvoke()``). This exercises
# the ``anyio.to_thread.run_sync()`` fix — the ``InvalidStateError`` bug only
# manifested when the tool was called through the agent's tool executor.


class _FakeToolModel(GenericFakeChatModel):
    """A fake chat model that advertises bind_tools so Deep Agents can bind schemas.

    The model emits a pre-scripted sequence of AIMessages; tool calls are already
    shaped with name/id/args so the harness routes them to the real tools.

    Pydantic copies the model during agent construction (create_deep_agent and
    the langchain 1.x middleware stack both call model_validate / model_copy),
    which would drain a plain ``iter(...)`` and leave subsequent invocations
    with no scripted response. We use a deque-backed list as the source so the
    model survives copies and each ``_generate`` call pops from the front
    without consuming the underlying iterator.
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


@pytest.mark.asyncio
async def test_recall_through_agent_with_checkpointer() -> None:
    """``recall_thread_turns`` returns populated turns when called through the
    Deep Agents runtime.

    This is the E2E test for the ``anyio.to_thread.run_sync()`` fix. The
    ``InvalidStateError`` (``AsyncPostgresSaver`` sync call from event-loop
    thread) only manifested when the tool was invoked through the agent's
    tool executor — direct ``tool.ainvoke()`` calls always worked. We build
    a real agent with an ``InMemorySaver`` checkpointer, populate checkpoints
    via a real graph run, then have the agent invoke the recall tool and
    verify the result.
    """
    from deepagents import create_deep_agent

    from core.agent import _build_middlewares
    from core.config import Provider, Settings

    # 1. Populate the checkpointer with a real graph run.
    saver = InMemorySaver()
    echo = _build_echo_graph(saver)
    pop_cfg = {"configurable": {"thread_id": "e2e-populate"}}
    await echo.ainvoke(
        {"messages": [HumanMessage(content="hello from e2e test")]},
        config=pop_cfg,
    )
    await echo.ainvoke(
        {"messages": [HumanMessage(content="second message")]},
        config=pop_cfg,
    )

    # 2. Build the episodic recall tool on the same checkpointer.
    episodic_tool = make_episodic_recall_tool(saver)
    assert episodic_tool is not None, "recall tool must be built with checkpointer"

    # 3. Build a Deep Agents agent with the recall tool.
    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=False,
        max_revision_loops=3,
    )

    model = _FakeToolModel(
        scripted=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "recall_thread_turns",
                        "id": "call-recall-1",
                        "args": {"thread_id": "e2e-populate", "limit": 5},
                    }
                ],
            ),
            AIMessage(content="I found the turns from the previous conversation."),
        ]
    )

    agent = create_deep_agent(
        name="ossia-e2e-test",
        model=model,
        tools=[episodic_tool],
        system_prompt=(
            "You are a test agent. Use recall_thread_turns to recall "
            "past conversations when the user asks."
        ),
        middleware=_build_middlewares(settings),
        checkpointer=saver,
    )

    # 4. Invoke the agent — it should call recall_thread_turns through the runtime.
    config = {"configurable": {"thread_id": "e2e-test-run"}}
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="Recall what I said earlier.")]},
        config,
    )

    messages = result["messages"]

    # 5. Verify the tool was called and returned the correct content.
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) >= 1, (
        "recall_thread_turns should have been executed and produced a ToolMessage"
    )

    recall_content = str(tool_msgs[-1].content)
    assert "hello from e2e test" in recall_content, (
        f"recall content should include the first message, got: {recall_content[:200]}"
    )
    assert "second message" in recall_content, (
        f"recall content should include the second message, got: {recall_content[:200]}"
    )
    assert "error" not in recall_content.lower(), (
        "recall should not contain an error field"
    )


# ── search_threads tests ──────────────────────────────────────────────────


def test_search_threads_factory_returns_none_without_search_fn() -> None:
    """No search backend => no search tool. Caller skips wiring."""
    assert make_search_threads_tool(None) is None


async def test_search_threads_returns_empty_without_caller() -> None:
    """Without a caller context, search_threads returns no results."""

    async def fake_search_fn(query: str, limit: int) -> list[dict]:
        return [{"thread_id": "any:t1", "snippet": "x"}]

    tool = make_search_threads_tool(fake_search_fn)
    assert tool is not None
    result = await tool.ainvoke({"query": "anything", "limit": 5})
    assert result == {"threads": []}


async def test_search_threads_filters_by_current_caller() -> None:
    """Only threads belonging to the current caller are returned,
    even if the search_fn returns other callers' threads."""

    async def fake_search_fn(query: str, limit: int) -> list[dict]:
        return [
            {"thread_id": "user-a:t1", "snippet": "match for a"},
            {"thread_id": "user-b:t1", "snippet": "match for b"},
            {"thread_id": "user-a:t2", "snippet": "another for a"},
        ]

    tool = make_search_threads_tool(fake_search_fn)
    assert tool is not None
    caller_var.set("user-a")
    try:
        result = await tool.ainvoke({"query": "match", "limit": 5})
        thread_ids = [t["thread_id"] for t in result["threads"]]
        assert "user-a:t1" in thread_ids
        assert "user-a:t2" in thread_ids
        assert "user-b:t1" not in thread_ids
        assert len(result["threads"]) == 2
    finally:
        caller_var.set(None)


async def test_search_threads_respects_limit() -> None:
    """The ``limit`` argument caps the number of returned threads."""

    async def fake_search_fn(query: str, limit: int) -> list[dict]:
        return [{"thread_id": f"user-a:t{i}", "snippet": f"hit {i}"} for i in range(limit)]

    tool = make_search_threads_tool(fake_search_fn)
    assert tool is not None
    caller_var.set("user-a")
    try:
        result = await tool.ainvoke({"query": "x", "limit": 2})
        assert len(result["threads"]) == 2
    finally:
        caller_var.set(None)
