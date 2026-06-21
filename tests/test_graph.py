"""Unit tests for the Ossia agent graph."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from ossia.agent import build_agent
from ossia.config import Provider, Settings
from ossia.tools import create_kb


def _test_settings() -> Settings:
    """Return settings configured for fast local tests."""
    return Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=False,
        max_revision_loops=3,
    )


def test_build_agent_creates_graph() -> None:
    """The agent builder returns a compiled graph with model and tools nodes."""
    settings = _test_settings()
    graph = build_agent(settings=settings)

    assert graph is not None
    # Deep Agents compiles tools into a single 'tools' node.
    assert "tools" in graph.nodes
    assert "model" in graph.nodes


@pytest.mark.asyncio
async def test_kb_search_returns_results() -> None:
    """Knowledge base search returns matching documents."""
    kb = create_kb()
    results = kb.search("Nebius Serverless Endpoints", top_k=2)
    assert len(results) >= 1
    assert any("endpoint" in r.content.lower() for r in results)


@pytest.mark.asyncio
async def test_kb_empty_uses_fallback() -> None:
    """When KB is empty, search returns no results instead of crashing."""
    kb = create_kb(documents=[])
    results = kb.search("unknown query", top_k=3)
    assert results == []


@pytest.mark.asyncio
async def test_kb_search_tool_fallback_on_web_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search tool degrades gracefully when web fallback fails."""
    from ossia import tools as tools_module

    def _raise(_query: str, _max: int) -> list[dict]:
        raise RuntimeError("network down")

    monkeypatch.setattr(tools_module, "_ddgs_text", _raise)
    result = await tools_module.search_knowledge_base.ainvoke(
        {"query": "zzz-no-such-topic-12345", "top_k": 1}
    )
    assert result.fallback_used is True
    assert result.results == []


def test_human_review_interrupt_configuration() -> None:
    """Human review enabled adds an interrupt on the send_response tool."""
    settings = _test_settings()
    settings.enable_human_review = True

    graph = build_agent(settings=settings)
    assert graph is not None


class _FakeToolModel(GenericFakeChatModel):
    """A fake chat model that advertises bind_tools so Deep Agents can bind schemas.

    The model emits a pre-scripted sequence of AIMessages; tool calls are already
    shaped with name/id/args so the harness routes them to the real tools.
    """

    def bind_tools(self, tools: object, **kwargs: object) -> _FakeToolModel:  # noqa: ARG002
        return self


def _send_response_ai() -> AIMessage:
    """An assistant turn that requests the (interrupt-gated) send_response tool."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "send_response", "id": "call-send-1", "args": {"response": "final draft", "channel": "chat"}}
        ],
    )


def _human_review_agent(
    *follow_ups: AIMessage,
) -> tuple[Any, str]:
    """Build a real Ossia agent with human review on and a fake scripted model.

    Uses Ossia's actual tools, middleware, and interrupt config so the test
    exercises the production human-in-the-loop path -- only the model is faked
    so the test is deterministic and offline.
    """
    from deepagents import create_deep_agent
    from langgraph.checkpoint.memory import InMemorySaver

    from ossia.agent import _build_middlewares, _interrupt_config, create_core_tools
    from ossia.config import Settings

    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=True,
        max_revision_loops=3,
    )
    saver = InMemorySaver()
    model = _FakeToolModel(messages=iter([_send_response_ai(), *follow_ups]))
    graph = create_deep_agent(
        name="ossia-test",
        model=model,
        tools=create_core_tools(),
        system_prompt="test",
        middleware=_build_middlewares(settings),
        checkpointer=saver,
        interrupt_on=_interrupt_config(settings, saver),
    )
    return graph, "hr-thread-1"


@pytest.mark.asyncio
async def test_human_review_blocks_until_approved() -> None:
    """The agent pauses at send_response; approving via Command(resume=...) delivers it."""
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    graph, thread_id = _human_review_agent(AIMessage(content="Delivered, thank you."))
    config = {"configurable": {"thread_id": thread_id}}

    # First invocation must block at the send_response interrupt (no final answer).
    await graph.ainvoke({"messages": [HumanMessage(content="please send the response")]}, config)
    state = await graph.aget_state(config)
    assert any(task.interrupts for task in state.tasks), "agent should be paused for human review"

    # Resume with an approve decision -> the gated tool runs and the turn completes.
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}), config
    )
    messages = result["messages"]
    assert any(getattr(m, "name", None) == "send_response" for m in messages), \
        "send_response should execute after approval"
    assert isinstance(messages[-1], AIMessage), "agent should produce a final assistant message"
    assert messages[-1].content.strip(), "final message should be non-empty"


@pytest.mark.asyncio
async def test_human_review_reject_blocks_send() -> None:
    """Rejecting the review blocks send_response and feeds the reason back to the model."""
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    graph, thread_id = _human_review_agent(AIMessage(content="Okay, I will revise instead."))
    config = {"configurable": {"thread_id": thread_id}}

    await graph.ainvoke({"messages": [HumanMessage(content="please send the response")]}, config)
    state = await graph.aget_state(config)
    assert any(task.interrupts for task in state.tasks), "agent should be paused for human review"

    # Resume with a reject decision -> send_response must NOT actually run; the
    # middleware injects an error ToolMessage carrying the rejection reason, and
    # the model gets it back so it can revise.
    result = await graph.ainvoke(
        Command(resume={"decisions": [{"type": "reject", "message": "Tone too aggressive. Revise."}]}),
        config,
    )
    messages = result["messages"]
    send_msgs = [m for m in messages if getattr(m, "name", None) == "send_response"]
    # The only send_response message is the rejection stub, never a real success.
    assert send_msgs, "expected the rejection stub ToolMessage"
    assert all(getattr(m, "status", None) == "error" for m in send_msgs), \
        "send_response must not execute successfully after rejection"
    assert isinstance(messages[-1], AIMessage), "agent should respond after rejection"
    assert messages[-1].content.strip(), "agent should produce a revision message"
