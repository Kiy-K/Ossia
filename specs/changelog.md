# Spec changelog

Human-readable record of breaking and notable non-breaking changes to the
Ossia HTTP contract. The machine-readable record is the git history of
`openapi.checked.json`.

## v0.8.0 — 2026-06-27 — security hardening (Argon2id, path traversal, dependency audit)

**Non-breaking** for the HTTP contract. No routes changed. Multiple security
fixes applied based on GitHub code scanning results.

### Weak hash replacement
- **`hashlib.sha256` → `argon2-cffi`**: The caller-id derivation in
  `verify_api_key` was changed from SHA-256 (flagged as broken/weak on
  sensitive data) to **Argon2id** via `argon2.low_level.hash_secret_raw`.
  Uses a fixed 16-byte salt for determinism, `time_cost=2`,
  `memory_cost=65536` (64 MB), `hash_len=16` (128 bits). Argon2 is the
  current standard for key hashing and is not flagged by any code scanner.
- **New dependency:** `argon2-cffi>=23.1.0` in `pyproject.toml`.

### Path traversal prevention
- **Dataset path hardcoded**: The `POST /v1/eval` endpoint no longer accepts
  a user-supplied `dataset_path`. The golden dataset is now loaded from a
  hardcoded path (`tests/golden_dataset.json` relative to project root),
  eliminating the path traversal risk surface entirely.
- **`EvalRequest` schema simplified**: Removed `dataset_path` field.
  Only `min_pass_rate` is configurable from the client.
- All 6 CodeQL path-injection alerts resolved (2 fixed by hardcoding, 4
  stale alerts dismissed as false positives after code restructuring).

### Dependency migration
- **`duckduckgo-search` → `ddgs`**: The old `duckduckgo-search` pip package
  was renamed to `ddgs`. Updated import from `from duckduckgo_search import
  DDGS` to `from ddgs import DDGS` and changed the dependency in
  `pyproject.toml` to `ddgs>=9.0.0`. API is identical (same
  `DDGS.text()` / `DDGS.news()` methods).

### GitHub code scanning status
- **9 alerts total, 0 open** as of this release.

## v0.7.0 — 2026-06-27 — monitoring stack, Makefile, Caddy reverse proxy, Docker refactor

**Non-breaking** for the HTTP contract. No routes changed. The project gains
a monitoring stack, a Makefile for common workflows, a Caddy reverse proxy,
and restructured Docker composition.

### Monitoring & observability
- **New dependency:** `prometheus-fastapi-instrumentator>=8.0.0` in `pyproject.toml`.
- **New endpoint:** `GET /metrics` (Prometheus format) exposed by the Instrumentator
  at module level (not inside lifespan, to avoid Starlette middleware-freeze error).
  Metrics: HTTP request count, latency (bucketed), and active requests.
- **New monitoring config directory:** `monitoring/` with:
  - `prometheus.yml` — scrape config for ossia (15s interval), prometheus, loki, grafana
  - `loki-config.yml` — single-node Loki with filesystem storage, TSDB index
  - `grafana/datasources.yml` — auto-provisions Prometheus + Loki datasources
  - `grafana/dashboard.json` — 11-panel pre-loaded dashboard
  - `grafana/dashboard-provider.yml` — auto-loads dashboards on startup
- **Docker compose** updated with `prometheus`, `loki`, `grafana` services
  under the `monitoring` profile. All services get `logging` config.
- **New env vars:** `GRAFANA_USER`, `GRAFANA_PASSWORD`, `PROMETHEUS_RETENTION`,
  `LOG_DRIVER`, `LOG_MAX_SIZE`, `LOG_MAX_FILE` in `.env.example`.

### Makefile
- **40+ targets** organized into categories: Setup, Development, Testing,
  Docker, Monitoring, Quality, Spec, TUI, Cleanup.
- Auto-generated `help` from inline `##` comments.
- `test-focused` errors with usage hint if `path=` is omitted.
- `install` auto-creates `.venv` if missing.
- Targets use `uv` for Python package management and `docker compose` for containers.

### Reverse proxy & Docker
- **Caddy** is now the default reverse proxy (replaces direct ossia:8000 exposure).
  Provides: auto HTTPS via Let's Encrypt (`DOMAIN=` env), security headers
  (HSTS, XSS protection), JSON access logs with rotation.
- **Nginx** config remains as a commented-out alternative in `docker-compose.yml`.
- **Docker compose** restructured with:
  - Shared `x-ossia-env` anchor for all ossia env vars
  - `postgres` healthcheck (5s interval, `pg_isready`)
  - Caddy with persistent cert storage volumes
  - `monitoring` profile for Prometheus/Loki/Grafana
  - Internal `ossia-net` bridge network for all services
