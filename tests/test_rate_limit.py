"""Tests for the rate limiter's per-API-key bucketing."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.api import _rate_limit_key


def _req(api_key: str | None = None, ip: str = "1.2.3.4") -> MagicMock:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    req = MagicMock()
    req.headers.get = lambda name, default=None: headers.get(name, default)
    req.client.host = ip
    return req


def test_key_uses_api_key_when_present() -> None:
    k1 = _rate_limit_key(_req(api_key="secret-1"))
    k2 = _rate_limit_key(_req(api_key="secret-2"))
    assert k1.startswith("key:")
    assert k2.startswith("key:")
    assert k1 != k2  # different keys → different buckets


def test_key_hashes_the_api_key_not_plaintext() -> None:
    """The bucket key is a digest, never the raw secret."""
    raw = "supersecret"
    k = _rate_limit_key(_req(api_key=raw))
    assert raw not in k
    assert len(k.split(":")[1]) == 16  # truncated sha256 hex


def test_key_falls_back_to_ip_when_no_api_key() -> None:
    k = _rate_limit_key(_req(api_key=None, ip="10.0.0.1"))
    assert k == "ip:10.0.0.1"


def test_same_key_same_bucket_regardless_of_ip() -> None:
    k1 = _rate_limit_key(_req(api_key="x", ip="1.1.1.1"))
    k2 = _rate_limit_key(_req(api_key="x", ip="2.2.2.2"))
    assert k1 == k2


def test_missing_client_falls_back_gracefully() -> None:
    req = MagicMock()
    req.headers.get = lambda name, default=None: default
    req.client = None
    k = _rate_limit_key(req)
    assert k == "ip:unknown"
