"""In-process eval harness for the Ossia agent.

``run_eval()`` loads a golden dataset, runs every query through the compiled
agent, grades each against its expected terms, and returns a structured
:class:`EvalReport`. The HTTP ``POST /v1/eval`` endpoint wraps this; the
``scripts/eval_ossia.py`` CLI is a thin HTTP client that calls the endpoint.
"""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from ossia.agent import build_agent_async
from ossia.config import Provider, Settings
from ossia.schemas import EvalQueryResult, EvalReport


def _load_dataset(path: str) -> list[dict[str, Any]]:
    """Load the golden dataset from a JSON file.

    Args:
        path: Path to a JSON file with shape ``{"queries": [...]}``.

    Returns:
        List of golden query records.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["queries"]


def _grade(query: dict[str, Any], final_text: str, routed_intents: list[str]) -> EvalQueryResult:
    """Grade one query against its golden expectation.

    Args:
        query: Golden query record (id, query, expected_intent, expected_terms).
        final_text: The agent's final answer text.
        routed_intents: Subagent names observed via ``task`` tool delegations.

    Returns:
        Graded :class:`EvalQueryResult`.
    """
    text = final_text.lower()
    missing = [t for t in query["expected_terms"] if t.lower() not in text]
    passed = bool(final_text.strip()) and not missing
    return EvalQueryResult(
        id=query["id"],
        expected_intent=query["expected_intent"],
        routed_intents=routed_intents,
        intent_match=(
            query["expected_intent"] in routed_intents if routed_intents else False
        ),
        passed=passed,
        missing_terms=missing,
        answer_preview=final_text[:160],
    )


async def _run_one(
    agent: Any,
    query: dict[str, Any],
    thread_id: str,
) -> EvalQueryResult:
    """Run one golden query through the agent and grade it.

    Args:
        agent: Compiled Ossia agent graph.
        query: Golden query record.
        thread_id: Unique thread id for this run.

    Returns:
        Graded result.
    """
    config = {"configurable": {"thread_id": thread_id}}
    routed_intents: list[str] = []
    final_text = ""

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=query["query"])]},
        config,
        version="v2",
    ):
        kind = event["event"]
        name = event.get("name", "")
        if kind == "on_tool_start" and name == "task":
            args = event.get("data", {}).get("input", {})
            subagent = args.get("subagent_type") or args.get("name") or ""
            if subagent:
                routed_intents.append(str(subagent))
        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            token = getattr(chunk, "content", "") if chunk is not None else ""
            if isinstance(token, str):
                final_text += token

    if not final_text.strip():
        state = await agent.aget_state(config)
        for msg in reversed(state.values.get("messages", [])):
            content = getattr(msg, "content", "")
            if (
                getattr(msg, "type", None) in {"ai", "assistant"}
                and isinstance(content, str)
                and content.strip()
            ):
                final_text = content
                break

    return _grade(query, final_text, routed_intents)


async def run_eval(
    dataset_path: str = "tests/golden_dataset.json",
    min_pass_rate: float = 0.8,
) -> EvalReport:
    """Run the golden eval and return a structured report.

    Args:
        dataset_path: Path to the golden dataset JSON (server-resolvable).
        min_pass_rate: Pass rate threshold (0.0-1.0) below which the report
            is marked not ok.

    Returns:
        Structured :class:`EvalReport` with per-query results and an aggregate
        pass rate.
    """
    queries = _load_dataset(dataset_path)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return EvalReport(
            queries=[],
            pass_rate=0.0,
            threshold=min_pass_rate,
            ok=False,
            skipped=True,
            skip_reason="OPENROUTER_API_KEY is not set",
        )

    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=api_key,
        enable_human_review=False,
        max_revision_loops=3,
    )

    results: list[EvalQueryResult] = []
    async with AsyncExitStack() as stack:
        agent = await stack.enter_async_context(
            build_agent_async(settings=settings, include_mcp_tools=True)
        )
        for q in queries:
            try:
                results.append(await _run_one(agent, q, thread_id=f"eval-{q['id']}"))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    EvalQueryResult(
                        id=q["id"],
                        expected_intent=q["expected_intent"],
                        routed_intents=[],
                        intent_match=False,
                        passed=False,
                        missing_terms=q["expected_terms"],
                        answer_preview=f"<error: {exc}>",
                    )
                )

    passed = sum(1 for r in results if r.passed)
    rate = passed / len(results) if results else 0.0
    return EvalReport(
        queries=results,
        pass_rate=rate,
        threshold=min_pass_rate,
        ok=rate >= min_pass_rate,
    )
