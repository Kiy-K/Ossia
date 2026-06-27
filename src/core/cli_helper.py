"""Shared helpers for the audit/eval CLIs.

These clients are thin wrappers around the unified HTTP API: they boot a
uvicorn subprocess, wait for /health, hit the endpoint, and tear the
server down. Extracted so the two scripts stay in lockstep.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

import httpx

logger = logging.getLogger(__name__)


def require_api_key() -> str:
    """Return the OSSIA_API_KEY from the environment, failing fast if absent.

    The CLIs must not boot a real server with a hard-coded fallback key;
    the audit/eval endpoints are protected by ``X-API-Key`` and a missing
    key means a misconfigured environment.
    """
    api_key = os.environ.get("OSSIA_API_KEY")
    if not api_key:
        print(
            "ERROR: OSSIA_API_KEY is not set. Set it in the environment or .env "
            "before running this script.",
            file=sys.stderr,
        )
        sys.exit(2)
    return api_key


def wait_for_health(
    base_url: str,
    timeout_s: float,
    log_every_s: float = 5.0,
) -> None:
    """Block until /health returns 200 or raise on timeout.

    Logs a one-line "waiting for server..." every ``log_every_s`` seconds
    so a misconfigured boot (lifespan error) doesn't sit silent for the
    full timeout before reporting.
    """
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    last_log = time.monotonic()
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        now = time.monotonic()
        if now - last_log >= log_every_s:
            logger.info("waiting for server at %s ...", base_url)
            last_log = now
        time.sleep(0.2)
    raise RuntimeError(
        f"server at {base_url} did not become healthy within {timeout_s}s"
        + (f" (last error: {last_exc})" if last_exc else "")
    )


def run_server_subprocess(
    host: str, port: int, env: dict[str, str]
) -> subprocess.Popen:
    """Start a uvicorn subprocess for ossia.api:app and return the Popen handle.

    The caller is responsible for terminating the process (use
    :func:`terminate` in a ``finally``).
    """
    cmd = [
        sys.executable, "-m", "uvicorn",
        "core.api:app",
        "--host", host,
        "--port", str(port),
        "--log-level", "warning",
    ]
    print(f"  starting server: {' '.join(cmd)}", file=sys.stderr)
    return subprocess.Popen(cmd, env=env, stdout=sys.stdout, stderr=sys.stderr)


def terminate(proc: subprocess.Popen, grace_s: float = 10.0) -> None:
    """Send SIGINT to a subprocess and wait, escalating to SIGKILL on timeout."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
