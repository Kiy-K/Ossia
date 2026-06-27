# Feature: Thread Event Buffer / Replay

- Status: implemented
- ADR: docs/adr/0012-thread-event-buffer-replay.md
- Scope: route | infrastructure

## What it does

Buffers normalized ``OssiaEvent`` objects in memory after each ``POST /v1/chat/stream``
completes, and exposes two endpoints — ``GET /v1/threads/{id}/events`` for retrieval and
``DELETE /v1/threads/{id}/events`` for clearing — so clients can replay, debug, audit, or
late-join a thread's event stream without re-running the agent.

Without this feature, events are streamed once via SSE and lost once the connection closes.
A late-joining TUI session or a developer debugging a past run has no way to reconstruct
what happened. The buffer makes every streaming invocation's full event log addressable
by thread id for the lifetime of the server process.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Event storage | In-memory dict keyed by scoped thread id; events appended after each stream | Persistent storage (file, Postgres, etc.) |
| Retrieval | `GET /v1/threads/{id}/events` returns all buffered events as ordered list of dicts | Query parameters for filtering by type, source, or time range |
| Clearing | `DELETE /v1/threads/{id}/events` clears all events for a thread | Partial clearing, TTL-based expiry, or scheduled cleanup |
| Isolation | Threads scoped per-caller (hash-prefixed); one thread cannot affect another | Cross-caller access (by design — the caller hash is the isolation boundary) |
| Capacity | Bounded at 10,000 events per thread (~5 MB at ~500 B/event); trims oldest first | Configurable limit, eviction callbacks, or memory-pressure warnings |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| `GET` | `/v1/threads/{thread_id}/events` | New — returns `ThreadEventsResponse` with `thread_id`, `events` (list of dicts), and `count` |
| `DELETE` | `/v1/threads/{thread_id}/events` | New — clears buffer for the scoped thread, returns `{"thread_id": ..., "cleared": true}` |

## Safety/Permissions

- **Thread isolation**: Both endpoints call ``_thread_id_for(caller, thread_id)`` with the
  authenticated caller hash, exactly as every other thread-scoped endpoint does. Two callers
  with different API keys cannot read or clear each other's event buffers.
- **No interrupt / PTC impact**: The buffer is populated *after* the event stream has been
  fully consumed and yielded to the client. It does not create new HITL interrupt points,
  does not add new tool calls, and does not affect the agent runtime at all.
- **In-memory only**: Events are never written to disk. A server restart clears all buffers.

## NFRs

- **Streaming:** The buffer is populated as a side effect *after* the SSE stream has been
  fully consumed. Streaming latency is unaffected — events are normalized and yielded in
  real-time; the buffer store is a single O(n) append after the generator exits.
- **Checkpointing:** Unaffected. The buffer is independent of the LangGraph checkpointer.
  Threads that have never been streamed (only used via `POST /v1/chat` non-streaming) have
  no buffered events.
- **HITL:** Unaffected.
- **Performance:** Each normalized event is ~500 bytes. At 10,000 events per thread the
  buffer consumes ~5 MB per active thread. The store call is O(events) after stream end;
  the retrieval call is O(events) for ``model_dump()`` of each event. No impact on the
  hot streaming path.

## Affected modules

- `src/core/events/buffer.py` — New module: `ThreadEventBuffer` class with `store/get/clear/clear_all/thread_ids`, `MAX_EVENTS_PER_THREAD=10000` trim, and module-level singleton via `get_thread_event_buffer()`
- `src/core/events/__init__.py` — Added exports for `ThreadEventBuffer` and `get_thread_event_buffer`
- `src/core/schemas.py` — Added `ThreadEventsResponse(thread_id, events, count)` with `extra="forbid"`
- `src/core/api.py` — Wired buffer into `chat_stream` (collect events after normalization, store after stream completes); added `GET /v1/threads/{thread_id}/events` endpoint; added `DELETE /v1/threads/{thread_id}/events` endpoint

## Testing notes

- **Buffer unit tests** in `tests/test_events.py` (12 tests): `test_buffer_store_and_get`,
  `test_buffer_empty_store_is_noop`, `test_buffer_append_on_multiple_stores`,
  `test_buffer_thread_isolation`, `test_buffer_clear`, `test_buffer_clear_all`,
  `test_buffer_thread_ids`, `test_buffer_get_returns_copy`, `test_buffer_global_singleton`,
  `test_buffer_trim_exceeds_max`.
- **API integration tests** in `tests/test_api.py` (3 tests):
  `test_thread_events_returns_empty_for_unknown_thread` (sync), 
  `test_thread_events_returns_events_after_stream` (async, verifies GET after storing),
  `test_thread_events_delete_does_not_affect_other_threads` (sync).
- All tests run as part of `pytest tests/`.
- Known limitation: the buffer is singleton-scoped per-process; multi-worker deployments
  (e.g. gunicorn with >1 worker) each have an independent buffer. This is acceptable for
  development and single-process production deployments.
