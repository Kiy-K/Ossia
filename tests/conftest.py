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

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
