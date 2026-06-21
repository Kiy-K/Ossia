"""Ossia runtime/memory/process audit with LangSmith tracing.

Run: .venv/bin/python scripts/audit_ossia.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from contextlib import AsyncExitStack
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from langchain_core.messages import HumanMessage
from langgraph.store.memory import InMemoryStore

from ossia.agent import build_agent_async
from ossia.config import Provider, Settings, get_settings
from ossia.memory import PostgresMemoryStore, get_checkpointer, get_store


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str, exc: BaseException | None = None) -> None:
    print(f"  [FAIL] {msg}")
    if exc:
        traceback.print_exception(type(exc), exc, exc.__traceback__)


async def audit_memory() -> None:
    """Audit memory: clear errors without Postgres + BaseStore wrapping logic."""
    _section("MEMORY AUDIT")
    settings = get_settings()

    # 1. get_checkpointer must raise a clear error when POSTGRES_URL is unset.
    if not settings.postgres_url:
        try:
            async with get_checkpointer(settings) as _:
                _fail("get_checkpointer should have raised when POSTGRES_URL is unset")
        except ValueError as exc:
            _ok(f"get_checkpointer raises ValueError when POSTGRES_URL unset: {exc}")
        except Exception as exc:  # noqa: BLE001
            _fail("get_checkpointer raised unexpected exception type", exc)
    else:
        _ok("POSTGRES_URL is set; skipping unset-DSN error check")

    # 2. PostgresMemoryStore wraps a BaseStore (InMemoryStore stand-in) correctly.
    store = InMemoryStore()
    await store.aput(("users", "u1"), "pref", {"tone": "concise"})
    mem = PostgresMemoryStore(store)
    got = await mem.get(("users", "u1"), "pref")
    assert got == {"tone": "concise"}, got
    _ok(f"PostgresMemoryStore.get returns item.value: {got}")

    await mem.put(("users", "u1"), "summary", {"last": "reset credentials"})
    got2 = await mem.get(("users", "u1"), "summary")
    assert got2 == {"last": "reset credentials"}, got2
    _ok(f"PostgresMemoryStore.put/get round-trips: {got2}")

    found = await mem.search(("users", "u1"))
    assert any("reset" in str(v) for v in found), found
    _ok(f"PostgresMemoryStore.search returns values: {[str(v)[:40] for v in found]}")

    # 3. get_store also raises a clear error when POSTGRES_URL is unset.
    if not settings.postgres_url:
        try:
            async with get_store(settings) as _:
                _fail("get_store should have raised when POSTGRES_URL is unset")
        except ValueError as exc:
            _ok(f"get_store raises ValueError when POSTGRES_URL unset: {exc}")


async def audit_process_middleware() -> None:
    """Audit process: revision-loop cap and retry middleware behavior."""
    _section("PROCESS AUDIT (middleware)")

    from ossia.middleware import RevisionLoopCapMiddleware, RetryToolMiddleware

    # --- Revision loop cap ---
    cap = RevisionLoopCapMiddleware(max_loops=2)
    cap._counts["default"] = 0
    forced_seen: list[bool] = []

    async def grade_handler(_request: Any) -> Any:
        from langchain_core.messages import ToolMessage

        return ToolMessage(content="grade ok", tool_call_id="t", name="grade_response")

    class _Req:
        def __init__(self, name: str, tid: str = "default") -> None:
            self.tool_call = {"name": name, "id": "t", "args": {}}

    # Simulate 4 grade calls; the 3rd (count=3 > max_loops=2) must force finalize.
    from langchain_core.messages import ToolMessage

    for i in range(1, 5):
        result = await cap.awrap_tool_call(_Req("grade_response"), grade_handler)
        content = result.content if hasattr(result, "content") else str(result)
        is_forced = "send_response immediately" in str(content)
        forced_seen.append(is_forced)
        print(f"    grade call {i}: forced={is_forced} count={cap._counts['default']}")

    assert not forced_seen[0] and not forced_seen[1], "first two grades should pass through"
    assert forced_seen[2], "3rd grade (count>max_loops) should force finalization"
    assert forced_seen[3], "4th grade should still force finalization"
    _ok(f"RevisionLoopCapMiddleware forces finalize after {cap.max_loops} loops")

    # --- Retry middleware ---
    calls: list[int] = []

    async def flaky_handler(_request: Any) -> Any:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        from langchain_core.messages import ToolMessage

        return ToolMessage(content="recovered", tool_call_id="t", name="search_knowledge_base")

    retry = RetryToolMiddleware(max_attempts=3, initial_interval=0.05, backoff_factor=2.0, jitter=False)
    import time as _time
    t0 = _time.monotonic()
    result = await retry.awrap_tool_call(_Req("search_knowledge_base"), flaky_handler)
    elapsed = _time.monotonic() - t0
    assert len(calls) == 3, f"expected 3 attempts, got {len(calls)}"
    assert "recovered" in str(result.content)
    # jitter=False must still apply base delay (>= initial_interval between attempts).
    assert elapsed >= 0.05, f"expected non-zero backoff, elapsed={elapsed}"
    _ok(f"RetryToolMiddleware retried with non-zero backoff (attempts={len(calls)}, elapsed={elapsed:.3f}s)")

    # Retry exhausts and re-raises after max_attempts.
    calls2: list[int] = []

    async def always_fail(_request: Any) -> Any:
        calls2.append(1)
        raise RuntimeError("permanent")

    retry2 = RetryToolMiddleware(max_attempts=3, initial_interval=0.01, backoff_factor=1.0, jitter=False)
    raised = False
    try:
        await retry2.awrap_tool_call(_Req("search_knowledge_base"), always_fail)
    except RuntimeError:
        raised = True
    assert raised and len(calls2) == 3
    _ok(f"RetryToolMiddleware re-raises after max_attempts (attempts={len(calls2)})")

    # Non-external tools are not retried.
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
    assert raised3 and len(calls3) == 1
    _ok("RetryToolMiddleware skips non-external tools (grade_response not retried)")


async def audit_runtime_and_langsmith() -> None:
    """Audit runtime: build agent with MCP, run a real query, stream, trace."""
    _section("RUNTIME AUDIT (end-to-end + LangSmith)")

    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        enable_human_review=False,
        max_revision_loops=3,
    )
    print(f"  provider={settings.provider} model={settings.model}")
    print(f"  langsmith_tracing={os.environ.get('LANGSMITH_TRACING')} "
          f"project={os.environ.get('LANGSMITH_PROJECT')}")

    tool_events: list[str] = []
    stream_events: list[str] = []

    async with AsyncExitStack() as stack:
        agent = await stack.enter_async_context(
            build_agent_async(settings=settings, include_mcp_tools=True)
        )
        print(f"  compiled graph nodes: {list(agent.nodes.keys())}")

        thread_id = "audit-runtime-001"
        config = {"configurable": {"thread_id": thread_id}}

        # Stream events to verify astream_events works and capture tool activity.
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content="What are Nebius Serverless Jobs?")]},
            config,
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")
            if kind == "on_tool_start":
                tool_events.append(name)
            if kind in {"on_chat_model_stream", "on_tool_start", "on_tool_end"}:
                stream_events.append(f"{kind}:{name}")

        print(f"  streamed {len(stream_events)} events")
        print(f"  tools started: {tool_events}")

        # Final state from a fresh invoke for a clean assertion.
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="How do I reset my endpoint credentials?")]},
            {"configurable": {"thread_id": "audit-runtime-002"}},
        )
        final_msgs = result.get("messages", [])
        last = final_msgs[-1] if final_msgs else None
        content = str(getattr(last, "content", ""))[:300]
        print(f"  final message preview: {content!r}")
        assert final_msgs, "agent returned no messages"
        _ok("End-to-end run completed with messages")

    assert stream_events, "no streaming events observed"
    _ok("astream_events produced live events")


async def audit_langsmith_trace() -> None:
    """Audit LangSmith: confirm a trace/run was recorded for the project."""
    _section("LANGSMITH TRACE AUDIT")
    if os.environ.get("LANGSMITH_TRACING") != "true":
        _ok("LANGSMITH_TRACING is not true; skipping trace verification")
        return
    try:
        from langsmith import Client
    except Exception as exc:  # noqa: BLE001
        _fail("langsmith client unavailable", exc)
        return

    try:
        client = Client()
        project = os.environ.get("LANGSMITH_PROJECT", "Ossia")
        runs = list(client.list_runs(project_name=project, limit=5))
        print(f"  recent runs in project '{project}': {len(runs)}")
        for r in runs[:5]:
            print(f"    - {r.run_type}: {r.name} status={r.status} "
                  f"end={r.end_time}")
        if runs:
            _ok(f"LangSmith recorded runs for project '{project}'")
        else:
            _fail(f"no runs found in LangSmith project '{project}' "
                  "(tracing may be async; allow a few seconds)")
    except Exception as exc:  # noqa: BLE001
        _fail("LangSmith trace query failed", exc)


async def audit_fix_verifications() -> None:
    """Verify the second-round review fixes behave correctly."""
    _section("FIX VERIFICATIONS")

    from ossia.config import Settings as _S
    from ossia.middleware import RevisionLoopCapMiddleware

    # 1. MCP graceful degradation: a bad MCP server must not abort agent build.
    import json as _json
    import tempfile as _tf

    bad_cfg = _tf.NamedTemporaryFile("w", suffix=".json", delete=False)  # noqa: SIM115
    _json.dump(
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

    deg_settings = _S(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        enable_human_review=False,
        mcp_config_path=bad_cfg.name,
    )
    async with build_agent_async(settings=deg_settings, include_mcp_tools=True) as agent:
        assert agent is not None
        _ok("build_agent_async degrades gracefully when an MCP server is unreachable")
    os.unlink(bad_cfg.name)

    # 2. Interrupt-without-checkpointer: human review on + no checkpointer must
    #    compile without crashing (interrupt_on is skipped).
    nr_settings = _S(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        enable_human_review=True,
        postgres_url=None,
    )
    from ossia.agent import build_agent

    graph = build_agent(settings=nr_settings, checkpointer=None)
    assert graph is not None
    _ok("Agent compiles with human review on and no checkpointer (interrupts skipped)")

    # 3. Revision counter cleanup: after_agent reclaims the per-thread entry.
    cap = RevisionLoopCapMiddleware(max_loops=2)

    class _Req:
        def __init__(self, name: str) -> None:
            self.tool_call = {"name": name, "id": "t", "args": {}}

    async def _gh(_r: Any) -> Any:
        from langchain_core.messages import ToolMessage

        return ToolMessage(content="ok", tool_call_id="t", name="grade_response")

    # Simulate a run: abefore_agent -> two grades -> aafter_agent.
    # The async hooks exist, so await them (calling without awaiting would
    # leave the reset/cleanup coroutines never-run and the counter unreclaimed).
    await cap.abefore_agent({}, None)
    await cap.awrap_tool_call(_Req("grade_response"), _gh)
    await cap.awrap_tool_call(_Req("grade_response"), _gh)
    assert cap._counts.get("default") == 2, cap._counts
    await cap.aafter_agent({}, None)
    assert "default" not in cap._counts, cap._counts
    _ok("RevisionLoopCapMiddleware reclaims per-thread counters after each run")


async def main() -> None:
    await audit_memory()
    await audit_process_middleware()
    await audit_fix_verifications()
    await audit_runtime_and_langsmith()
    await audit_langsmith_trace()
    _section("AUDIT COMPLETE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        print(f"\nAUDIT ABORTED: {exc}")
        traceback.print_exc()
        sys.exit(1)
