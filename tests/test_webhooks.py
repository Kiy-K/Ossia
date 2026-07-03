"""Tests for the in-memory webhook store + delivery (HMAC + retry)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from core.events.types import OssiaEvent
from core.webhooks import (
    EVENTS_ALL,
    MAX_DELIVERY_ATTEMPTS,
    SIGNATURE_HEADER,
    WebhookConfig,
    WebhookStore,
    _deliver_one,
    _sign,
    deliver_event,
)


def _evt(type_: str = "message_delta", thread_id: str = "t1") -> OssiaEvent:
    return OssiaEvent(type=type_, thread_id=thread_id, seq=1, data={"text": "hi"})


def test_sign_is_hmac_sha256_hex() -> None:
    sig = _sign("topsecret", b'{"x":1}')
    assert sig.startswith("sha256=")
    expected = hmac.new(b"topsecret", b'{"x":1}', hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


# ── WebhookConfig.matches ────────────────────────────────────────────────────


def test_matches_wildcard() -> None:
    w = WebhookConfig(id="x", url="u", events=[EVENTS_ALL])
    assert w.matches("anything")
    assert w.matches("tool_call")


def test_matches_specific_event() -> None:
    w = WebhookConfig(id="x", url="u", events=["tool_call", "message_delta"])
    assert w.matches("tool_call")
    assert w.matches("message_delta")
    assert not w.matches("other")


def test_to_public_redacts_secret() -> None:
    w = WebhookConfig(id="x", url="u", events=["*"], secret="s3cr3t")
    pub = w.to_public()
    assert "secret" not in pub
    assert pub["id"] == "x"
    assert pub["url"] == "u"


# ── WebhookStore: add / list / get / delete ──────────────────────────────────


@pytest.mark.asyncio
async def test_store_add_returns_id_and_stores() -> None:
    store = WebhookStore()
    cfg = await store.add(url="https://example.com/hook", events=["*"])
    assert cfg.id
    assert cfg.url == "https://example.com/hook"
    assert cfg.events == ["*"]
    # Secret auto-generated when not provided
    assert len(cfg.secret) >= 16
    # And it's now in the store
    assert (await store.get(cfg.id)) is cfg


@pytest.mark.asyncio
async def test_store_list_returns_all() -> None:
    store = WebhookStore()
    await store.add(url="https://a")
    await store.add(url="https://b")
    items = await store.list()
    urls = {w.url for w in items}
    assert urls == {"https://a", "https://b"}


@pytest.mark.asyncio
async def test_store_delete_removes() -> None:
    store = WebhookStore()
    cfg = await store.add(url="https://x")
    assert await store.delete(cfg.id) is True
    assert (await store.get(cfg.id)) is None
    # Second delete returns False (idempotent)
    assert await store.delete(cfg.id) is False


# ── Delivery: matches + posts + signs ───────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_event_posts_to_matching_webhooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = WebhookStore()
    match_cfg = await store.add(url="https://match", events=["message_delta"])
    await store.add(url="https://skip", events=["tool_call"])

    captured: list[tuple[str, bytes, dict[str, str]]] = []

    async def _post(url: str, **kwargs: Any) -> httpx.Response:
        captured.append((url, kwargs["content"], kwargs["headers"]))
        return httpx.Response(200, request=httpx.Request("POST", url))

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)

    delivered = await deliver_event(_evt("message_delta"), store=store, client=client)
    # The delivered list contains the IDs of webhooks that received the event.
    assert delivered == [match_cfg.id]
    urls = [c[0] for c in captured]
    assert "https://match" in urls
    assert "https://skip" not in urls  # not subscribed
    # Body is JSON
    body = next(c[1] for c in captured if c[0] == "https://match")
    parsed = json.loads(body)
    assert parsed["type"] == "message_delta"
    # Signature header is set + valid
    headers = next(c[2] for c in captured if c[0] == "https://match")
    assert SIGNATURE_HEADER in headers
    assert headers[SIGNATURE_HEADER] == _sign(match_cfg.secret, body)


@pytest.mark.asyncio
async def test_deliver_event_no_matching_webhooks_returns_empty() -> None:
    store = WebhookStore()
    await store.add(url="https://x", events=["other_kind"])
    client = MagicMock()
    client.post = AsyncMock()
    delivered = await deliver_event(_evt("message_delta"), store=store, client=client)
    assert delivered == []
    client.post.assert_not_called()


# ── Delivery: retry on failure ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deliver_one_retries_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 500 response triggers retry; 200 on the 3rd attempt succeeds."""
    calls = {"n": 0}

    async def _post(*_a: Any, **_k: Any) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, request=httpx.Request("POST", "u"))
        return httpx.Response(200, request=httpx.Request("POST", "u"))

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    cfg = WebhookConfig(id="w", url="u", events=["*"], secret="s")
    # Patch the asyncio.sleep name that _deliver_one actually uses.
    sleeps: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("core.webhooks.asyncio.sleep", _fake_sleep)
    result = await _deliver_one(cfg, b"{}", client)
    assert result == "w"
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_deliver_one_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _post(*_a: Any, **_k: Any) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, request=httpx.Request("POST", "u"))

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    cfg = WebhookConfig(id="w", url="u", events=["*"], secret="s")

    async def _fake_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("core.webhooks.asyncio.sleep", _fake_sleep)
    result = await _deliver_one(cfg, b"{}", client)
    assert result == ""  # not delivered
    assert calls["n"] == MAX_DELIVERY_ATTEMPTS


@pytest.mark.asyncio
async def test_deliver_one_handles_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _post(*_a: Any, **_k: Any):
        raise httpx.ConnectError("boom")

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    cfg = WebhookConfig(id="w", url="u", events=["*"], secret="s")

    async def _fake_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("core.webhooks.asyncio.sleep", _fake_sleep)
    result = await _deliver_one(cfg, b"{}", client)
    assert result == ""


# ── Event buffer dispatches webhooks when called from a loop ────────────────


@pytest.mark.asyncio
async def test_buffer_store_dispatches_webhooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.events.buffer import ThreadEventBuffer

    delivered: list[OssiaEvent] = []

    async def _capture(event: OssiaEvent, **_k: Any) -> list[str]:
        delivered.append(event)
        return ["x"]

    monkeypatch.setattr("core.webhooks.deliver_event", _capture)
    buf = ThreadEventBuffer()
    buf.store("t1", [_evt("message_delta")])
    # Let the scheduled task run
    await asyncio.sleep(0)
    assert len(delivered) == 1
    assert delivered[0].type == "message_delta"


def test_buffer_store_sync_does_not_dispatch() -> None:
    """Called outside a running loop, the buffer still works (no webhook)."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    # This should not raise even though no event loop is running.
    buf.store("t1", [_evt("message_delta")])
    assert len(buf.get("t1")) == 1
