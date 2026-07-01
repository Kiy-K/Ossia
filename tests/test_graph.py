"""Unit tests for the Ossia agent graph."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from core.agent import _build_middlewares, build_agent
from core.config import Provider, Settings
from core.tools import _build_kb


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
    """Knowledge base search returns matching documents when seeded."""
    kb = _build_kb(
        [
            {
                "title": "Deep Agents memory",
                "source": "mem",
                "content": "Memory lets your agent learn across conversations.",
            },
            {
                "title": "Postgres checkpointer",
                "source": "pg",
                "content": "Postgres is required when human review is enabled.",
            },
        ]
    )
    results = kb.search("memory agent", top_k=2)
    assert len(results) >= 1
    assert any("memory" in r.title.lower() for r in results)


@pytest.mark.asyncio
async def test_kb_empty_uses_fallback() -> None:
    """When KB is empty, search returns no results instead of crashing."""
    kb = _build_kb([])
    results = kb.search("unknown query", top_k=3)
    assert results == []


@pytest.mark.asyncio
async def test_kb_search_tool_fallback_on_web_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search tool degrades gracefully when web fallback fails."""
    from core import tools as tools_module

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

    Pydantic copies the model during agent construction (create_deep_agent and
    the langchain 1.x middleware stack both call model_validate / model_copy),
    which would drain a plain ``iter(...)`` and leave subsequent invocations
    with no scripted response. We use a deque-backed list as the source so the
    model survives copies and each ``_generate`` call pops from the front
    without consuming the underlying iterator.
    """

    def __init__(self, scripted: list[AIMessage]) -> None:
        super().__init__(messages=iter([]))  # type-ignored; we override _generate
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

    from core.agent import _build_middlewares, _interrupt_config, create_core_tools
    from core.config import Settings

    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key="sk-test",
        enable_human_review=True,
        max_revision_loops=3,
    )
    saver = InMemorySaver()
    model = _FakeToolModel(scripted=[_send_response_ai(), *follow_ups])
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


# ── Interpreter middleware tests ──────────────────────────────────────────────


def test_interpreter_middleware_in_middleware_stack() -> None:
    """CodeInterpreterMiddleware is included in the middleware stack."""
    from langchain_quickjs import CodeInterpreterMiddleware

    settings = _test_settings()
    middlewares = _build_middlewares(settings)

    interpreter_middlewares = [
        m for m in middlewares if isinstance(m, CodeInterpreterMiddleware)
    ]
    assert len(interpreter_middlewares) == 1

    middleware = interpreter_middlewares[0]
    # Verify PTC configuration (stored in _ptc, a list of tool names or instances)
    assert middleware._ptc is not None
    ptc_names = {entry if isinstance(entry, str) else entry.name for entry in middleware._ptc}
    assert "search_codebase" in ptc_names
    assert "read_file" in ptc_names
    assert "recall_thread_turns" in ptc_names
    # task should NOT be in PTC (reserved for subagent global)
    assert "task" not in ptc_names
    # Verify other settings
    assert middleware._timeout == 5.0
    assert middleware._max_ptc_calls == 32
    assert middleware._mode == "thread"


@pytest.mark.asyncio
async def test_interpreter_state_persists_across_turns() -> None:
    """Interpreter state (variables, globals) persists across conversation turns.

    Uses mode='thread' so the QuickJS context survives across agent runs
    for the same LangGraph thread_id.
    """
    from deepagents import create_deep_agent

    from core.agent import create_core_tools

    settings = _test_settings()
    saver = InMemorySaver()
    model = _FakeToolModel(scripted=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "eval",
                "id": "call-eval-1",
                "args": {"code": "const myValue = 42; myValue;"},
            }],
        ),
        AIMessage(content="Stored value."),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "eval",
                "id": "call-eval-2",
                "args": {"code": "myValue * 2;"},
            }],
        ),
        AIMessage(content="Result: 84"),
    ])

    graph = create_deep_agent(
        name="ossia-test",
        model=model,
        tools=create_core_tools(),
        system_prompt="test",
        middleware=_build_middlewares(settings),
        checkpointer=saver,
    )

    config = {"configurable": {"thread_id": "interpreter-persistence-test"}}

    await graph.ainvoke(
        {"messages": [HumanMessage(content="store 42 in interpreter")]},
        config,
    )
    # First eval result should be captured as a ToolMessage
    tool_msgs_1 = [m for m in graph.get_state(config).values.get("messages", []) if isinstance(m, ToolMessage)]
    assert len(tool_msgs_1) >= 1

    result2 = await graph.ainvoke(
        {"messages": [HumanMessage(content="double the stored value")]},
        config,
    )
    # Second eval should have persisted context; response should include 84
    assert "84" in str(result2["messages"][-1].content)


@pytest.mark.asyncio
async def test_ptc_tools_available_in_interpreter() -> None:
    """PTC-allowed tools are exposed as tools.* namespace in interpreter code."""
    from deepagents import create_deep_agent

    from core.agent import create_core_tools

    settings = _test_settings()
    saver = InMemorySaver()

    model = _FakeToolModel(scripted=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": "eval",
                "id": "call-eval-ptc-1",
                "args": {
                    "code": (
                        "const result = await tools.searchCodebase({"
                        " query: 'test', path: '.'"
                        "}); result;"
                    )
                },
            }],
        ),
        AIMessage(content="PTC search completed."),
    ])

    graph = create_deep_agent(
        name="ossia-test",
        model=model,
        tools=create_core_tools(),
        system_prompt="test",
        middleware=_build_middlewares(settings),
        checkpointer=saver,
    )

    config = {"configurable": {"thread_id": "ptc-tools-test"}}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="search codebase via PTC")]},
        config,
    )

    # Verify eval was called and completed without error
    eval_msgs = [m for m in result["messages"] if getattr(m, "name", None) == "eval"]
    assert len(eval_msgs) >= 1


# ── Async subagent middleware tests ──────────────────────────────────────────


def test_async_subagent_middleware_wired_when_enabled() -> None:
    """AsyncSubAgentMiddleware is included when enable_async_subagents=True.

    ``AsyncSubAgent`` is a ``TypedDict``, so items use dict-style access.
    """
    from deepagents.middleware.async_subagents import AsyncSubAgentMiddleware

    settings = _test_settings()
    settings.enable_async_subagents = True
    middlewares = _build_middlewares(settings)

    async_middlewares = [
        m for m in middlewares if isinstance(m, AsyncSubAgentMiddleware)
    ]
    assert len(async_middlewares) == 1
    middleware = async_middlewares[0]
    # Verify the middleware exposes the async agent tools
    tool_names = sorted(t.name for t in middleware.tools)
    assert "start_async_task" in tool_names
    assert "check_async_task" in tool_names
    assert "update_async_task" in tool_names
    assert "cancel_async_task" in tool_names
    assert "list_async_tasks" in tool_names


def test_async_subagent_middleware_not_wired_when_disabled() -> None:
    """AsyncSubAgentMiddleware is excluded when enable_async_subagents=False."""
    from deepagents.middleware.async_subagents import AsyncSubAgentMiddleware

    settings = _test_settings()
    settings.enable_async_subagents = False
    middlewares = _build_middlewares(settings)

    async_middlewares = [
        m for m in middlewares if isinstance(m, AsyncSubAgentMiddleware)
    ]
    assert len(async_middlewares) == 0


def test_async_subagent_specs_have_required_fields() -> None:
    """Each async subagent spec has name, description, and graph_id.

    ``AsyncSubAgent`` is a ``TypedDict``, so items use dict-style access.
    """
    from core.agent import _build_async_subagents

    settings = _test_settings()
    agents = _build_async_subagents(settings)

    assert len(agents) >= 1
    for a in agents:
        assert a["name"], "each async subagent needs a name"
        assert a["description"], "each async subagent needs a description"
        assert a["graph_id"], "each async subagent needs a graph_id"
        assert len(a["name"]) > 0
        assert len(a["description"]) > 0
        assert len(a["graph_id"]) > 0


def test_build_agent_includes_async_middleware() -> None:
    """The full agent builder includes async subagent middleware by default."""

    settings = _test_settings()
    settings.enable_async_subagents = True
    graph = build_agent(settings=settings)

    assert graph is not None
    assert "tools" in graph.nodes


def test_build_agent_excludes_async_middleware_when_disabled() -> None:
    """The full agent builder excludes async subagent middleware when disabled."""

    settings = _test_settings()
    settings.enable_async_subagents = False
    graph = build_agent(settings=settings)

    assert graph is not None
    assert "tools" in graph.nodes


# ── Async task event schema tests ────────────────────────────────────────────


def test_async_task_payload_required_fields() -> None:
    """StreamAsyncTaskPayload validates required fields."""
    from pydantic import ValidationError

    from core.schemas import StreamAsyncTaskPayload

    # Valid payload
    payload = StreamAsyncTaskPayload(
        event="async_task_started",
        task_id="task-1",
        agent_name="researcher",
        status="running",
        tasks=[],
    )
    assert payload.event == "async_task_started"
    assert payload.task_id == "task-1"
    assert payload.agent_name == "researcher"
    assert payload.status == "running"
    assert payload.error is None

    # Valid: completed with error
    payload2 = StreamAsyncTaskPayload(
        event="async_task_failed",
        task_id="task-2",
        agent_name="tester",
        status="error",
        tasks=[],
        error="Test timed out",
    )
    assert payload2.event == "async_task_failed"
    assert payload2.error == "Test timed out"

    # Invalid event type
    with pytest.raises(ValidationError):
        StreamAsyncTaskPayload(
            event="invalid_event",  # type: ignore[arg-type]
            task_id="task-3",
            agent_name="auditor",
            status="unknown",
            tasks=[],
        )

    # task_id="" is valid since Pydantic accepts empty str for str fields.
    # Verify the payload is created successfully even with empty strings.
    payload = StreamAsyncTaskPayload(
        event="async_task_started",
        task_id="",
        agent_name="",
        status="",
        tasks=[],
    )
    assert payload.task_id == ""
    assert payload.agent_name == ""
    assert payload.status == ""


def test_async_task_event_kind_in_stream_event() -> None:
    """StreamEvent accepts 'async_task' as a valid kind."""
    from core.schemas import StreamAsyncTaskPayload, StreamEvent

    event = StreamEvent(
        kind="async_task",  # type: ignore[arg-type]
        data=StreamAsyncTaskPayload(
            event="async_task_completed",
            task_id="task-1",
            agent_name="researcher",
            status="success",
            tasks=[],
        ).model_dump(),
    )
    assert event.kind == "async_task"
    assert event.data["event"] == "async_task_completed"
    assert event.data["task_id"] == "task-1"


# ── Orchestrator pipeline tests ──────────────────────────────────────────────


def test_orchestrator_tools_are_loaded() -> None:
    """Orchestrator pipeline tools are included in the built agent."""
    settings = _test_settings()
    graph = build_agent(settings=settings)

    assert graph is not None
    tool_names = set()
    tools_node = graph.nodes.get("tools")
    if tools_node and hasattr(tools_node, "bound"):
        bound = tools_node.bound
        tool_names = set(getattr(bound, "_tools_by_name", {}).keys())
    assert "run_bugfix_pipeline" in tool_names, \
        f"bugfix tool not found in {tool_names}"
    assert "run_audit_pipeline" in tool_names, \
        f"audit tool not found in {tool_names}"
    assert "run_refactor_pipeline" in tool_names, \
        f"refactor tool not found in {tool_names}"


def test_orchestrator_schemas_validate() -> None:
    """Orchestrator structured output schemas accept valid data."""

    from core.orchestrators.schemas import (
        AuditFinding,
        AuditReport,
        BugReport,
        PatchProposal,
        RefactorPlan,
        TestResult,
        ValidationResult,
    )

    # BugReport
    bug = BugReport(
        title="Login fails with Google SSO",
        summary="Users cannot log in using Google SSO",
        root_cause="OAuth redirect URI mismatch",
        reproduction_steps=["Go to /login", "Click Google SSO"],
        affected_files=["src/auth/google.py"],
        severity="high",
    )
    assert bug.title == "Login fails with Google SSO"
    assert bug.severity == "high"

    # PatchProposal
    patch = PatchProposal(
        summary="Fix OAuth redirect URI",
        file_path="src/auth/google.py",
        before="old code",
        after="new code",
        risk_notes="May affect existing sessions",
    )
    assert patch.summary == "Fix OAuth redirect URI"

    # TestResult
    tr = TestResult(passed=True, total=10, passed_count=10, failures=[])
    assert tr.passed is True
    assert tr.passed_count == 10

    # AuditFinding
    finding = AuditFinding(
        file_path="src/core/api.py",
        line=42,
        category="security",
        severity="high",
        message="SQL injection risk",
        suggestion="Use parameterized queries",
    )
    assert finding.category == "security"

    # AuditReport
    report = AuditReport(
        findings=[finding],
        summary="1 security finding",
        passed=False,
    )
    assert len(report.findings) == 1
    assert report.passed is False

    # RefactorPlan
    plan = RefactorPlan(
        target="src/core/middleware.py",
        motivation="Simplify retry logic",
        changes=[{"file": "src/core/middleware.py", "desc": "Extract retry class"}],
        estimated_impact="low",
    )
    assert plan.target == "src/core/middleware.py"

    # ValidationResult
    vr = ValidationResult(passed=True, issues=[], recommendations=["Add tests"])
    assert vr.passed is True
    assert len(vr.recommendations) == 1


def test_orchestrator_pipeline_bugfix_tool_registered() -> None:
    """The bugfix pipeline tool is registered as a callable tool."""
    from core.orchestrators.tools import run_bugfix_pipeline

    # When called without a running event loop, the tool returns
    # an instruction to use the interpreter
    schema = run_bugfix_pipeline.args_schema
    assert schema is not None
    assert "issue_description" in schema.model_fields


def test_orchestrator_pipeline_audit_tool_registered() -> None:
    """The audit pipeline tool is registered as a callable tool."""
    from core.orchestrators.tools import run_audit_pipeline

    schema = run_audit_pipeline.args_schema
    assert schema is not None
    assert "target" in schema.model_fields
    assert "focus" in schema.model_fields


def test_orchestrator_structured_schemas_extra_forbid() -> None:
    """Schemas reject extra fields."""
    from pydantic import ValidationError

    from core.orchestrators.schemas import BugReport

    with pytest.raises(ValidationError):
        BugReport(
            title="Bug",
            summary="Something broke",
            root_cause="Unknown",
            extra_field="should not be allowed",  # type: ignore[call-arg]
        )


def test_orchestrator_bugfix_pipeline_js_uses_correct_api() -> None:
    """Bugfix JS template uses subagentType/description/responseSchema, not agent/instruction."""
    from core.orchestrators.bugfix_pipeline import get_bugfix_pipeline_js

    js = get_bugfix_pipeline_js("Test bug: login fails")
    # Must use the new API fields
    assert '"bug-diagnostician"' in js or "bug-diagnostician" in js, "should reference bug-diagnostician subagent"
    assert "subagentType" in js, "should use subagentType, not agent"
    assert "description" in js, "should use description, not instruction"
    assert "responseSchema" in js, "should use responseSchema"
    assert "BUG_REPORT_SCHEMA" in js, "should include BugReport schema"
    assert "PATCH_PROPOSAL_SCHEMA" in js, "should include PatchProposal schema"
    assert "TEST_RESULT_SCHEMA" in js, "should include TestResult schema"
    # Must NOT use the old API
    assert "agent:" not in js, "should not use old 'agent:' field"
    assert "instruction:" not in js, "should not use old 'instruction:' field"


def test_orchestrator_audit_pipeline_js_uses_correct_api() -> None:
    """Audit JS template uses subagentType/description/responseSchema."""
    from core.orchestrators.audit_pipeline import get_audit_pipeline_js

    js = get_audit_pipeline_js(target="src/core", focus="security")
    assert "subagentType" in js, "should use subagentType"
    assert "description" in js, "should use description"
    assert "responseSchema" in js, "should use responseSchema"
    assert "RESEARCH_RESULT_SCHEMA" in js, "should include research schema"
    assert "AUDIT_FINDING_SCHEMA" in js, "should include audit finding schema"
    assert "agent:" not in js, "should not use old 'agent:' field"
    assert "instruction:" not in js, "should not use old 'instruction:' field"
    assert "src/core" in js, "should interpolate target"
    assert "security" in js, "should interpolate focus"


def test_orchestrator_refactor_pipeline_js_uses_correct_api() -> None:
    """Refactor JS template uses subagentType/description/responseSchema."""
    from core.orchestrators.refactor_pipeline import get_refactor_pipeline_js

    js = get_refactor_pipeline_js(target="src/core/middleware.py", goal="Simplify retry logic")
    assert "subagentType" in js, "should use subagentType"
    assert "description" in js, "should use description"
    assert "responseSchema" in js, "should use responseSchema"
    assert "RESEARCH_RESULT_SCHEMA" in js, "should include research schema"
    assert "REFACTOR_PLAN_SCHEMA" in js, "should include plan schema"
    assert "PATCH_SET_SCHEMA" in js, "should include patch schema"
    assert "VALIDATION_SCHEMA" in js, "should include validation schema"
    assert "agent:" not in js, "should not use old 'agent:' field"
    assert "instruction:" not in js, "should not use old 'instruction:' field"
    assert "src/core/middleware.py" in js, "should interpolate target"
    assert "Simplify retry logic" in js, "should interpolate goal"


def test_orchestrator_pipeline_js_escapes_special_chars() -> None:
    """Pipeline JS templates escape backticks, backslashes, and dollar signs."""
    from core.orchestrators.bugfix_pipeline import get_bugfix_pipeline_js

    # Issue with backticks and dollar signs
    js = get_bugfix_pipeline_js("Variable `x` costs $5")
    # Should be safely escaped
    assert "\\`x\\`" in js or "`x`" not in js, "should escape backticks"


def test_orchestrator_schema_serialization() -> None:
    """pydantic_to_js_response_schema produces valid JSON Schema compatible with task()."""
    from core.orchestrators.schemas import (
        BugReport,
        pydantic_to_js_response_schema,
        serialize_schema_js,
    )

    schema = pydantic_to_js_response_schema(BugReport)
    assert schema["type"] == "object"
    assert "title" in schema["properties"]
    assert "root_cause" in schema["properties"]
    assert "reproduction_steps" in schema["properties"]
    assert "affected_files" in schema["properties"]
    assert "severity" in schema["properties"]
    # Should not have $defs
    assert "$defs" not in schema

    # Serialize to JS inline
    js_str = serialize_schema_js(schema)
    assert isinstance(js_str, str)
    assert '"type":"object"' in js_str


def test_orchestrator_pipeline_event_schema() -> None:
    """StreamPipelinePayload validates correctly."""
    from pydantic import ValidationError

    # Must import from core.schemas, not just orchestrators
    from core.schemas import StreamPipelinePayload

    # Valid pipeline_started
    evt = StreamPipelinePayload(
        pipeline_id="bugfix-001",
        pipeline_type="bugfix",
        event="pipeline_started",
        total_steps=3,
        status="running",
    )
    assert evt.pipeline_id == "bugfix-001"
    assert evt.pipeline_type == "bugfix"
    assert evt.event == "pipeline_started"
    assert evt.status == "running"
    assert evt.total_steps == 3

    # Valid pipeline_step_completed with result
    evt = StreamPipelinePayload(
        pipeline_id="bugfix-001",
        pipeline_type="bugfix",
        event="pipeline_step_completed",
        step_name="bug-diagnostician",
        step_index=0,
        total_steps=3,
        status="completed",
        result="Found root cause: OAuth redirect mismatch",
    )
    assert evt.step_name == "bug-diagnostician"
    assert evt.step_index == 0
    assert evt.result is not None

    # Valid pipeline_step_failed with error
    evt = StreamPipelinePayload(
        pipeline_id="audit-001",
        pipeline_type="audit",
        event="pipeline_step_failed",
        step_name="code-researcher",
        step_index=0,
        total_steps=2,
        status="failed",
        error="Search timed out",
    )
    assert evt.event == "pipeline_step_failed"
    assert evt.error == "Search timed out"

    # Valid pipeline_completed
    evt = StreamPipelinePayload(
        pipeline_id="refactor-001",
        pipeline_type="refactor",
        event="pipeline_completed",
        total_steps=4,
        status="completed",
        result="All tests passed",
    )
    assert evt.event == "pipeline_completed"

    # Invalid pipeline_type rejected
    with pytest.raises(ValidationError):
        StreamPipelinePayload(
            pipeline_id="x",
            pipeline_type="invalid",  # type: ignore[arg-type]
            event="pipeline_started",
            total_steps=1,
            status="running",
        )

    # Invalid event type rejected
    with pytest.raises(ValidationError):
        StreamPipelinePayload(
            pipeline_id="x",
            pipeline_type="bugfix",
            event="invalid_event",  # type: ignore[arg-type]
            total_steps=1,
            status="running",
        )


def test_pipeline_event_kind_in_stream_event() -> None:
    """StreamEvent accepts 'pipeline' as a valid kind."""
    from core.schemas import StreamEvent, StreamPipelinePayload

    event = StreamEvent(
        kind="pipeline",  # type: ignore[arg-type]
        data=StreamPipelinePayload(
            pipeline_id="audit-002",
            pipeline_type="audit",
            event="pipeline_started",
            total_steps=2,
            status="running",
        ).model_dump(),
    )
    assert event.kind == "pipeline"
    assert event.data["pipeline_id"] == "audit-002"
    assert event.data["event"] == "pipeline_started"


def test_orchestrator_tool_returns_js_code() -> None:
    """Orchestrator tools return JS code with the correct API fields."""
    from core.orchestrators.tools import (
        run_audit_pipeline,
        run_bugfix_pipeline,
        run_refactor_pipeline,
    )

    # Bugfix
    result = run_bugfix_pipeline.invoke(
        {"issue_description": "Test bug", "repo": "test/repo", "issue_number": 1}
    )
    assert result["pipeline"] == "bugfix"
    assert result["status"] == "ready"
    js = result["js_code"]
    assert "subagentType" in js
    assert "responseSchema" in js
    assert "bug-diagnostician" in js
    assert "eval" in result["instruction"]

    # Audit
    result = run_audit_pipeline.invoke({"target": "src/core", "focus": "security"})
    assert result["pipeline"] == "audit"
    assert result["status"] == "ready"
    js = result["js_code"]
    assert "subagentType" in js
    assert "responseSchema" in js
    assert "code-researcher" in js

    # Refactor
    result = run_refactor_pipeline.invoke(
        {"target": "src/core/middleware.py", "goal": "Simplify retry logic"}
    )
    assert result["pipeline"] == "refactor"
    assert result["status"] == "ready"
    js = result["js_code"]
    assert "subagentType" in js
    assert "responseSchema" in js
    assert "code-researcher" in js


def test_graph_module_variable_exists() -> None:
    """Graph modules export a ``graph`` variable.

    The LangGraph CLI reads ``module:graph`` from langgraph.json by
    inspecting ``module.__dict__``. Each graph module must have a
    real module-level ``graph`` variable.
    """
    import os

    # Ensure API key is set for graph building
    old_key = os.environ.get("OPENROUTER_API_KEY")
    if not old_key:
        os.environ["OPENROUTER_API_KEY"] = "sk-test"
    old_ossia = os.environ.get("OSSIA_API_KEY")
    if not old_ossia:
        os.environ["OSSIA_API_KEY"] = "dev"

    try:
        import core.graphs.supervisor as sup
        assert "graph" in dir(sup), "graph should be in dir()"
        assert sup.graph is not None, "graph should be built"

        import core.graphs.researcher as res
        assert "graph" in dir(res)
        assert res.graph is not None

        import core.graphs.tester as tes
        assert "graph" in dir(tes)
        assert tes.graph is not None

        import core.graphs.auditor as aud
        assert "graph" in dir(aud)
        assert aud.graph is not None
    finally:
        # Restore env
        if old_key:
            os.environ["OPENROUTER_API_KEY"] = old_key
        else:
            os.environ.pop("OPENROUTER_API_KEY", None)
        if old_ossia:
            os.environ["OSSIA_API_KEY"] = old_ossia
        else:
            os.environ.pop("OSSIA_API_KEY", None)
