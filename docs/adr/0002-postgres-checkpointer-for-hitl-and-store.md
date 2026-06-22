# ADR-0002: Postgres checkpointing required for human review and cross-session memory

**Status:** accepted.
**Date:** 2026-06-20.
**Supersedes:** none.

## Context

Two distinct persistence concerns live in the same Postgres:

1. **Checkpointing** (`AsyncPostgresSaver`) — stores graph state at every step so a thread can pause and resume across HTTP requests, which is required for human-in-the-loop `interrupt_on`.
2. **Cross-session memory** (`AsyncPostgresStore`) — key/value store used for long-term user preferences and summaries, accessible to the agent across threads.

InMemoryStore and InMemorySaver exist for local dev and tests, but neither survives a process restart, so neither is sufficient for production.

## Decision

When `ENABLE_HUMAN_REVIEW=true` the FastAPI lifespan fails fast if `POSTGRES_URL` is unset, because interrupts cannot persist without a checkpointer. The BaseStore is built on top of `AsyncPostgresStore` in production and `InMemoryStore` in dev/CI.

The two resources share a single connection helper (`memory._connect`) but are entered as separate `AsyncExitStack` context managers so their lifecycles are independent.

## Consequences

- **Pro:** one database to back up; one connection-string to rotate.
- **Pro:** human review works across deploys — a paused thread survives a rolling restart.
- **Con:** every environment that wants HITL needs a running Postgres; no SQLite escape hatch.
- **Con:** the same DB outage takes down both checkpointing and long-term memory. Acceptable for v1; v2 may split them.

## Alternatives considered

1. **SQLite checkpointer.** Would have removed the Postgres dependency for HITL, but `langgraph-checkpoint-sqlite` lags behind the Postgres implementation on features like `aget_state_history`. Deferred.
2. **Redis checkpointer.** Faster but ephemeral; doesn't satisfy the "survive restart" requirement.
3. **No cross-session memory.** Defer to v2; the BaseStore wiring exists but is not yet surfaced through the API.
