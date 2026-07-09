"""Regenerate the pinned OpenAPI spec at specs/openapi.checked.json.

Run this whenever you intentionally change the API surface. The drift
test (``tests/test_openapi_drift.py``) will fail until you do.

Usage:
    .venv/bin/python scripts/update_openapi_spec.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

# The FastAPI lifespan only needs to be importable; we never start the
# server here, just inspect the app's OpenAPI schema. No env overrides
# are required for this.

from core.api import app  # noqa: E402

SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "openapi.checked.json"


def main() -> int:
    # The AG-UI endpoint is added at lifespan-time, not at import-time, so
    # ``app.openapi()`` only sees it once a TestClient has run the lifespan.
    # Capture inside a TestClient to match what the running server exposes.
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        new_spec = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if SPEC_PATH.exists():
        old_spec = SPEC_PATH.read_text(encoding="utf-8")
        if old_spec == new_spec:
            print(f"openapi spec at {SPEC_PATH} is already up to date")
            return 0
    SPEC_PATH.write_text(new_spec, encoding="utf-8")
    print(f"wrote {SPEC_PATH} ({len(new_spec.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
