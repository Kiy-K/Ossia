"""Ossia audit CLI — thin HTTP client for the unified API.

Starts the FastAPI app in a subprocess, calls ``GET /v1/audit``, prints the
structured report, and tears the server down. The actual audit logic lives
in ``ossia.audit`` and runs inside the server process; this client is just
a presenter.

Usage:
    .venv/bin/python scripts/audit_ossia.py
"""

from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import find_dotenv, load_dotenv

from ossia.cli_helper import require_api_key, run_server_subprocess, terminate, wait_for_health

load_dotenv(find_dotenv(usecwd=True))


def _print_report(report: dict) -> int:
    """Pretty-print a /v1/audit response. Returns process exit code."""
    print(f"\n{'=' * 70}\nAUDIT REPORT (via HTTP)\n{'=' * 70}")
    exit_code = 0 if report.get("ok") else 1
    for section in report.get("sections", []):
        print(f"\n[{section['name'].upper()}]")
        for check in section.get("checks", []):
            tag = "OK" if check.get("ok") else "FAIL"
            line = f"  [{tag:4s}] {check.get('name')}"
            if check.get("detail"):
                line += f"  -- {check['detail']}"
            print(line)
            if not check.get("ok"):
                exit_code = 1
    print(f"\n{'=' * 70}\nAUDIT {'PASS' if report.get('ok') else 'FAIL'}\n{'=' * 70}")
    return exit_code


def main() -> int:
    """Start the server, hit /v1/audit, print the report, exit."""
    parser = argparse.ArgumentParser(description="Ossia audit CLI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    parser.add_argument("--audit-timeout", type=float, default=300.0)
    args = parser.parse_args()

    api_key = require_api_key()
    env = {**os.environ, "OSSIA_API_KEY": api_key}
    # The audit harness does not exercise HITL; disable to avoid the
    # POSTGRES_URL dependency. Override by exporting ENABLE_HUMAN_REVIEW.
    env.setdefault("ENABLE_HUMAN_REVIEW", "false")

    proc = run_server_subprocess(args.host, args.port, env)
    base_url = f"http://{args.host}:{args.port}"
    try:
        wait_for_health(base_url, args.startup_timeout)
        r = httpx.get(
            f"{base_url}/v1/audit",
            headers={"X-API-Key": api_key},
            timeout=args.audit_timeout,
        )
        if r.status_code != 200:
            print(f"AUDIT ABORTED: server returned {r.status_code}: {r.text}",
                  file=sys.stderr)
            return 1
        return _print_report(r.json())
    except Exception as exc:  # noqa: BLE001
        print(f"AUDIT ABORTED: {exc}", file=sys.stderr)
        return 1
    finally:
        terminate(proc)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
