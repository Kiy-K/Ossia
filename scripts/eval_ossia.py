"""Golden-dataset evaluation harness for the Ossia support agent.

Run: .venv/bin/python scripts/eval_ossia.py [--dataset tests/golden_dataset.json]

Each golden query is run end-to-end against the real agent (MCP + intent
subagents). A query passes when the agent produces a non-empty final answer
that contains every expected key term (case-insensitive). Intent routing is
observed via `task` tool delegations and reported alongside, but is not a
hard failure (LLM routing varies); the correctness signal is the answer.

Exits non-zero when the pass rate falls below --min-pass-rate (default 0.8),
so this is suitable as a Nebius eval Job gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from langchain_core.messages import HumanMessage

from ossia.agent import build_agent_async
from ossia.config import Provider, Settings


def _load_dataset(path: str) -> list[dict[str, Any]]:
    """Load the golden dataset from a JSON file.

    Args:
        path: Path to the golden dataset JSON.

    Returns:
        List of query records.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["queries"]


def _grade(query: dict[str, Any], final_text: str, routed_intents: list[str]) -> dict[str, Any]:
    """Grade one query against its golden expectation.

    Args:
        query: Golden query record with expected_terms.
        final_text: The agent's final answer text (lowercased).
        routed_intents: Subagent names observed via `task` delegations.

    Returns:
        Result dict with pass/fail and diagnostic detail.
    """
    text = final_text.lower()
    missing = [t for t in query["expected_terms"] if t.lower() not in text]
    passed = bool(final_text.strip()) and not missing
    return {
        "id": query["id"],
        "expected_intent": query["expected_intent"],
        "routed_intents": routed_intents,
        "intent_match": query["expected_intent"] in routed_intents if routed_intents else False,
        "passed": passed,
        "missing_terms": missing,
        "answer_preview": final_text[:160],
    }


async def _run_one(
    agent: Any,
    query: dict[str, Any],
    thread_id: str,
) -> dict[str, Any]:
    """Run one golden query through the agent and capture intent + final answer.

    Args:
        agent: Compiled Ossia agent graph.
        query: Golden query record.
        thread_id: Unique thread id for this run.

    Returns:
        Graded result dict.
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
        # The `task` tool delegates to a subagent; capture which specialist ran.
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

    # Fallback: if streaming tokens didn't accumulate (some providers batch),
    # pull the final assistant message from state.
    if not final_text.strip():
        state = await agent.aget_state(config)
        for msg in reversed(state.values.get("messages", [])):
            content = getattr(msg, "content", "")
            if getattr(msg, "type", None) in {"ai", "assistant"} and isinstance(content, str) and content.strip():
                final_text = content
                break

    return _grade(query, final_text, routed_intents)


async def main() -> int:
    """Run the golden eval and return a process exit code."""
    parser = argparse.ArgumentParser(description="Ossia golden-dataset eval harness")
    parser.add_argument("--dataset", default="tests/golden_dataset.json")
    parser.add_argument("--min-pass-rate", type=float, default=0.8)
    args = parser.parse_args()

    queries = _load_dataset(args.dataset)
    print(f"\n{'=' * 70}\nGOLDEN EVAL — {len(queries)} queries\n{'=' * 70}")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("  [SKIP] OPENROUTER_API_KEY is not set; cannot run live eval. Set it in .env.")
        return 0

    settings = Settings(
        provider=Provider.OPENROUTER,
        model="openai/gpt-4o-mini",
        openrouter_api_key=api_key,
        enable_human_review=False,
        max_revision_loops=3,
    )

    results: list[dict[str, Any]] = []
    async with AsyncExitStack() as stack:
        agent = await stack.enter_async_context(
            build_agent_async(settings=settings, include_mcp_tools=True)
        )

        for q in queries:
            try:
                res = await _run_one(agent, q, thread_id=f"eval-{q['id']}")
            except Exception as exc:  # noqa: BLE001
                res = {
                    "id": q["id"],
                    "expected_intent": q["expected_intent"],
                    "routed_intents": [],
                    "intent_match": False,
                    "passed": False,
                    "missing_terms": q["expected_terms"],
                    "answer_preview": f"<error: {exc}>",
                }
            results.append(res)
            status = "PASS" if res["passed"] else "FAIL"
            intent = ",".join(res["routed_intents"]) or "(direct)"
            print(
                f"  [{status}] {res['id']} intent={intent} "
                f"match={res['expected_intent'] if res['routed_intents'] else 'n/a'} "
                f"missing={res['missing_terms'] or '-'}"
            )

    passed = sum(1 for r in results if r["passed"])
    rate = passed / len(results) if results else 0.0
    routed = sum(1 for r in results if r["routed_intents"])
    intent_matches = sum(1 for r in results if r["intent_match"])
    print(f"\n  correctness: {passed}/{len(results)} ({rate:.0%})")
    print(f"  intent routing observed: {routed}/{len(results)}; matched expected: {intent_matches}/{routed}")
    print(f"  threshold: {args.min_pass_rate:.0%}\n")

    if rate < args.min_pass_rate:
        print(f"  [FAIL] pass rate {rate:.0%} below threshold {args.min_pass_rate:.0%}")
        return 1
    print("  [OK] pass rate meets threshold")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001
        print(f"\nEVAL ABORTED: {exc}")
        traceback.print_exc()
        sys.exit(1)
