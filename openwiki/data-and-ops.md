# Data and operations

This page covers persistence, replay, metrics, and deployment-related behavior that matters when operating Ossia. Recent branch work also tightened the relationship between agent prompt design and runtime state, so memory and operations changes should be reviewed alongside tool delegation changes.

## Persistence and memory

The repository README describes Postgres as the persistence backend for checkpointing and memory, and the codebase includes Redis-backed alternatives when configured.

Relevant source areas:

- `src/core/memory.py` — memory and namespace behavior, and seed writes
- `src/core/redis_client.py` — Redis client helpers and optional Redis behavior
- `src/core/episodic.py` — episodic memory / recall support
- `src/core/cache.py` — cache behavior (includes the `redis_lock` used by seeding)
- `src/core/events/buffer.py` — in-memory event buffer for replay
- `src/core/metrics.py` — metrics helpers

### Memory seeding and store-key alignment

`seed_memory()` in `src/core/memory.py` writes seed files (e.g. `/memories/AGENTS.md`) directly to the LangGraph store. The agent's filesystem middleware (`CompositeBackend` + `StoreBackend`) strips the route prefix (`/memories/`) and uses the relative path as the store key. To keep seeded data findable by the agent's `read_file`, `seed_memory()` now derives the store key via `_store_key_from_memory_path()` (e.g. `/memories/AGENTS.md` → `/AGENTS.md`). Concurrent first boots are serialized with a `redis_lock("seed_memory", ...)` (no-op when Redis is unset, falling back to last-write-wins). Pass the *full filesystem path* as `key`; do not pre-strip the prefix.

## Event replay

`src/core/events/buffer.py` stores raw event dictionaries per thread so `GET /v1/threads/{id}/events` can replay a run without a full recomputation.

The buffer is bounded per thread and also schedules webhook delivery tasks when an event loop is running. That makes it part replay store, part integration point for downstream event subscribers.

## Observability

The backend exposes Prometheus metrics at `GET /metrics` through instrumentation in `src/core/api.py`. The README also highlights runtime event ordering and thread replay as part of the debugging model.

Operationally, this means the main things to inspect during incidents are:

- request logs and request IDs
- metrics from `/metrics`
- thread replay via `GET /v1/threads/{id}/events`
- audit/eval scripts from `scripts/`

## Deployment notes

The repository includes Docker and reverse-proxy configuration at the root (`docker-compose.yml`, `Caddyfile`, `nginx.conf`, `Dockerfile`). The README positions the project as a portable support agent, and the architecture docs describe the reverse proxy in front of the FastAPI server.

The codebase also wires CORS for the Web UI and uses graceful shutdown in the FastAPI lifespan, so deployment changes should be checked for:

- allowed origins
- shutdown behavior
- persistence backend selection
- replay buffer behavior across restarts

## Change guidance

If you touch persistence, replay, or operational behavior, inspect these first:

- `src/core/api.py`
- `src/core/events/buffer.py`
- `src/core/memory.py`
- `src/core/redis_client.py`
- `src/core/metrics.py`
- `src/core/plugin.py` if the change affects loaded tools or subagents
- the deployment files at the repo root
