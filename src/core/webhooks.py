"""Webhook delivery — in-memory store + async HTTP delivery with HMAC + retry.

Ponytail: minimal-but-correct. The store is in-memory (process
lifetime), delivery is best-effort with 3 retries at exponential
backoff, and signatures use HMAC-SHA256 like Stripe / GitHub. When
someone needs cross-process delivery, swap ``WebhookStore`` for a
Postgres-backed implementation behind the same interface.

Wire model
----------

  - A webhook is ``(id, url, events, secret, created_at)``.
  - ``events`` is a list of event-kind strings; ``["*"]`` matches all.
  - Delivery body: JSON of the event dict (v3 channel-keyed format).
  - Signature header: ``X-Ossia-Signature: sha256=<hex>`` over the
    raw JSON body. The receiver verifies with the same secret.
  - Retry: 3 attempts, backoff 1s / 2s / 4s. After 3 failures the
    event is dropped and logged at WARNING.

Thread-safety: a single ``asyncio.Lock`` guards the store dict
and the id counter. Delivery itself is awaitable and
non-blocking; many webhooks fan out in parallel.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3
DELIVERY_TIMEOUT_S = 10.0
SIGNATURE_HEADER = "X-Ossia-Signature"
EVENTS_ALL = "*"


def _sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest of *body* with *secret*."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


@dataclass
class WebhookConfig:
    """One registered webhook."""

    id: str
    url: str
    events: list[str] = field(default_factory=lambda: [EVENTS_ALL])
    secret: str = ""
    created_at: float = field(default_factory=time.time)

    def matches(self, event_type: str) -> bool:
        """True if this webhook is subscribed to *event_type*."""
        return EVENTS_ALL in self.events or event_type in self.events

    def to_public(self) -> dict[str, Any]:
        """Return the public view (no secret) for the API response."""
        d = asdict(self)
        d.pop("secret", None)
        return d


class WebhookStore:
    """In-memory webhook store with async-safe mutations."""

    def __init__(self) -> None:
        self._items: dict[str, WebhookConfig] = {}
        self._lock = asyncio.Lock()

    async def add(
        self,
        url: str,
        events: list[str] | None = None,
        secret: str = "",
    ) -> WebhookConfig:
        """Create and store a new webhook. Returns the new config."""
        async with self._lock:
            wid = secrets.token_urlsafe(8)
            cfg = WebhookConfig(
                id=wid,
                url=url,
                events=list(events) if events else [EVENTS_ALL],
                secret=secret or secrets.token_urlsafe(24),
            )
            self._items[wid] = cfg
            return cfg

    async def list(self) -> list[WebhookConfig]:
        async with self._lock:
            return list(self._items.values())

    async def get(self, wid: str) -> WebhookConfig | None:
        async with self._lock:
            return self._items.get(wid)

    async def delete(self, wid: str) -> bool:
        async with self._lock:
            return self._items.pop(wid, None) is not None


_STORE = WebhookStore()


def get_webhook_store() -> WebhookStore:
    """Return the global webhook store singleton."""
    return _STORE


def _event_payload(event: dict[str, Any]) -> bytes:
    """Serialize a v2 StreamPart dict to JSON bytes for delivery."""
    return json.dumps(event, default=str).encode("utf-8")


async def deliver_event(
    event: dict[str, Any],
    *,
    store: WebhookStore | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Deliver an event to every matching webhook.

    Returns the list of webhook ids that successfully received the
    event (a successful ack is HTTP 2xx). Failures are logged and
    retried; ids that exhausted retries are NOT in the returned
    list.
    """
    store = store or _STORE
    event_type = event.get("type", "unknown") if isinstance(event, dict) else getattr(event, "type", None) or "unknown"
    webhooks = [w for w in await store.list() if w.matches(event_type)]
    if not webhooks:
        return []

    body = _event_payload(event)
    owns_client = client is None
    active_client: httpx.AsyncClient
    if owns_client:
        active_client = httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_S)
    else:
        assert client is not None
        active_client = client
    try:
        return list(
            await asyncio.gather(
                *[_deliver_one(w, body, active_client) for w in webhooks],
                return_exceptions=False,
            )
        )
    finally:
        if owns_client:
            await active_client.aclose()


async def _deliver_one(
    webhook: WebhookConfig,
    body: bytes,
    client: httpx.AsyncClient,
) -> str:
    """Deliver to one webhook with exponential-backoff retry."""
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: _sign(webhook.secret, body),
    }
    for attempt in range(1, MAX_DELIVERY_ATTEMPTS + 1):
        try:
            resp = await client.post(webhook.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning(
                "webhook %s attempt %d failed: %s",
                webhook.id,
                attempt,
                exc,
            )
        else:
            if 200 <= resp.status_code < 300:
                return webhook.id
            logger.warning(
                "webhook %s attempt %d got HTTP %d",
                webhook.id,
                attempt,
                resp.status_code,
            )
        if attempt < MAX_DELIVERY_ATTEMPTS:
            await asyncio.sleep(2 ** (attempt - 1))
    logger.warning(
        "webhook %s gave up after %d attempts (url=%s)",
        webhook.id,
        MAX_DELIVERY_ATTEMPTS,
        webhook.url,
    )
    return ""  # explicit: not delivered
