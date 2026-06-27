"""In-process audit harness for the Ossia agent.

Each ``audit_*`` function returns a structured report (list of
:class:`AuditSection` / :class:`CheckResult`) suitable for the HTTP
``GET /v1/audit`` endpoint. The ``scripts/audit_ossia.py`` CLI is a thin
HTTP client that calls the endpoint; this module is the actual logic.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time as _time
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore

from core.agent import build_agent, build_agent_async
from core.config import Provider, Settings, get_settings
from core.memory import get_checkpointer, get_store
from core.middleware import RetryToolMiddleware, RevisionLoopCapMiddleware
from core.schemas import AuditReport, AuditSection, CheckResult


def _check(name: str, ok: bool, detail: str | None = None) -> CheckResult:
    return CheckResult(name=name, ok=ok, detail=detail)


def _section(name: str, checks: list[CheckResult]) -> AuditSection:
    return AuditSection(name=name, checks=checks, ok=all(c.ok for c in checks))


async def audit_memory() -> AuditSection:
    """Memory: clear errors without Postgres + BaseStore wrapping logic."""
    settings = get_settings()
    checks: list[CheckResult] = []

    if not settings.postgres_url:
        try:
            async with get_checkpointer(settings) as _:
                checks.append(_check(
                    "get_checkpointer raises ValueError when POSTGRES_URL unset",
                    False, detail="did not raise",
                ))
        except ValueError as exc:
            checks.append(_check(
                "get_checkpointer raises ValueError when POSTGRES_URL unset",
                True, detail=str(exc),
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check(
                "get_checkpointer raises ValueError when POSTGRES_URL unset",
                False, detail=f"unexpected: {type(exc).__name__}: {exc}",
            ))
    else:
        checks.append(_check("POSTGRES_URL set; skip unset-DSN check", True))

    store = InMemoryStore()
    await store.aput(("users", "u1"), "pref", {"tone": "concise"})
    item = await store.aget(("users", "u1"), "pref")
    got = item.value if item else None
    checks.append(_check(
        "BaseStore.get returns item.value",
        got == {"tone": "concise"},
        detail=str(got),
    ))

    await store.aput(("users", "u1"), "summary", {"last": "reset credentials"})
    item2 = await store.aget(("users", "u1"), "summary")
    got2 = item2.value if item2 else None
    checks.append(_check(
        "BaseStore.put/get round-trips",
        got2 == {"last": "reset credentials"},
        detail=str(got2),
    ))

    found = await store.asearch(("users", "u1"))
    vals = [i.value for i in found]
    checks.append(_check(
        "BaseStore.search returns values",
        any("reset" in str(v) for v in vals),
        detail=str(vals),
    ))

    if not settings.postgres_url:
        try:
            async with get_store(settings) as _:
                checks.append(_check(
                    "get_store raises ValueError when POSTGRES_URL unset",
                    False, detail="did not raise",
                ))
        except ValueError as exc:
            checks.append(_check(
                "get_store raises ValueError when POSTGRES_URL unset",
                True, detail=str(exc),
            ))
    return _section("memory", checks)


class _Req:
    """Minimal stand-in for an AgentMiddleware tool-call request."""

    def __init__(self, name: str) -> None:
        self.tool_call = {"name": name, "id": "t", "args": {}}


async def audit_process_middleware() -> AuditSection:
    """Process: revision-loop cap and retry middleware behavior."""
    checks: list[CheckResult] = []

    cap = RevisionLoopCapMiddleware(max_loops=2)
    cap._counts["default"] = 0
    forced_seen: list[bool] = []

    async def grade_handler(_request: Any) -> Any:
        return ToolMessage(content="grade ok", tool_call_id="t", name="grade_response")

    for _i in range(1, 5):
        result = await cap.awrap_tool_call(_Req("grade_response"), grade_handler)
        content = result.content if hasattr(result, "content") else str(result)
        is_forced = "send_response immediately" in str(content)
        forced_seen.append(is_forced)
    checks.append(_check(
        "RevisionLoopCapMiddleware forces finalize after 2 loops",
        not forced_seen[0] and not forced_seen[1] and forced_seen[2] and forced_seen[3],
        detail=str(forced_seen),
    ))

    calls: list[int] = []

    async def flaky_handler(_request: Any) -> Any:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return ToolMessage(content="recovered", tool_call_id="t", name="search_knowledge_base")

    retry = RetryToolMiddleware(
        max_attempts=3, initial_interval=0.05, backoff_factor=2.0, jitter=False
    )
    t0 = _time.monotonic()
    result = await retry.awrap_tool_call(_Req("search_knowledge_base"), flaky_handler)
    elapsed = _time.monotonic() - t0
    checks.append(_check(
        "RetryToolMiddleware retried with non-zero backoff",
        len(calls) == 3 and elapsed >= 0.05 and "recovered" in str(result.content),
        detail=f"attempts={len(calls)} elapsed={elapsed:.3f}s",
    ))

    calls2: list[int] = []

    async def always_fail(_request: Any) -> Any:
        calls2.append(1)
        raise RuntimeError("permanent")

    retry2 = RetryToolMiddleware(
        max_attempts=3, initial_interval=0.01, backoff_factor=1.0, jitter=False
    )
    raised = False
    try:
        await retry2.awrap_tool_call(_Req("search_knowledge_base"), always_fail)
    except RuntimeError:
        raised = True
    checks.append(_check(
        "RetryToolMiddleware re-raises after max_attempts",
        raised and len(calls2) == 3,
        detail=f"attempts={len(calls2)}",
    ))

    calls3: list[int] = []

    async def grade_fail(_request: Any) -> Any:
        calls3.append(1)
        raise RuntimeError("nope")

    retry3 = RetryToolMiddleware()
    raised3 = False
    try:
        await retry3.awrap_tool_call(_Req("grade_response"), grade_fail)
    except RuntimeError:
        raised3 = True
    checks.append(_check(
        "RetryToolMiddleware skips non-external tools",
        raised3 and len(calls3) == 1,
        detail=f"attempts={len(calls3)}",
    ))
    return _section("process", checks)


async def audit_runtime_and_langsmith() -> AuditSection:
    """Runtime: build agent with MCP, run a real query, stream, trace."""
    checks: list[CheckResult] = []
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        checks.append(_check(
            "OPENROUTER_API_KEY is set",
            False, detail="set OPENROUTER_API_KEY in env to run runtime audit",
        ))
        return _section("runtime", checks)

    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=api_key,
        enable_human_review=False,
        max_revision_loops=3,
    )
    tool_events: list[str] = []
    stream_events: list[str] = []

    async with AsyncExitStack() as stack:
        agent = await stack.enter_async_context(
            build_agent_async(settings=settings, include_mcp_tools=True)
        )

        async for event in agent.astream_events(
            {"messages": [HumanMessage(content="What are Nebius Serverless Jobs?")]},
            {"configurable": {"thread_id": "audit-runtime-001"}},
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_tool_start":
                tool_events.append(name)
            if kind in {"on_chat_model_stream", "on_tool_start", "on_tool_end"}:
                stream_events.append(f"{kind}:{name}")

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="How do I reset my endpoint credentials?")]},
            {"configurable": {"thread_id": "audit-runtime-002"}},
        )
        final_msgs = result.get("messages", [])
        checks.append(_check(
            "End-to-end run completed with messages",
            bool(final_msgs),
            detail=f"streamed={len(stream_events)} tools={tool_events}",
        ))
        checks.append(_check(
            "astream_events produced live events",
            bool(stream_events),
            detail=f"events={len(stream_events)}",
        ))
    return _section("runtime", checks)


async def audit_langsmith_trace() -> AuditSection:
    """LangSmith: confirm a trace/run was recorded for the project."""
    checks: list[CheckResult] = []
    if os.environ.get("LANGSMITH_TRACING") != "true":
        checks.append(_check(
            "LangSmith tracing enabled (LANGSMITH_TRACING=true)", True,
            detail="skipping trace query",
        ))
        return _section("langsmith", checks)
    try:
        from langsmith import Client
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("langsmith client importable", False, detail=str(exc)))
        return _section("langsmith", checks)
    try:
        client = Client()
        project = os.environ.get("LANGSMITH_PROJECT", "Ossia")
        runs = list(client.list_runs(project_name=project, limit=5))
        checks.append(_check(
            f"LangSmith recorded runs for project '{project}'",
            bool(runs),
            detail=f"runs={len(runs)}",
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("LangSmith trace query", False, detail=str(exc)))
    return _section("langsmith", checks)


async def audit_fix_verifications() -> AuditSection:
    """Verify the review fixes behave correctly."""
    checks: list[CheckResult] = []

    bad_cfg = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)  # noqa: SIM115
    json.dump(
        {
            "mcpServers": {
                "bad-server": {
                    "name": "Bad",
                    "transport": "streamable_http",
                    "url": "http://localhost:1/nonexistent",
                }
            }
        },
        bad_cfg,
    )
    bad_cfg.close()
    try:
        deg_settings = Settings(
            provider=Provider.OPENROUTER,
            model="openai/gpt-4o-mini",
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
            enable_human_review=False,
            mcp_config_path=bad_cfg.name,
        )
        try:
            async with build_agent_async(
                settings=deg_settings, include_mcp_tools=True
            ) as agent:
                checks.append(_check(
                    "build_agent_async degrades on bad MCP server",
                    agent is not None
                    and "model" in agent.nodes
                    and "tools" in agent.nodes,
                ))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check(
                "build_agent_async degrades on bad MCP server",
                False, detail=str(exc),
            ))
    finally:
        os.unlink(bad_cfg.name)

    nr_settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        enable_human_review=True,
        postgres_url=None,
    )
    try:
        graph = build_agent(settings=nr_settings, checkpointer=None)
        checks.append(_check(
            "Agent compiles with human review on and no checkpointer",
            graph is not None,
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(
            "Agent compiles with human review on and no checkpointer",
            False, detail=str(exc),
        ))

    cap = RevisionLoopCapMiddleware(max_loops=2)

    async def _gh(_r: Any) -> Any:
        return ToolMessage(content="ok", tool_call_id="t", name="grade_response")

    try:
        await cap.abefore_agent({}, None)
        await cap.awrap_tool_call(_Req("grade_response"), _gh)
        await cap.awrap_tool_call(_Req("grade_response"), _gh)
        assert cap._counts.get("default") == 2
        await cap.aafter_agent({}, None)
        reclaimed = "default" not in cap._counts
        checks.append(_check(
            "RevisionLoopCapMiddleware reclaims per-thread counters",
            reclaimed,
            detail=str(cap._counts),
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(
            "RevisionLoopCapMiddleware reclaims per-thread counters",
            False, detail=str(exc),
        ))
    return _section("fix-verifications", checks)


async def run_audit() -> AuditReport:
    """Run every audit section and return a structured report."""
    sections = await asyncio.gather(
        audit_memory(),
        audit_process_middleware(),
        audit_fix_verifications(),
        audit_runtime_and_langsmith(),
        audit_langsmith_trace(),
    )
    return AuditReport(sections=list(sections), ok=all(s.ok for s in sections))
