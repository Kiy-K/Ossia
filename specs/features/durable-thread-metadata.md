# Feature: Durable Thread Metadata (Postgres)

- Status: draft
- ADR: docs/adr/0015-durable-horizontal-scaling.md
- Scope: infrastructure | route

## What it does

Persists per-thread metadata — user-set title and archive/regular status — in a
Postgres table so it survives process restarts and is visible across all replicas.
Today this state lives in the `_thread_meta` in-memory dict in `src/core/api.py`
(the code comment says "swap for a Postgres table when this needs to survive
restarts"). This feature implements a `PostgresThreadMetaStore` behind the
`ThreadMetaStore` protocol from M1, selected automatically when `POSTGRES_URL` is
set; when it is unset, the in-memory store from M1 remains the default so the
zero-config dev server is unchanged.

The endpoints that read/write thread metadata (`GET/PATCH /v1/threads/{id}`,
`POST /v1/threads`, `POST /v1/threads/{id}/unarchive`, and the archive filtering in
`GET /v1/threads`) keep their exact request/response shapes — only the storage
backend changes.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Persistence | `thread_meta` Postgres table keyed by scoped `thread_id`, columns for `title`, `status`, timestamps | Storing message content (that is the checkpointer's job) |
| Backend selection | Postgres backend when `POSTGRES_URL` set; in-memory (M1) otherwise | A file-based or SQLite backend |
| Schema management | Idempotent `CREATE TABLE IF NOT EXISTS` at startup (matches how the checkpointer sets up its tables) | A full migration framework (Alembic) |
| Concurrency | Upsert semantics (`INSERT ... ON CONFLICT DO UPDATE`) safe under concurrent replicas | Distributed locking (not needed for last-write-wins on a single thread row) |
| Isolation | Rows keyed by the already-`caller`-scoped thread id | Cross-caller access |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| GET | `/v1/threads` | Behavior only — archive filtering now reads status from Postgres; response shape unchanged |
| POST | `/v1/threads` | Behavior only — initial title persisted to Postgres; response shape unchanged |
| GET | `/v1/threads/{thread_id}` | Behavior only — title/status read from Postgres; `ThreadInfo` shape unchanged |
| PATCH | `/v1/threads/{thread_id}` | Behavior only — rename/archive persisted to Postgres; response shape unchanged |
| POST | `/v1/threads/{thread_id}/unarchive` | Behavior only — status change persisted to Postgres |

No request/response schema changes, so `specs/openapi.checked.json` is unchanged and
`test_openapi_drift.py` stays green.

## Safety/Permissions

- **State isolation model:** The primary key is the caller-scoped thread id
  produced by `_thread_id_for(caller, thread_id)`. A caller can only ever read or
  write rows whose key embeds their own caller hash — the same isolation boundary
  as every other thread-scoped endpoint.
- **Injection safety:** All access uses parameterized queries; no string
  interpolation of thread ids or titles into SQL.
- **Degradation:** If `POSTGRES_URL` is unset, the in-memory store is used
  (dev/single-process). If Postgres is configured but unreachable at request time,
  the failure surfaces as a 5xx rather than silently losing data — consistent with
  the checkpointer's behavior.

## NFRs

- **Streaming:** Unaffected — metadata is not on the streaming path.
- **Checkpointing:** Independent table; does not touch the LangGraph checkpointer
  schema, but reuses the same `POSTGRES_URL` connection configuration.
- **HITL:** Unaffected.
- **Performance:** Each metadata endpoint does one indexed single-row read or one
  upsert (sub-millisecond on a warm connection). `GET /v1/threads` batches status
  lookups (single query filtered by caller prefix) to avoid N+1.

## Affected modules

- `src/core/thread_meta.py` — Add `PostgresThreadMetaStore` implementing the
  `ThreadMetaStore` protocol; extend `get_thread_meta_store()` to return it when
  `POSTGRES_URL` is set. Includes idempotent table creation.
- `src/core/config.py` — No new required setting (reuses `postgres_url`); document
  the durability behavior in the field help text.
- `src/core/api.py` — No call-site changes beyond M1 (already routed through
  `get_thread_meta_store()`); confirm archive filtering in `GET /v1/threads` uses
  the batched status query.
- `specs/changelog.md` — Non-breaking "Unreleased" note: thread metadata is now
  durable when `POSTGRES_URL` is set.

## Testing notes

- Unit tests in `tests/test_thread_meta.py` — parametrize the shared protocol test
  suite over both the in-memory and Postgres backends so they prove identical
  behavior. Postgres tests gated on a `POSTGRES_URL` test fixture (skipped when
  unavailable, matching the existing Postgres-dependent tests).
- Integration test — set a title / archive a thread, drop and rebuild the store
  object (simulating a restart), and assert the title/status survive.
- API tests in `tests/test_api.py` — existing thread-metadata tests must pass
  against both backends.
- Run: `.venv/bin/python -m pytest tests/test_thread_meta.py tests/test_api.py -v`.
- Known limitation: last-write-wins on concurrent PATCH to the same thread; no
  optimistic-concurrency token (acceptable for title/archive).
