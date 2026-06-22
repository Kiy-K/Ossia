# ADR-0004: Unified HTTP API at `/v1/*` is the only runtime entry point

**Status:** accepted.
**Date:** 2026-06-22.
**Supersedes:** the prior un-versioned `/chat` and `/chat/stream` routes (removed; no deprecated aliases are kept).

## Context

Before this change, the FastAPI app exposed `/chat` and `/chat/stream` only. The audit script (`scripts/audit_ossia.py`) and the eval script (`scripts/eval_ossia.py`) imported `ossia.agent` and `ossia.middleware` directly, built the agent in-process, ran their checks, and tore it down. The notebook did the same.

Three problems:

1. **Two execution paths.** The same logic could run in the API server or in a CLI; bugs found in one weren't exercised in the other.
2. **Untyped payloads.** `/chat` and `/chat/stream` used `dict[str, Any]` request bodies and `m.dict()`-serialized responses, so clients had no way to validate inputs or interpret outputs without reading the Python source.
3. **No thread introspection.** There was no way to read a thread's state, history, or pending interrupts over HTTP — the only way to inspect a paused HITL thread was to attach a debugger to the running server.

## Decision

Expose every runtime operation under `/v1/*` (the only exception is `/health`, which is versionless). The endpoint list, request/response schemas, and error contract are pinned in `specs/openapi.checked.json` and locked by a drift test (`tests/test_openapi_drift.py`).

Routes:

- `POST /v1/chat` — typed `ChatRequest`/`ChatResponse`.
- `POST /v1/chat/stream` — typed `StreamEvent` envelopes over SSE.
- `GET /v1/tools` — list loaded tools with provenance (core vs MCP).
- `GET /v1/threads/{id}/state` and `/history` — LangGraph state and message history.
- `POST /v1/threads/{id}/resume` — `Command(resume={"decisions": [...]})`.
- `POST /v1/eval` and `GET /v1/audit` — quality gates over HTTP.

Every non-2xx response returns the standard envelope `{"error": {"code", "message", "request_id"}}`. Scripts and the notebook are thin HTTP clients; the actual logic lives in `ossia.audit` / `ossia.eval` and runs in the server process.

We do **not** keep deprecated aliases. Breaking changes bump the URL prefix (`/v2/...`) and update `specs/changelog.md`.

## Consequences

- **Pro:** one process boundary, one auth model, one telemetry path. LangSmith tracing, MCP, and middleware behavior are identical regardless of caller.
- **Pro:** typed Pydantic models become the contract; clients (including the CLI scripts) get 422 on malformed input instead of cryptic 500s.
- **Pro:** the drift test makes the contract executable — a PR that changes a route without regenerating the spec fails CI.
- **Con:** every CLI invocation pays the cost of starting a uvicorn subprocess. For the audit and eval that's fine (they take seconds-to-minutes); for trivial calls it's overkill. Acceptable: trivial calls go through the running server, not a CLI.
- **Con:** `command` from `langgraph` is now a hard dependency of the API surface. It already is, transitively, but we now reference it directly.

## Alternatives considered

1. **Keep un-versioned routes, add `/v1/audit` etc. as siblings.** Splits the contract into "old" and "new" — clients don't know which to use, and we'd have to maintain both. Rejected; no compat aliases per house style.
2. **Two separate processes: API and a "control plane" for audit/eval.** More moving parts for no real isolation benefit; the audit uses the same agent, model, and checkpointer as the API.
3. **gRPC for the internal contract, REST only externally.** More ceremony than this codebase needs; revisit if a typed streaming contract becomes critical.
