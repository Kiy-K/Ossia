# ADR-0012: Thread Event Buffer for Replay and Late-Joining

**Status:** accepted
**Date:** 2026-06-26

## Context

The normalized event protocol (ADR-0006) converts the DeepAgent v3 stream into ``OssiaEvent``
objects that stream in real-time via SSE. However, once the SSE connection closes, the event
stream is gone. Clients that connect late (e.g. a TUI that starts after the stream began) or
developers debugging a past run have no way to reconstruct what happened.

## Decision

Store normalized ``OssiaEvent`` objects in an in-memory ``ThreadEventBuffer`` after each
``POST /v1/chat/stream`` completes. Expose ``GET /v1/threads/{id}/events`` for retrieval and
``DELETE /v1/threads/{id}/events`` for clearing.

Key design choices:
- **In-memory only.** No Postgres, file, or external store. Keeps the feature zero-config
  and zero-dependency. Acceptable for single-process deployments and development.
- **Append-after-stream.** Events are stored *after* the SSE generator has yielded them all.
  This means the hot streaming path is unaffected — no serialization delay, no partial buffer
  writes mid-stream.
- **Bounded at 10,000 events per thread.** At ~500 B/event this is ~5 MB per thread.
  Trims oldest first on overflow.
- **Singleton per process.** Multi-worker deployments each have their own buffer. This is an
  accepted limitation for v1.

## Consequences

- Every streaming invocation doubles memory usage for its events (one copy in the buffer).
  At 10,000 events/thread this is acceptable (5 MB).
- Late-joining TUI clients can poll ``GET /v1/threads/{id}/events`` on connect to catch up.
- Server restart loses all buffered events. For persistent replay, a future version could
  checkpoint events to Postgres or an append-only log.
