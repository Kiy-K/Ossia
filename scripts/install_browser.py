"""Install a local Chromium for the web-reviewer subagent.

One-time setup. Detects an existing Playwright Chromium first
(re-using it if present — common when other tools on the box
already used ``playwright install``). Otherwise downloads ~200MB via
``uvx browser-use install``.

Idempotent — re-running is a no-op if a working binary is found.

After this completes, set in .env:
    BROWSER_USE_LOCAL=true
and restart the server. The web-reviewer subagent will then use the
local Chromium instead of the cloud browser (no BROWSER_USE_API_KEY
needed, no free-tier session cap).

Run with the venv:
    .venv/bin/python scripts/install_browser.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Make src/ importable so we can reuse the lookup logic
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.browser_use_tool import _find_local_chromium  # noqa: E402


def main() -> int:
    existing = _find_local_chromium()
    if existing:
        print(f"Found existing Chromium: {existing}")
        print("Nothing to install. Set BROWSER_USE_LOCAL=true in .env and restart.")
        return 0

    if not shutil.which("uvx"):
        print(
            "uvx is not on PATH. Install uv (https://docs.astral.sh/uv/) "
            "and re-run, or download a Chromium binary manually and set "
            "BROWSER_USE_USER_DATA_DIR (or add the binary's parent to "
            "PATH so browser-use can find it).",
            file=sys.stderr,
        )
        return 2
    print("Running: uvx browser-use install")
    print("This downloads Chromium (~200MB) and may take a minute...")
    result = subprocess.run(
        ["uvx", "browser-use", "install"],
        check=False,
    )
    if result.returncode != 0:
        return result.returncode
    # Confirm we can now find one
    found = _find_local_chromium()
    if found:
        print(f"Installed: {found}")
        return 0
    print(
        "uvx browser-use install exited 0 but no Chromium was found. "
        "Check $PLAYWRIGHT_BROWSERS_PATH and ~/.cache/ms-playwright/.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
