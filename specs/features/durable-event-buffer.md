# Feature: Durable Thread Event Buffer (Redis Streams)

- Status: draft
- ADR: docs/adr/0015-durable-horizontal-scaling.md
- Scope: infrastructure | route

## What it does

Backs the thread event buffer (`GET /v1/threads/{id}/events` replay) with **Redis
Streams** so replay survives restarts and works across replicas, instead of the
current process-local `ThreadEventBuffer` dict (`src/core/events/buffer.py`,
accepted as an in-memory v1 limitation in ADR-0012). A `RedisThreadEventStore`
implements the `ThreadEventStore` protocol from M1 and is selected automatically
when `REDIS_URL` is set; otherwise the in-memory buffer remains the default so the
zero-config dev server is unchanged.

Redis Streams fit the access pattern exactly: append with `XADD` (using
`MAXLEN ~ 10000` to preserve today's per-thread cap), read the full ordered log
with `XRANGE` for replay, and `DEL` the key on clear. The stream key is namespaced
per scoped thread id (`ossia:events:{thread_id}`) with a TTL so idle threads expire
rather than growing Redis unbounded.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Append | `XADD ossia:events:{thread_id} MAXLEN ~ 10000` after each stream completes | Streaming events into Redis mid-stream (stays append-after-stream) |
| Retrieval | `XRANGE` full-log read for `GET /v1/threads/{id}/events`, preserving order | Server-side filtering by type/source/time (out of scope, as today) |
| Capacity | Approximate cap of 10,000 events per thread via `MAXLEN ~` | Configurable cap (can follow later) |
| Expiry | Per-thread TTL so idle event streams are reclaimed | Manual eviction callbacks |
| Backend selection | Redis when `REDIS_URL` set; in-memory (M1) otherwise | Postgres backend (documented as an alternative, not implemented here) |
| Webhook dispatch | Preserve the existing fire-and-forget `deliver_event` behavior on append | Durable webhook queue (that is M5) |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| GET | `/v1/threads/{thread_id}/events` | Behavior only — reads from Redis Streams when `REDIS_URL` set; `ThreadEventsResponse` shape unchanged |
| DELETE | `/v1/threads/{thread_id}/events` | Behavior only — deletes the Redis stream key; response shape unchanged |

No request/response schema changes, so `specs/openapi.checked.json` is unchanged and
`test_openapi_drift.py` stays green.

## Safety/Permissions

- **State isolation model:** The Redis key embeds the caller-scoped thread id from
  `_thread_id_for(caller, thread_id)`. Two callers cannot read or clear each other's
  streams — the caller hash is the isolation boundary, identical to today.
- **No new interrupt / PTC impact:** The buffer is still populated after the SSE
  stream has been fully consumed; it adds no tool calls and no HITL interrupts.
- **Degradation:** With `REDIS_URL` unset, the in-memory buffer is used. If Redis is
  configured but unreachable, replay degrades to empty for that thread (a miss,
  matching the graceful-degradation contract in `redis_client.py`) rather than
  failing the request; a warning is logged.

## NFRs

- **Streaming:** Unaffected. Events are serialized and `XADD`-ed after the SSE
  generator exits, exactly like the current append-after-stream design — the hot
  path never touches Redis.
- **Checkpointing:** Independent of the LangGraph checkpointer.
- **HITL:** Unaffected.
- **Performance:** One pipelined `XADD` batch per completed stream; one `XRANGE`
  per replay request. Redis round-trips are off the streaming path. `MAXLEN ~`
  (approximate trimming) avoids the cost of exact trimming on every append.

## Affected modules

- `src/core/events/buffer.py` — Add `RedisThreadEventStore` implementing the
  `ThreadEventStore` protocol; keep `ThreadEventBuffer` as the in-memory default;
  extend `get_thread_event_store()` to return the Redis backend when `REDIS_URL`
  is set. Port the `_dispatch_webhooks` fire-and-forget behavior into the Redis
  backend's `store()`.
- `src/core/events/__init__.py` — Export `RedisThreadEventStore`.
- `src/core/config.py` — No new required setting (reuses `redis_url`); optional
  `EVENT_BUFFER_TTL` field for per-thread stream expiry (default e.g. 24h).
- `src/core/api.py` — No call-site changes beyond M1 (already routed through
  `get_thread_event_store()`).
- `specs/changelog.md` — Non-breaking "Unreleased" note: event replay is now
  durable and cross-replica when `REDIS_URL` is set.

## Testing notes

- Unit tests in `tests/test_events.py` — parametrize the shared event-store suite
  over the in-memory and Redis backends. Redis tests gated on a `REDIS_URL` test
  fixture (skipped when unavailable, matching `tests/test_redis_backends.py`);
  prefer `fakeredis` for CI where already available.
- Integration test — store events, discard the store object (simulate restart),
  rebuild against the same Redis, and assert `get()` replays the same events.
- Cross-replica test — write via one store instance, read via a second instance
  pointed at the same Redis, assert equality (proves replicas share replay).
- API tests in `tests/test_api.py` — existing replay tests pass against both
  backends.
- Run: `.venv/bin/python -m pytest tests/test_events.py tests/test_api.py -v`.
- Known limitation: `MAXLEN ~` trims approximately, so a thread may briefly hold
  slightly more than 10,000 events before Redis reclaims — acceptable for replay.
