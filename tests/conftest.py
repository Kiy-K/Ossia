"""Pytest configuration: lock test-mode env for the API test module.

The unified API tests (``tests/test_api.py``) need ``OSSIA_API_KEY`` and
``ENABLE_HUMAN_REVIEW=false`` (no Postgres in CI). Applying these at
module-import time leaks into the rest of the suite, so we apply them
inside the ``client`` fixture (module-scoped) and restore them on
teardown.

The OpenAPI drift test also needs the same env to import the app; it
sets/restores its own copy.
"""

from __future__ import annotations

import warnings
from typing import Any

from dotenv import find_dotenv, load_dotenv

# ── Warning filters ─────────────────────────────────────────────────────────
# These deprecations come from third-party dependencies and cannot be fixed in
# our codebase. We suppress them via the pytest_configure hook, which runs before
# any test modules are imported.

_IGNORED_WARNINGS: list[dict[str, Any]] = [
    # slowapi uses asyncio.iscoroutinefunction() deprecated in Python 3.14+
    {"category": DeprecationWarning, "module": r"slowapi\.extension"},
    # StarletteDeprecationWarning: httpx with starlette.testclient
    {"message": r".*httpx.*starlette\.testclient.*"},
]


def pytest_configure(config: Any) -> None:
    """Configure warning filters before any test modules are imported."""
    for kwargs in _IGNORED_WARNINGS:
        warnings.filterwarnings("ignore", **kwargs)

    # LangChainBetaWarning: experimental APIs we use intentionally.
    try:
        from langchain_core._api import LangChainBetaWarning

        warnings.filterwarnings("ignore", category=LangChainBetaWarning)
    except ImportError:
        pass


load_dotenv(find_dotenv(usecwd=True))
