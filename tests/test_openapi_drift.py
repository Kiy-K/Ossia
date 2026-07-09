"""Drift test: ensure the current OpenAPI matches the pinned spec.

Boots the FastAPI app, generates the OpenAPI document, and diffs it
against ``specs/openapi.checked.json``. Any drift fails the test.

To intentionally change the spec, run
``python scripts/update_openapi_spec.py`` to regenerate the pinned file.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

import pytest
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from core.api import app  # noqa: E402
from core.config import get_settings  # noqa: E402

SPEC_PATH = Path(__file__).resolve().parent.parent / "specs" / "openapi.checked.json"

_SAVED_ENV: dict[str, str | None] = {}


def _setup_env() -> None:
    """Lock env for app import, then clear the settings cache."""
    for k in ("OSSIA_API_KEY", "ENABLE_HUMAN_REVIEW", "POSTGRES_URL"):
        _SAVED_ENV[k] = os.environ.get(k)
    os.environ["OSSIA_API_KEY"] = "drift-test-key"
    os.environ["ENABLE_HUMAN_REVIEW"] = "false"
    os.environ["POSTGRES_URL"] = ""
    get_settings.cache_clear()


def _restore_env() -> None:
    """Restore the original env so other test modules are unaffected."""
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _env_lock() -> None:
    """Apply and restore test env around every test in this module."""
    _setup_env()
    try:
        yield
    finally:
        _restore_env()


def test_openapi_matches_pinned_spec() -> None:
    """Generated OpenAPI must match the pinned spec verbatim.

    Captures the spec inside a ``TestClient`` context so the lifespan
    runs and the AG-UI endpoint is registered — that route is added
    at lifespan-time, not at import-time, and ``app.openapi()`` only
    includes it once. The previous version called ``app.openapi()``
    at module level, which made the result order-dependent: it only
    matched the pinned spec when the test ran before any other test
    had triggered the lifespan, so the AG-UI route was missing.
    """
    from fastapi.testclient import TestClient

    with TestClient(app) as _client:
        current = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    pinned = SPEC_PATH.read_text(encoding="utf-8")
    if current == pinned:
        return
    diff = "".join(
        difflib.unified_diff(
            pinned.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile="specs/openapi.checked.json (pinned)",
            tofile="generated OpenAPI (current)",
            n=3,
        )
    )
    pytest.fail(
        "OpenAPI drift detected. If this change is intentional, run\n"
        "  python scripts/update_openapi_spec.py\n"
        "to regenerate the pinned spec, then commit it.\n\n" + diff
    )
