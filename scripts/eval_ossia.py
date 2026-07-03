"""Ossia golden-dataset eval CLI — thin HTTP client for the unified API.

Starts the FastAPI app in a subprocess, calls ``POST /v1/eval``, prints the
report. The actual eval logic lives in ``ossia.eval`` and runs inside the
server process; this client is just a presenter.

Usage:
    .venv/bin/python scripts/eval_ossia.py [--dataset tests/golden_dataset.json]
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import find_dotenv, load_dotenv

from core.cli_helper import require_api_key, run_server_subprocess, terminate, wait_for_health

load_dotenv(find_dotenv(usecwd=True))


def _print_report(report: dict, threshold: float) -> int:
    """Pretty-print a /v1/eval response. Returns process exit code."""
    queries = report.get("queries", [])
    print(f"\n{'=' * 70}\nGOLDEN EVAL — {len(queries)} queries (via HTTP)\n{'=' * 70}")
    if report.get("skipped"):
        print(f"  [SKIP] {report.get('skip_reason', 'skipped')}")
        return 0
    for q in queries:
        status = "PASS" if q.get("passed") else "FAIL"
        intent = ",".join(q.get("routed_intents", [])) or "(direct)"
        print(
            f"  [{status}] {q.get('id')} intent={intent} "
            f"match={q.get('expected_intent') if q.get('routed_intents') else 'n/a'} "
            f"missing={q.get('missing_terms') or '-'}"
        )

    passed = sum(1 for q in queries if q.get("passed"))
    rate = report.get("pass_rate", passed / len(queries) if queries else 0.0)
    routed = sum(1 for q in queries if q.get("routed_intents"))
    intent_matches = sum(1 for q in queries if q.get("intent_match"))
    print(f"\n  correctness: {passed}/{len(queries)} ({rate:.0%})")
    print(
        f"  intent routing observed: {routed}/{len(queries)}; "
        f"matched expected: {intent_matches}/{routed}"
    )
    print(f"  threshold: {threshold:.0%}\n")

    if not queries:
        print("  [SKIP] no queries ran")
        return 0
    if rate < threshold:
        print(f"  [FAIL] pass rate {rate:.0%} below threshold {threshold:.0%}")
        return 1
    print("  [OK] pass rate meets threshold")
    return 0


def main() -> int:
    """Start the server, hit /v1/eval, print the report, exit."""
    parser = argparse.ArgumentParser(description="Ossia golden-dataset eval CLI")
    parser.add_argument("--dataset", default="tests/golden_dataset.json")
    parser.add_argument("--min-pass-rate", type=float, default=0.8)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--eval-timeout", type=float, default=600.0)
    args = parser.parse_args()

    api_key = require_api_key()
    env = {**os.environ, "OSSIA_API_KEY": api_key}
    # The eval harness does not exercise HITL; disable to avoid the
    # POSTGRES_URL dependency. Override by exporting ENABLE_HUMAN_REVIEW.
    env.setdefault("ENABLE_HUMAN_REVIEW", "false")

    proc = run_server_subprocess(args.host, args.port, env)
    base_url = f"http://{args.host}:{args.port}"
    try:
        wait_for_health(base_url, args.startup_timeout)
        r = httpx.post(
            f"{base_url}/v1/eval",
            json={"dataset_path": args.dataset, "min_pass_rate": args.min_pass_rate},
            headers={"X-API-Key": api_key},
            timeout=args.eval_timeout,
        )
        if r.status_code != 200:
            print(f"EVAL ABORTED: server returned {r.status_code}: {r.text}", file=sys.stderr)
            return 1
        return _print_report(r.json(), args.min_pass_rate)
    except Exception as exc:  # noqa: BLE001
        print(f"EVAL ABORTED: {exc}", file=sys.stderr)
        return 1
    finally:
        terminate(proc)


if __name__ == "__main__":
    sys.exit(main())
