"""Unified ``ossia`` CLI — starts backend + TUI from a single command, or
runs a subcommand.

Usage:

    ossia                          Start backend + TUI (default; no subcommand)
    ossia server [--port 8000]     Start only the backend server
    ossia tui                      Start only the TUI (backend must already run)
    ossia doctor                   Health check: env, API keys, plugins, server
    ossia plugins list             List loaded plugins (matches GET /v1/plugins)

Subcommands are intentionally minimal — they're diagnostic, not
operational. Run the server with ``ossia`` or ``ossia server`` and
manage it from there. Ponytail: the subcommands are read-only where
possible; ``doctor`` and ``plugins list`` never start a server.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import httpx
from dotenv import find_dotenv, load_dotenv

from core.cli_helper import require_api_key, terminate, wait_for_health
from core.plugin import discover_plugins
from core.plugin_config import load_ossia_config

load_dotenv(find_dotenv(usecwd=True))

# ── Find the project root (where src/tui/ lives) ────────────────────────────
# The package is installed as "ossia" from the repo root, so we walk up from
# the installed location.  Fall back to PWD or CWD for development setups.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if not os.path.isdir(os.path.join(_PROJECT_ROOT, "src", "tui")):
    _PROJECT_ROOT = os.getcwd()

_TUI_DIR = os.path.join(_PROJECT_ROOT, "src", "tui")


def _find_bun() -> str | None:
    """Locate the Bun binary (prefer local, fall back to global)."""
    local_bun = os.path.join(_TUI_DIR, "node_modules", ".bin", "bun")
    if os.path.isfile(local_bun):
        return local_bun
    return shutil.which("bun")


def _start_server(host: str, port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """Start the uvicorn backend server as a subprocess."""
    _python: str = sys.executable or "python3"
    cmd: list[str] = [
        _python,
        "-m",
        "uvicorn",
        "core.api:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    print(f"  starting backend:  uvicorn core.api:app --host {host} --port {port}", file=sys.stderr)
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _start_tui(
    bun: str, api_url: str, api_key: str, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    """Start the OpenTUI React frontend as a subprocess."""
    cmd = [bun, "run", "src/index.tsx"]
    tui_env = {
        **env,
        "OSSIA_API_URL": api_url,
        "OSSIA_API_KEY": api_key,
    }
    print(f"  starting TUI:       {bun} run src/index.tsx  →  {api_url}", file=sys.stderr)
    return subprocess.Popen(
        cmd,
        cwd=_TUI_DIR,
        env=tui_env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _run_combined(args: argparse.Namespace) -> int:
    """Default ``ossia`` invocation: backend + TUI in one process tree."""
    api_key = require_api_key()
    base_url = f"http://{args.host}:{args.port}"
    # Build the server env. Explicitly set POSTGRES_URL to empty string to
    # override the .env file value (Settings() reads .env directly, but env
    # vars take precedence). The Docker deployment sets POSTGRES_URL; the
    # unified CLI is a dev tool that doesn't need it. Users who want
    # Postgres can start the server directly or export POSTGRES_URL.
    env = {**os.environ, "POSTGRES_URL": ""}

    processes: list[subprocess.Popen[bytes]] = []

    try:
        if not args.tui_only:
            proc = _start_server(args.host, args.port, env)
            processes.append(proc)
            wait_for_health(base_url, args.startup_timeout)
            print(f"  backend healthy at   {base_url}", file=sys.stderr)

        if not args.server_only:
            bun = _find_bun()
            if bun is None:
                print(
                    "ERROR: Bun is not installed. The TUI requires Bun.\n"
                    "  Install: curl -fsSL https://bun.sh/install | bash\n"
                    "  Or start with --server-only to run without the TUI.",
                    file=sys.stderr,
                )
                return 1
            if not os.path.isdir(_TUI_DIR):
                print(
                    f"ERROR: TUI directory not found at {_TUI_DIR}. "
                    "Make sure you're running from the project root.",
                    file=sys.stderr,
                )
                return 1
            proc = _start_tui(bun, base_url, api_key, env)
            processes.append(proc)

        print(
            f"\n  {'Both running.' if len(processes) == 2 else 'Running.'} Press Ctrl+C to stop.\n",
            file=sys.stderr,
        )

        while True:
            for i, p in enumerate(processes):
                rc = p.poll()
                if rc is not None:
                    names = ["backend", "TUI"]
                    print(
                        f"\n  {names[i]} exited with code {rc}. Shutting down.\n", file=sys.stderr
                    )
                    return rc
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n  Shutting down...", file=sys.stderr)
        return 0
    finally:
        for p in reversed(processes):
            terminate(p, grace_s=5.0)
    return 0  # unreachable; for type-checkers


def _run_server(args: argparse.Namespace) -> int:
    """``ossia server`` — backend only, blocks until killed."""
    require_api_key()
    base_url = f"http://{args.host}:{args.port}"
    env = {**os.environ, "POSTGRES_URL": ""}
    proc = _start_server(args.host, args.port, env)
    wait_for_health(base_url, args.startup_timeout)
    print(f"  backend healthy at   {base_url}", file=sys.stderr)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        terminate(proc, grace_s=5.0)
        return 0


def _run_tui(args: argparse.Namespace) -> int:
    """``ossia tui`` — TUI only, requires backend already running."""
    bun = _find_bun()
    if bun is None:
        print(
            "ERROR: Bun is not installed. The TUI requires Bun.\n"
            "  Install: curl -fsSL https://bun.sh/install | bash",
            file=sys.stderr,
        )
        return 1
    if not os.path.isdir(_TUI_DIR):
        print(
            f"ERROR: TUI directory not found at {_TUI_DIR}.",
            file=sys.stderr,
        )
        return 1
    api_key = require_api_key()
    base_url = f"http://{args.host}:{args.port}"
    proc = _start_tui(bun, base_url, api_key, os.environ.copy())
    try:
        return proc.wait()
    except KeyboardInterrupt:
        terminate(proc, grace_s=5.0)
        return 0


def _run_doctor(args: argparse.Namespace) -> int:
    """``ossia doctor`` — environment + plugin health check.

    Returns 0 on healthy, 1 if any required check fails, 2 if only
    optional / warning checks fail. Does not start a server.

    Required: ``OSSIA_API_KEY`` and at least one provider API key
    matching the configured ``PROVIDER``. Optional: other provider
    keys, ossia.json, plugin count.
    """
    from core.config import get_settings

    issues: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    if os.environ.get("OSSIA_API_KEY"):
        ok.append("OK   OSSIA_API_KEY set")
    else:
        issues.append("FAIL OSSIA_API_KEY not set")

    cfg = get_settings()
    ok.append(f"OK   provider={cfg.provider} model={cfg.model}")

    # The provider-specific key for the active provider. Other
    # provider keys are optional (warnings, not failures).
    active_key_env = {
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "fireworks": "FIREWORKS_API_KEY",
        "baseten": "BASETEN_API_KEY",
    }.get(cfg.provider)
    if active_key_env:
        if os.environ.get(active_key_env):
            ok.append(f"OK   {active_key_env} (active provider)")
        else:
            issues.append(f"FAIL {active_key_env} (active provider '{cfg.provider}')")

    # Plugins
    try:
        plugins = discover_plugins()
        if plugins:
            ok.append(f"OK   plugins: {len(plugins)} loaded")
        else:
            warnings.append("WARN plugins: none loaded")
    except Exception as exc:  # noqa: BLE001
        issues.append(f"FAIL plugins: {exc}")

    # ossia.json
    try:
        ossia_cfg = load_ossia_config()
        if ossia_cfg.source is not None:
            ok.append(f"OK   ossia.json: {ossia_cfg.source}")
        else:
            warnings.append("WARN ossia.json: none found (filesystem scan)")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"WARN ossia.json: {exc}")

    # Optional: live server
    if args.check_server:
        base_url = f"http://{args.host}:{args.port}"
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                ok.append(f"OK   server {base_url}/health")
            else:
                warnings.append(f"WARN server {base_url}/health: HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            warnings.append(f"WARN server {base_url}: {exc}")

    print("\n  Ossia doctor\n  " + "=" * 40)
    for line in ok:
        print(f"  {line}")
    for line in warnings:
        print(f"  {line}")
    for line in issues:
        print(f"  {line}")
    print()
    if issues:
        return 1
    if warnings:
        return 2
    return 0


def _run_plugins_list(args: argparse.Namespace) -> int:
    """``ossia plugins list`` — print loaded plugins as JSON or table."""
    plugins = discover_plugins()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": p.name,
                        "module": p.module,
                        "path": str(p.path),
                        "config": p.config,
                        "tools": [t.name for t in p.tools],
                        "subagents": [s["name"] for s in p.subagents],
                        "middlewares": [type(m).__name__ for m in p.middlewares],
                    }
                    for p in plugins
                ],
                indent=2,
            )
        )
        return 0
    if not plugins:
        print("  no plugins loaded")
        return 0
    name_w = max(len(p.name) for p in plugins)
    print(f"  {'NAME'.ljust(name_w)}  TOOLS")
    print(f"  {'-' * name_w}  {'-' * 4}")
    for p in plugins:
        tools = ", ".join(t.name for t in p.tools) or "—"
        print(f"  {p.name.ljust(name_w)}  {tools}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Build the arg parser with the combined-mode default + subcommands."""
    parser = argparse.ArgumentParser(
        prog="ossia",
        description="Ossia — backend + TUI launcher, diagnostics, and plugins.",
    )
    sub = parser.add_subparsers(dest="cmd")

    # `ossia` (no subcommand): same as before — server + TUI.
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the backend server to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port (default: 8000)"
    )
    parser.add_argument(
        "--startup-timeout", type=float, default=30.0, help="Backend startup timeout"
    )
    parser.add_argument("--server-only", action="store_true", help="Start only the backend")
    parser.add_argument("--tui-only", action="store_true", help="Start only the TUI")

    # `ossia server` — backend only.
    p_server = sub.add_parser("server", help="Start only the backend server")
    p_server.add_argument("--host", default="127.0.0.1")
    p_server.add_argument("--port", type=int, default=8000)
    p_server.add_argument("--startup-timeout", type=float, default=30.0)

    # `ossia tui` — TUI only.
    p_tui = sub.add_parser("tui", help="Start only the TUI")
    p_tui.add_argument("--host", default="127.0.0.1")
    p_tui.add_argument("--port", type=int, default=8000)

    # `ossia doctor` — env check.
    p_doc = sub.add_parser("doctor", help="Environment + plugin health check")
    p_doc.add_argument("--host", default="127.0.0.1")
    p_doc.add_argument("--port", type=int, default=8000)
    p_doc.add_argument(
        "--check-server",
        action="store_true",
        help="Also hit GET /health on the running server",
    )

    # `ossia plugins ...`
    p_plug = sub.add_parser("plugins", help="Plugin diagnostics")
    p_plug_sub = p_plug.add_subparsers(dest="plugin_cmd", required=True)
    p_plug_list = p_plug_sub.add_parser("list", help="List loaded plugins")
    p_plug_list.add_argument(
        "--json", action="store_true", help="Emit JSON instead of a table"
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.cmd == "server":
        return _run_server(args)
    if args.cmd == "tui":
        return _run_tui(args)
    if args.cmd == "doctor":
        return _run_doctor(args)
    if args.cmd == "plugins" and args.plugin_cmd == "list":
        return _run_plugins_list(args)
    # Default: combined backend + TUI.
    return _run_combined(args)


if __name__ == "__main__":
    sys.exit(main())
