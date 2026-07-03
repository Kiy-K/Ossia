"""Tests for the multi-tenant API key resolution and /v1/whoami."""

from __future__ import annotations

import importlib
import os
from collections.abc import Generator

import pytest


@pytest.fixture
def fresh_api_module() -> Generator[None, None, None]:
    """Reload core.api so module-level OSSIA_API_KEY reads pick up patches."""
    import core.api as api_mod

    importlib.reload(api_mod)
    yield
    importlib.reload(api_mod)


def test_expected_keys_from_comma_env(
    monkeypatch: pytest.MonkeyPatch, fresh_api_module: None
) -> None:
    from core.api import _expected_api_keys

    monkeypatch.setenv("OSSIA_API_KEYS", "k1,k2,k3")
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("OSSIA_API_KEY", raising=False)
    assert _expected_api_keys() == ["k1", "k2", "k3"]


def test_expected_keys_from_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch, fresh_api_module: None
) -> None:
    from core.api import _expected_api_keys

    f = tmp_path / "keys.txt"
    f.write_text("k1\n# comment\n\nk2\nk3\n")
    monkeypatch.setenv("OSSIA_API_KEYS_FILE", str(f))
    monkeypatch.delenv("OSSIA_API_KEYS", raising=False)
    monkeypatch.delenv("OSSIA_API_KEY", raising=False)
    assert _expected_api_keys() == ["k1", "k2", "k3"]


def test_expected_keys_single_back_compat(
    monkeypatch: pytest.MonkeyPatch, fresh_api_module: None
) -> None:
    from core.api import _expected_api_keys

    monkeypatch.delenv("OSSIA_API_KEYS", raising=False)
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    monkeypatch.setenv("OSSIA_API_KEY", "single")
    assert _expected_api_keys() == ["single"]


def test_expected_keys_comma_wins_over_single(
    monkeypatch: pytest.MonkeyPatch, fresh_api_module: None
) -> None:
    from core.api import _expected_api_keys

    monkeypatch.setenv("OSSIA_API_KEYS", "a,b")
    monkeypatch.setenv("OSSIA_API_KEY", "should-be-ignored")
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    assert _expected_api_keys() == ["a", "b"]


def test_expected_keys_empty_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch, fresh_api_module: None
) -> None:
    from core.api import _expected_api_keys

    monkeypatch.delenv("OSSIA_API_KEYS", raising=False)
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("OSSIA_API_KEY", raising=False)
    assert _expected_api_keys() == []


def test_whoami_returns_caller_and_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each accepted key gets a stable caller id; the fingerprint matches."""
    from fastapi.testclient import TestClient

    from core.config import get_settings

    monkeypatch.setenv("OSSIA_API_KEYS", "alpha,beta")
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("OSSIA_API_KEY", raising=False)
    get_settings.cache_clear()
    # Reload so module-level reads pick up the new env
    import core.api as api_mod
    importlib.reload(api_mod)
    try:
        c = TestClient(api_mod.app)
        r1 = c.get("/v1/whoami", headers={"X-API-Key": "alpha"})
        r2 = c.get("/v1/whoami", headers={"X-API-Key": "beta"})
        r3 = c.get("/v1/whoami", headers={"X-API-Key": "alpha"})
    finally:
        # Restore single-key setup for the rest of the suite
        monkeypatch.delenv("OSSIA_API_KEYS", raising=False)
        monkeypatch.setenv("OSSIA_API_KEY", os.environ.get("OSSIA_API_KEY", "dev"))
        get_settings.cache_clear()
        importlib.reload(api_mod)
    assert r1.status_code == 200
    assert r1.json()["key_fpr"] == "alpha"
    assert r2.json()["key_fpr"] == "beta"
    # Same key → same caller id (stability)
    assert r1.json()["caller"] == r3.json()["caller"]
    # Different keys → different caller ids
    assert r1.json()["caller"] != r2.json()["caller"]


def test_invalid_key_rejected_with_multi_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from core.config import get_settings

    monkeypatch.setenv("OSSIA_API_KEYS", "alpha,beta")
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("OSSIA_API_KEY", raising=False)
    get_settings.cache_clear()
    import core.api as api_mod
    importlib.reload(api_mod)
    try:
        c = TestClient(api_mod.app)
        r = c.get("/v1/whoami", headers={"X-API-Key": "not-a-key"})
    finally:
        monkeypatch.delenv("OSSIA_API_KEYS", raising=False)
        monkeypatch.setenv("OSSIA_API_KEY", os.environ.get("OSSIA_API_KEY", "dev"))
        get_settings.cache_clear()
        importlib.reload(api_mod)
    assert r.status_code == 401


def test_no_keys_configured_returns_500(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify_api_key raises 500 when nothing is configured."""
    from starlette.requests import Request

    monkeypatch.setenv("OSSIA_API_KEYS", "")
    monkeypatch.delenv("OSSIA_API_KEYS_FILE", raising=False)
    monkeypatch.setenv("OSSIA_API_KEY", "")
    import core.api as api_mod
    importlib.reload(api_mod)
    try:
        req = Request({"type": "http", "headers": [], "method": "GET"})
        import asyncio

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            asyncio.run(api_mod.verify_api_key(req))
        assert ei.value.status_code == 500
    finally:
        monkeypatch.setenv("OSSIA_API_KEY", os.environ.get("OSSIA_API_KEY", "dev"))
        importlib.reload(api_mod)
