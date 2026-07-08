# Feature: Pluggable Durability Backend Seam

- Status: draft
- ADR: docs/adr/0015-durable-horizontal-scaling.md
- Scope: infrastructure

## What it does

Introduces a storage-backend abstraction (a `typing.Protocol` per store) in front
of the two process-local runtime stores — the thread event buffer and the thread
metadata dict — so that `api.py` and the rest of the codebase depend on an
interface rather than a concrete in-memory class. This milestone is a **pure
refactor with no observable behavior change**: the existing in-memory
implementations become the default backends behind the new protocols. It exists to
de-risk the durable-backend milestones (M2/M3) that follow, by isolating the "where
does this state live" decision behind a single seam with its own tests.

Without this seam, swapping the event buffer or thread metadata to Redis/Postgres
would require editing every call site in `api.py`. With it, a durable backend is a
new class implementing the same protocol plus one selection function.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Event store interface | `ThreadEventStore` Protocol with `store/get/clear/clear_all/thread_ids` matching today's `ThreadEventBuffer` | Changing the event data shape or the replay endpoint behavior |
| Metadata store interface | `ThreadMetaStore` Protocol with `get/set/list`-style ops matching today's `_thread_meta` access | Adding new metadata fields or endpoints |
| Backend selection | A `get_thread_event_store()` / `get_thread_meta_store()` factory that returns the in-memory impl by default | Actually adding Redis/Postgres backends (M2/M3) |
| Behavior parity | Byte-for-byte identical behavior to the current singletons; existing tests pass unchanged | Any new persistence guarantee |
| Call-site migration | Replace direct `_thread_meta` / `get_thread_event_buffer()` use in `api.py` with the protocol accessors | New routes or schema changes |

## Endpoint impact

None — this feature does not modify the HTTP contract. Routes, request/response
schemas, and `specs/openapi.checked.json` are unchanged. `test_openapi_drift.py`
stays green.

## Safety/Permissions

- **State isolation model:** Unchanged. Thread scoping via `_thread_id_for(caller,
  thread_id)` is applied at the call site exactly as today; the protocol operates
  on already-scoped thread ids. Two callers cannot see each other's events or
  metadata.
- **No new security boundaries.** The seam is an internal refactor; it introduces
  no new external inputs, no new tool calls, and no new interrupt points.

## NFRs

- **Streaming:** Unaffected. The event store is still written after the SSE
  generator completes (append-after-stream); the protocol call is a direct
  in-memory method dispatch in this milestone.
- **Checkpointing:** Unaffected. Independent of the LangGraph checkpointer.
- **HITL:** Unaffected.
- **Performance:** Zero measurable overhead — the default backend is the existing
  in-memory object; the protocol adds one attribute lookup per call.

## Affected modules

- `src/core/events/buffer.py` — Extract a `ThreadEventStore` Protocol; keep
  `ThreadEventBuffer` as the default in-memory implementation of it; add a
  `get_thread_event_store()` selector (returns the singleton for now).
- `src/core/events/__init__.py` — Export `ThreadEventStore` and
  `get_thread_event_store` alongside the existing symbols.
- `src/core/thread_meta.py` — New module: `ThreadMetaStore` Protocol and an
  `InMemoryThreadMetaStore` that ports the current `_thread_meta` dict and
  `_get_thread_meta` helper verbatim; add a `get_thread_meta_store()` selector.
- `src/core/api.py` — Replace the module-level `_thread_meta` dict and inline
  `_get_thread_meta` with calls through `get_thread_meta_store()`; route event
  buffer access through `get_thread_event_store()`.

## Testing notes

- Unit tests in `tests/test_events.py` — the existing `ThreadEventBuffer` suite
  must pass unchanged against the store accessed via `get_thread_event_store()`.
- New unit tests in `tests/test_thread_meta.py` — cover `InMemoryThreadMetaStore`
  get/set/list, thread isolation, and default-status behavior (parity with the old
  `_get_thread_meta`).
- API integration tests in `tests/test_api.py` — existing thread-metadata and
  replay tests must pass unchanged, proving the refactor is behavior-preserving.
- Run: `.venv/bin/python -m pytest tests/test_events.py tests/test_thread_meta.py tests/test_api.py -v`.
- Known limitation: this milestone still stores everything in memory — durability
  arrives in M2/M3.
