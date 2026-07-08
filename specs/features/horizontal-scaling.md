# Feature: Horizontal-Scale Validation (Multi-Replica)

- Status: draft
- ADR: docs/adr/0015-durable-horizontal-scaling.md
- Scope: infrastructure

## What it does

Proves and documents that Ossia can run as **N stateless replicas behind the
reverse proxy** with shared Redis + Postgres, once M1–M3 have externalized all
process-local state. This milestone adds a multi-replica deployment topology
(docker-compose), an automated cross-replica integration test, and a "Horizontal
scaling" section in the docs so the guarantee is verifiable and repeatable rather
than assumed. It is the acceptance gate for the durability epic: if a request
served by replica A is fully observable (replay, thread metadata, webhooks) from
replica B, the epic is done.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Topology | docker-compose overlay running 2× `ossia` replicas + shared `postgres` + `redis` behind Caddy | Kubernetes/Helm (tracked separately in ADR-0014 future work) |
| Load balancing | Caddy round-robin across replicas, no sticky sessions required | Autoscaling policies / HPA |
| Cross-replica test | Integration test: write on replica A, read/replay/verify on replica B | Load/stress benchmarking |
| Health/readiness | Confirm `/health` works per-replica for LB health checks | New health semantics |
| Docs | "Horizontal scaling" section in README + ADR-0015 consequences | Multi-region / geo-replication |

## Endpoint impact

None — this feature does not modify the HTTP contract. It exercises existing
routes (`/health`, `/v1/chat/stream`, `/v1/threads/*`) across replicas; no schema
or route changes, so `test_openapi_drift.py` stays green.

## Safety/Permissions

- **State isolation model:** Unchanged — caller-scoped thread ids remain the
  isolation boundary regardless of which replica serves a request.
- **Shared-secret consistency:** All replicas must share the same `OSSIA_API_KEY`
  and Argon2 configuration so caller-id derivation is identical across replicas
  (documented as a deployment requirement).
- **No new external surface.** The extra replicas sit behind the same proxy and
  auth as the single-node deployment.

## NFRs

- **Streaming:** SSE works per-connection on whichever replica the LB selects;
  because replay is now shared (M3), a client can reconnect to a different replica
  and catch up via `GET /v1/threads/{id}/events`.
- **Checkpointing:** All replicas point at the same Postgres checkpointer, so HITL
  interrupts created on one replica can be resumed on another.
- **HITL:** Resume-across-replicas is validated (create interrupt on A, resume via
  `POST /v1/threads/{id}/resume` on B).
- **Performance:** Establishes the baseline that horizontal scaling increases
  throughput roughly linearly with replica count, since replicas are stateless.

## Affected modules

- `docker-compose.scale.yml` (or a `--scale ossia=2` documented invocation) — New
  overlay defining multiple replicas + shared Redis/Postgres behind Caddy.
- `Caddyfile` — Confirm/adjust upstream to load-balance across replicas.
- `tests/test_horizontal_scaling.py` — New integration test (opt-in via env flag /
  marker) that boots or targets two replicas and asserts cross-replica replay,
  thread metadata, and HITL resume.
- `README.md` — New "Horizontal scaling" section documenting requirements
  (shared Redis + Postgres, identical `OSSIA_API_KEY`) and the compose command.
- `docs/adr/0015-durable-horizontal-scaling.md` — Referenced; consequences updated
  with the validated topology.

## Testing notes

- Integration test in `tests/test_horizontal_scaling.py`, marked (e.g.
  `@pytest.mark.integration`) and skipped unless `RUN_SCALE_TESTS=1` and shared
  Redis/Postgres are reachable — mirrors how the existing Postgres/Redis-dependent
  tests gate themselves.
- Manual verification: `docker compose -f docker-compose.yml -f docker-compose.scale.yml up -d`,
  then drive traffic through Caddy and confirm replay/metadata/resume are consistent
  regardless of which replica served each request.
- Run (unit-safe subset): `.venv/bin/python -m pytest tests/ -m "not integration"`.
- Known limitation: this milestone validates correctness of shared state, not
  throughput ceilings; formal load testing is out of scope.
