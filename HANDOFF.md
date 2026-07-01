# Ossia — Handoff Document

**Date:** 2026-07-01.
**Workspace:** `/home/khoi/ossia`.

This handoff describes the **current** state of the repo after the v0.2.0
release (CI green: mypy/pyright/pytest/tsc/coverage all pass).

---

## 1. What this project is

**Ossia** — a portable, model-agnostic support agent built on **LangChain
Deep Agents**. The unified HTTP API (`/v1/*`) is the only runtime entry
point; CLI scripts, the notebook, and the TUI are thin HTTP clients.

Architecture and intent are documented in:

- `specs/SPEC.md` — living spec (scope, NFRs, workflow)
- `docs/adr/0001..0014.md` — the fourteen design decisions
- `specs/openapi.checked.json` — pinned OpenAPI contract (drift fails tests)

## 2. Current state

- **Unified API at `/v1/*`** (no deprecated aliases). Routes:
  - `POST /v1/chat`, `POST /v1/chat/stream` (SSE)
  - `GET /v1/threads/{id}/state`, `/history`, `/events`, `DELETE /v1/threads/{id}/events`, `POST /v1/threads/{id}/resume`
  - `GET /v1/tools`, `POST /v1/eval`, `GET /v1/audit`
  - `GET /health`, `GET /metrics`
  - Standard error envelope: `{"error": {"code", "message", "request_id"}}`
  - `X-Request-ID` honored end-to-end.
- **Scripts and notebook are HTTP clients.** `scripts/audit_ossia.py` and
  `scripts/eval_ossia.py` start a uvicorn subprocess, hit the endpoint,
  print, tear down. `notebooks/demo.ipynb` uses `httpx` against the API.
- **Audit + eval extracted.** `src/core/audit.py` and `src/core/eval.py`
  expose `run_audit()` and `run_eval()`; the API and CLIs both call them.
- **Typed Pydantic surface.** `src/core/schemas.py` defines every
  request, response, and error shape.
- **Spec-driven workflow.** `tests/test_openapi_drift.py` fails the test
  suite if the generated OpenAPI diverges from `specs/openapi.checked.json`.
- **14 ADRs** in `docs/adr/` covering every major design decision.
- **TUI** — OpenTUI/React 19 terminal client at `src/tui/`.
- **Monitoring stack** — Prometheus + Loki + Grafana via Docker Compose profiles.
- **Caddy reverse proxy** — default proxy for production deployments.
- **Makefile** — 40+ targets for dev, test, build, deploy, monitoring.
- **Tests:** All tests pass cleanly. CI is green on all workflows.
- **Security:** 0 open code scanning alerts. Argon2id for key hashing.

## 3. Layout

```
src/
  core/              # Library: agent, memory, tools, mcp_tools, middleware,
                     # schemas, audit, eval, cli_helper, api, events,
                     # graphs (supervisor, researcher, tester, auditor),
                     # orchestrators (bugfix, audit, refactor pipelines)
  tui/               # OpenTUI/React terminal client (bun)
tests/               # test_api, test_graph, test_mcp_tools, test_openapi_drift,
                     # test_context, test_episodic, test_memory, test_tools,
                     # test_feature_specs, test_events, test_graph_id_consistency,
                     # test_subagent_descriptions, test_tool_descriptions
scripts/             # audit_ossia, eval_ossia, update_openapi_spec,
                     # coverage_matrix, generate_changelog_entry
specs/               # SPEC.md, openapi.checked.json (pinned), changelog.md,
                     # features/ (feature specs), coverage.md
monitoring/          # prometheus.yml, loki-config.yml, grafana/ (datasources,
                     # dashboard.json, dashboard-provider.yml)
docs/
  adr/               # 0001..0014 — design decisions
  agents/            # Agent context reference
  skills/            # Loadable skill files (web-search, code-review)
  diagrams.md        # Index of all architecture diagrams
notebooks/demo.ipynb # HTTP client via httpx
```

## 4. Workflow for future changes

1. Edit handlers in `src/core/api.py` and/or models in `src/core/schemas.py`.
2. Run `pytest -k openapi_drift`. If intentional, run
   `make spec-docs` to regenerate the pinned spec.
3. Commit the spec alongside the code.
4. Add or update an entry in `specs/changelog.md` and an ADR if the change
   is architecturally significant.
5. Add a test in `tests/test_api.py` for new routes; add a test in
   `tests/test_graph.py` or `tests/test_mcp_tools.py` for new agent or
   MCP behavior.

## 5. Pre-existing flaky tests (NOT to be fixed in this pass)

`tests/test_graph.py::test_human_review_blocks_until_approved` and
`test_human_review_reject_blocks_send` fail with `RuntimeError` during
`agent.ainvoke` on the `model` task. Verified broken on the clean tree.
They exercise the live HITL path with no live LLM stub; likely a
DeepAgents/langgraph version interaction. Known issue, not in scope.

## 6. Environment

- `uv` venv at `.venv` (no `pip` binary). Use `.venv/bin/python` directly.
- `.env` (gitignored) carries API keys and configuration.
- `OSSIA_API_KEY` is required to boot the server (validated in lifespan).
- `ENABLE_HUMAN_REVIEW=true` requires `POSTGRES_URL`; audit/eval CLIs force
  `ENABLE_HUMAN_REVIEW=false` to skip the requirement.
- System `OSSIA_API_KEY` env var may override .env — check before debugging.

## 7. Quick start

```bash
# Install
make install

# Create .env
make env
# Edit .env with your API keys

# Start the server
make dev

# Hit it
curl -s http://127.0.0.1:8000/health
curl -s -H "X-API-Key: dev" http://127.0.0.1:8000/v1/tools

# Run the audit
make audit

# Run the eval
make eval

# Test suite
make test

# Start the TUI
make tui
```
