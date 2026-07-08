# Feature: Durable Webhook Delivery (Redis Queue) — Stretch

- Status: draft
- ADR: docs/adr/0015-durable-horizontal-scaling.md
- Scope: infrastructure | route

## What it does

Makes webhook subscriptions and event delivery durable and cross-replica. Today
webhook subscriptions live in process memory and delivery is a fire-and-forget
`asyncio` task scheduled from the event buffer (`_dispatch_webhooks` →
`deliver_event`), so a subscription registered on replica A is invisible to replica
B, subscriptions vanish on restart, and a failed delivery is dropped with no retry.
This feature persists subscriptions (Postgres) and moves delivery onto a
**Redis-backed queue** with bounded retry and a dead-letter list, so events are
delivered at-least-once across replicas and survive restarts. It is the stretch
milestone of the durability epic and depends on M1–M3.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Subscriptions | Persist `/v1/webhooks` registrations in Postgres, readable by any replica | New subscription fields beyond today's shape |
| Delivery queue | Enqueue delivery jobs to Redis (list/stream); a worker drains and POSTs | An external broker (Kafka/RabbitMQ) |
| Retry | Bounded exponential backoff with a max attempt count | Per-subscriber custom retry policies |
| Dead-letter | Failed-after-max jobs moved to a DLQ key for inspection | A DLQ management API |
| Delivery semantics | At-least-once; receivers should dedupe on event id | Exactly-once delivery |
| Backend selection | Redis+Postgres when configured; in-memory fire-and-forget otherwise | File-based durability |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| GET | `/v1/webhooks` | Behavior only — subscriptions read from Postgres when configured; response shape unchanged |
| POST | `/v1/webhooks` | Behavior only — subscription persisted to Postgres; response shape unchanged |
| DELETE | `/v1/webhooks/{webhook_id}` | Behavior only — deletes the persisted subscription; response shape unchanged |

No request/response schema changes, so `specs/openapi.checked.json` is unchanged and
`test_openapi_drift.py` stays green.

## Safety/Permissions

- **State isolation model:** Subscriptions are stored with their owning caller
  scope; a caller can only list/delete their own webhooks, matching the existing
  endpoint behavior.
- **SSRF / egress:** Preserve any existing target-URL validation in
  `core/webhooks`; document that outbound delivery should be restricted to allowed
  destinations in production.
- **Secret handling:** Delivery payloads pass through the existing PII redaction on
  the event data; signing secrets (if any) are never logged.
- **Degradation:** With neither Redis nor Postgres configured, behavior falls back
  to today's in-memory fire-and-forget (single replica, best-effort).

## NFRs

- **Streaming:** Unaffected — enqueue happens on the same append-after-stream path
  as event buffering; the SSE hot path is untouched.
- **Checkpointing:** Independent of the LangGraph checkpointer.
- **HITL:** Unaffected.
- **Performance:** Enqueue is a single Redis push; delivery HTTP calls happen in a
  background worker, decoupled from request latency. Retries use backoff to avoid
  hammering a failing receiver.

## Affected modules

- `src/core/webhooks.py` — Add Postgres-backed subscription storage and a
  Redis-queue producer; refactor `deliver_event` to enqueue rather than POST
  directly when the queue backend is active.
- `src/core/webhook_worker.py` — New module: a queue-draining worker with retry +
  DLQ, runnable in-process (background task) or as a separate process for scale.
- `src/core/config.py` — Reuse `redis_url` / `postgres_url`; add optional
  `WEBHOOK_MAX_RETRIES` and `WEBHOOK_BACKOFF_BASE` fields.
- `src/core/events/buffer.py` — Route `_dispatch_webhooks` through the durable
  queue when configured (kept fire-and-forget otherwise).
- `specs/changelog.md` — Non-breaking "Unreleased" note: durable, retrying,
  cross-replica webhook delivery when Redis+Postgres are set.

## Testing notes

- Unit tests in `tests/test_webhooks.py` — enqueue/dequeue, retry backoff sequence,
  max-attempt → DLQ transition, and subscription persistence (in-memory + durable
  backends, gated on Redis/Postgres fixtures / `fakeredis`).
- Integration test — register a webhook on one store instance, deliver from a
  worker bound to a second instance (simulating another replica), and assert the
  receiver got the event; kill the receiver to force retries and assert DLQ landing.
- API tests in `tests/test_api.py` — existing webhook endpoint tests pass against
  both backends.
- Run: `.venv/bin/python -m pytest tests/test_webhooks.py tests/test_api.py -v`.
- Known limitation: at-least-once delivery means duplicate deliveries are possible;
  receivers must dedupe on event id.
