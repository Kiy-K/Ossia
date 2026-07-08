# ADR-0015: Durable, Horizontally-Scalable State Stores

**Status:** proposed
**Date:** 2026-07-08

## Context

Ossia is positioned as a practical-use "concierge developer copilot." Running it
in production means running **multiple stateless replicas behind a load balancer**
and surviving process restarts (deploys, crashes, autoscaling events) with **zero
state loss**. Today three pieces of runtime state live only in the process heap:

1. **Thread event buffer** (`src/core/events/buffer.py`) — a module-level
   `ThreadEventBuffer` singleton (`dict[str, list[dict]]`) that backs
   `GET /v1/threads/{id}/events` replay. ADR-0012 explicitly accepted the
   in-memory limitation for v1 and flagged Postgres/append-only log as future work.
2. **Thread metadata** (`_thread_meta` dict in `src/core/api.py`) — per-thread
   title and archive/regular status for the assistant-ui thread list. The code
   comment already says "swap for a Postgres table when this needs to survive
   restarts."
3. **Webhook subscriptions + delivery** — subscriptions and the fire-and-forget
   `deliver_event` dispatch triggered from the buffer are in-process only.

These are fine for a single-process dev server but break the moment there is a
restart or a second replica: replay returns empty, renames/archives vanish, and a
webhook registered on replica A is invisible to replica B.

The good news is the durable substrates are **already wired**: Redis has lazy
sync+async singletons that degrade gracefully when `REDIS_URL` is unset
(`src/core/redis_client.py`), and Postgres is already the LangGraph checkpointer
and long-term store (`postgres_url` in `Settings`). No new infrastructure is
required — only durable backends behind the existing seams.

## Decision

Introduce a **pluggable storage-backend seam** for each in-memory store, with the
current in-memory implementation kept as the zero-config default and durable
backends selected automatically based on which connection URLs are configured.

Guiding principles (consistent with the existing codebase):

- **Protocol-first.** Define a small `typing.Protocol` per store
  (`ThreadEventStore`, `ThreadMetaStore`) so `api.py` depends on an interface,
  not a concrete class. This is a pure refactor with no behavior change and
  de-risks every subsequent milestone.
- **Graceful degradation.** Backend selection mirrors the Redis pattern:
  `POSTGRES_URL`/`REDIS_URL` set → durable backend; unset → in-memory fallback.
  A single-file `uvicorn` run stays zero-config.
- **No HTTP-contract changes.** These are storage/durability changes. Routes,
  request/response schemas, and the pinned `openapi.checked.json` are unchanged,
  so no `/v1 → /v2` bump. The contract test (`test_openapi_drift.py`) must stay
  green.
- **Backend choice per store follows the access pattern.** Thread metadata is
  low-volume relational key/value → **Postgres table**. The event buffer is a
  high-volume append + range-read with a size cap → **Redis Streams**
  (`XADD MAXLEN` for bounded capacity, `XRANGE` for replay), with a Postgres
  fallback and the in-memory default. Webhook delivery becomes a Redis-backed
  queue with retry/DLQ.

Milestones (each shipped as its own feature spec + PR):

| # | Milestone | Backend | Feature spec |
|---|-----------|---------|--------------|
| M1 | Pluggable backend seam (refactor, no behavior change) | — | `durability-backend-seam.md` |
| M2 | Durable thread metadata | Postgres | `durable-thread-metadata.md` |
| M3 | Durable event buffer | Redis Streams | `durable-event-buffer.md` |
| M4 | Horizontal-scale validation (multi-replica) | Redis + Postgres | `horizontal-scaling.md` |
| M5 | Durable webhook delivery (stretch) | Redis queue | `durable-webhooks.md` |

## Consequences

- **Enables N stateless replicas.** Once all shared state lives in Redis/Postgres,
  replicas are interchangeable; the load balancer needs no sticky sessions.
- **Survives restarts.** Replay, thread titles/archive, and webhook subscriptions
  persist across deploys and crashes.
- **Zero-config dev preserved.** With neither URL set, behavior is identical to
  today (in-memory, single process).
- **New operational dependency in production.** Multi-replica deployments now
  require Redis and Postgres to be reachable; connection loss must degrade
  predictably (documented per milestone).
- **Serialization cost.** Events/metadata now cross a network boundary. The event
  buffer write stays off the hot streaming path (append-after-stream, as today),
  so streaming latency is unaffected.
- **Migration.** No data migration for the event buffer (ephemeral, rebuilt on
  next run). Thread metadata is created lazily per thread, so the Postgres table
  starts empty and populates on first rename/archive — no backfill required.
