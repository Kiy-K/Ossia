"""Tests for the episodic memory tool.

The episodic tool wraps the agent's checkpointer so the model can
recall previous turns of a specific thread. The checkpointer is
thread-scoped (per ``BaseCheckpointSaver`` docs); cross-thread
enumeration requires the LangGraph SDK's ``client.threads.search``
and is out of scope here.

Tests cover:
1. The factory returns ``None`` without a checkpointer.
2. The factory returns a tool with a checkpointer.
3. The tool returns the recent turns of a thread in chronological
   order, using a real graph run to populate the blob store.
4. The tool returns an empty ``turns`` list for a thread with no
   recorded checkpoints.
5. Recall is thread-isolated: messages on thread A are not in
   thread B's result.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph

from ossia.episodic import make_episodic_recall_tool


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
    await graph.ainvoke(
        {"messages": [HumanMessage(content="first message")]}, config=cfg
    )
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
    await graph.ainvoke(
        {"messages": [HumanMessage(content="alpha only")]}, config=cfg_a
    )
    await graph.ainvoke(
        {"messages": [HumanMessage(content="beta only")]}, config=cfg_b
    )

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

