"""Unified ``ossia`` CLI — starts backend + TUI from a single command.

Usage:

    ossia                          Start backend + TUI (auto-detect port)
    ossia --port 9000              Custom port
    ossia --host 0.0.0.0           Bind to all interfaces
    ossia --server-only             Start only the backend server
    ossia --tui-only                Start only the TUI (backend must already be running)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

from dotenv import find_dotenv, load_dotenv

from core.cli_helper import require_api_key, terminate, wait_for_health

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


def _start_server(
    host: str, port: int, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    """Start the uvicorn backend server as a subprocess."""
    _python: str = sys.executable or "python3"
    cmd: list[str] = [
        _python, "-m", "uvicorn",
        "core.api:app",
        "--host", host,
        "--port", str(port),
        "--log-level", "warning",
    ]
    print(f"  starting backend:  uvicorn core.api:app --host {host} --port {port}",
          file=sys.stderr)
    return subprocess.Popen(
        cmd, env=env, stdout=sys.stdout, stderr=sys.stderr,
    )


def _start_tui(bun: str, api_url: str, api_key: str, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """Start the OpenTUI React frontend as a subprocess."""
    cmd = [bun, "run", "src/index.tsx"]
    tui_env = {
        **env,
        "OSSIA_API_URL": api_url,
        "OSSIA_API_KEY": api_key,
    }
    print(f"  starting TUI:       {bun} run src/index.tsx  →  {api_url}",
          file=sys.stderr)
    return subprocess.Popen(
        cmd, cwd=_TUI_DIR, env=tui_env, stdout=sys.stdout, stderr=sys.stderr,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ossia — unified backend + TUI launcher",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host to bind the backend server to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for the backend server (default: 8000)")
    parser.add_argument("--startup-timeout", type=float, default=30.0,
                        help="Seconds to wait for the backend to become healthy")
    parser.add_argument("--server-only", action="store_true",
                        help="Start only the backend server, without the TUI")
    parser.add_argument("--tui-only", action="store_true",
                        help="Start only the TUI (backend must already be running)")
    args = parser.parse_args()

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

        # Wait for either process to finish (TUI exits, or Ctrl+C kills us)
        print(f"\n  {'Both running.' if len(processes) == 2 else 'Running.'}"
              f" Press Ctrl+C to stop.\n",
              file=sys.stderr)

        while True:
            for i, p in enumerate(processes):
                rc = p.poll()
                if rc is not None:
                    names = ["backend", "TUI"]
                    print(f"\n  {names[i]} exited with code {rc}. Shutting down.\n",
                          file=sys.stderr)
                    return rc
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n  Shutting down...", file=sys.stderr)
        return 0
    finally:
        for p in reversed(processes):
            terminate(p, grace_s=5.0)


if __name__ == "__main__":
    sys.exit(main())