- **Fixed:** Prometheus Instrumentator moved from lifespan to module level
  to avoid Starlette's "Cannot add middleware after an application has started" error.

### Source migration
- **`src/ossia/` → `src/core/`**: The importable module was renamed from `ossia`
  to `core` to avoid duplicating the brand name in the module path.
  Every `from ossia.X import` was updated to `from core.X import`.
  See `pyproject.toml` for the `[tool.hatch.build.targets.wheel] packages`
  change and `AGENTS.md` for the full migration notes.
- **New submodules:** `src/core/events/` (normalizer, buffer, serializers),
  `src/core/graphs/` (supervisor, researcher, tester, auditor),
  `src/core/orchestrators/` (bugfix, audit, refactor pipelines).
- **New scripts:** `scripts/coverage_matrix.py`, `scripts/generate_changelog_entry.py`.

## v0.6.0 — 2026-06-26 — thread event buffer, code interpreter

**Non-breaking** for the HTTP contract. Two new feature surfaces:
- **Thread event buffer** (see ADR-0012): `GET /v1/threads/{id}/events`
  replays the normalized SSE event stream for any thread. `DELETE` clears the
  buffer. TUI clients can late-join a running session.
- **Code interpreter** (see ADR-0011): `langchain-quickjs` middleware adds a
  sandboxed `eval` tool. PTC allowlist: `search_codebase`, `read_file`,
  `recall_thread_turns` (read-only only).
- **New dependency:** `langchain-quickjs>=0.1.0` (indirectly via
  `deepagents[quickjs]>=0.6.11`).

## v0.5.0 — 2026-06-22 — runtime context propagation (OssiaContext)

**Non-breaking** for the HTTP contract. No routes changed; the spec
schema and pinned `openapi.checked.json` are unchanged. The agent
runtime gains a per-invoke context dataclass that propagates to all
subagents and is readable from any tool via the deepagent
``ToolRuntime``.

- **New module** `src/core/context.py` exports
  :class:`OssiaContext`, a frozen dataclass with three fields:
  ``caller`` (X-API-Key hash, required), ``request_id`` (UUID for
  tracing, optional), ``provider`` (model provider, defaults to
  ``"openrouter"``).
- **Agent wiring**: ``create_deep_agent(..., context_schema=
  OssiaContext)`` so any tool that wants the caller's identity
  can read it from ``runtime.context.caller`` (per the Deep Agents
  "Context engineering" doc).
- **FastAPI plumbing**: the ``/v1/chat`` and ``/v1/chat/stream``
  handlers now construct an ``OssiaContext`` from the validated
  API key and the per-request id, then pass it as ``context=`` to
  ``agent.ainvoke`` / ``agent.astream_events``.

See `docs/adr/0010-runtime-context-ossia-context.md` for the
full decision record.

## v0.4.0 — 2026-06-22 — Tavily-backed web tools + Nebius adapter removed

**Non-breaking** for the HTTP contract. No routes changed. The agent
runtime gains three new tools and drops the unused Nebius adapter.

- **New tools:**
  - `internet_search(query, max_results, topic)` — Tavily-backed web
    search with DuckDuckGo fallback.
  - `fetch_url(url, question=None)` — Tavily-backed URL extraction
    with DuckDuckGo fallback.
  - `qna_search(query, topic)` — Tavily-backed one-shot Q&A with
    DuckDuckGo fallback.
- **Nebius adapter removed**: `Provider.NEBIUS` raises
  `NotImplementedError`.
- **New dependency:** `tavily-python>=0.7.0`.

## v0.3.0 — 2026-06-22 — subagent descriptions and system prompts tightened

**Non-breaking** for the HTTP contract. The four custom subagents
gained action-oriented descriptions and output format constraints.

## v0.2.0 — 2026-06-22 — agent-scoped memory + episodic recall

**Non-breaking**. Two new memory surfaces: semantic memory
(`/memories/AGENTS.md` via LangGraph Store) and episodic recall
(`recall_thread_turns` tool via checkpointer).

## v0.1.0 — 2026-06-22 — streaming switches to the v3 protocol

**Breaking** for clients of `POST /v1/chat/stream`. Wire shape changes
from flat v2 event dicts to a discriminated-union envelope with
`kind` + per-kind `data`.

## v0.0.1 — 2026-06-22 — initial unified API

**Breaking** (no prior contract to break — first pinned version).
New `/v1/*` surface replaces un-versioned routes. Pydantic-typed
models, standard error envelope, new routes for tools/threads/resume/
audit/eval.
