# Ossia — Handoff Document

**Date:** 2026-06-22.
**Workspace:** `/home/khoi/ossia`.

This handoff describes the **current** state of the repo, including the
recent unification + spec-driven work. The 2026-06-21 handoff is superseded —
it described the MCP graceful-degradation bug as pending; that bug is fixed
and `tests/test_mcp_tools.py` locks the guarantee in (4 tests, all green).

---

## 1. What this project is

**Ossia** — a portable, model-agnostic support agent built on **LangChain
Deep Agents**. The unified HTTP API (`/v1/*`) is the only runtime entry
point; CLI scripts and the notebook are thin HTTP clients.

Architecture and intent are documented in:

- `specs/SPEC.md` — living spec (scope, NFRs, workflow)
- `docs/adr/0001..0005` — the five design decisions that shape the code
- `specs/openapi.checked.json` — pinned OpenAPI contract (drift fails tests)

## 2. Current state

- **Unified API at `/v1/*`** (no deprecated aliases). Routes:
  - `POST /v1/chat`, `POST /v1/chat/stream` (SSE)
  - `GET /v1/threads/{id}/state`, `/history`, `POST /v1/threads/{id}/resume`
  - `GET /v1/tools`, `POST /v1/eval`, `GET /v1/audit`
  - `GET /health`
  - Standard error envelope: `{"error": {"code", "message", "request_id"}}`
  - `X-Request-ID` honored end-to-end.
- **Scripts and notebook are HTTP clients.** `scripts/audit_ossia.py` and
  `scripts/eval_ossia.py` start a uvicorn subprocess, hit the endpoint,
  print, tear down. `notebooks/demo.ipynb` uses `httpx` against the API.
- **Audit + eval extracted.** `src/ossia/audit.py` and `src/ossia/eval.py`
  expose `run_audit()` and `run_eval()`; the API and CLIs both call them.
- **Typed Pydantic surface.** `src/ossia/schemas.py` defines every
  request, response, and error shape. Untyped `dict[str, Any]` payloads
  are gone.
- **Spec-driven workflow.** `tests/test_openapi_drift.py` fails the test
  suite if the generated OpenAPI diverges from `specs/openapi.checked.json`.
  `scripts/update_openapi_spec.py` regenerates the pinned spec on
  intentional changes.
- **Tests:** 11/11 in `tests/test_graph.py` + `tests/test_mcp_tools.py`
  pass, plus 16/16 in `tests/test_api.py`, plus the OpenAPI drift test.
  Two pre-existing failures in `test_graph.py::test_human_review_*` are
  unrelated to this work (verified by stashing the diff and re-running on
  the clean tree; they were broken before).
- **Audit harness runs green end-to-end via HTTP** (all 5 sections pass).

## 3. Layout (post-unification)

```
src/ossia/
├── agent.py            # Core Deep Agent logic
├── memory.py           # Postgres checkpointing + BaseStore
├── tools.py            # KB / search / grading tools
├── config.py           # Env-based Pydantic v2 settings
├── mcp_tools.py        # MCP client with worker-per-task degradation
├── middleware.py       # Retry + revision-loop middleware
├── api.py              # Unified /v1/* FastAPI app
├── schemas.py          # Typed Pydantic request/response models
├── audit.py            # run_audit() returning AuditReport
├── eval.py             # run_eval() returning EvalReport
├── adapters/nebius.py
└── prompts/system.md

tests/
├── test_graph.py       # 7 graph + subagent tests
├── test_mcp_tools.py   # 4 MCP graceful-degradation tests
├── test_api.py         # 16 FastAPI integration tests
├── test_openapi_drift.py
└── conftest.py

scripts/
├── audit_ossia.py      # HTTP client for /v1/audit
├── eval_ossia.py       # HTTP client for /v1/eval
└── update_openapi_spec.py

specs/
├── SPEC.md
├── openapi.checked.json
└── changelog.md

docs/adr/
├── 0001-provider-agnostic-via-chatopenai-baseurl.md
├── 0002-postgres-checkpointer-for-hitl-and-store.md
├── 0003-mcp-graceful-degradation-via-worker-per-task.md
├── 0004-unified-http-api-v1.md
└── 0005-spec-driven-openapi-as-contract.md

notebooks/demo.ipynb    # HTTP client via httpx
```

## 4. Workflow for future changes

1. Edit handlers in `src/ossia/api.py` and/or models in `src/ossia/schemas.py`.
2. Run `pytest -k openapi_drift`. If intentional, run
   `python scripts/update_openapi_spec.py` to regenerate the pinned spec.
3. Commit the spec alongside the code.
4. Add or update an entry in `specs/changelog.md` and an ADR if the change
   is architecturally significant.
5. Add a test in `tests/test_api.py` for new routes; add a test in
   `tests/test_graph.py` or `tests/test_mcp_tools.py` for new agent or
   MCP behavior.

## 5. Pre-existing flaky tests (NOT to be fixed in this pass)

`tests/test_graph.py::test_human_review_blocks_until_approved` and
`test_human_review_reject_blocks_send` fail with `RuntimeError` during
`agent.ainvoke` on the `model` task. Verified broken on the clean pre-diff
tree via `git stash`. They exercise the live HITL path with no live LLM
stub; likely a DeepAgents/langgraph version interaction. Filed as a known
issue; not in scope for the unification work.

## 6. Environment

- `uv` venv at `.venv` (no `pip` binary). Use `.venv/bin/python` directly.
- `.env` (gitignored) carries `OPENROUTER_API_KEY`, `LANGSMITH_API_KEY`,
  and `LANGSMITH_PROJECT=Ossia`.
- `OSSIA_API_KEY` is required to boot the server (validated in lifespan).
- `ENABLE_HUMAN_REVIEW=true` requires `POSTGRES_URL`; the audit/eval CLIs
  force `ENABLE_HUMAN_REVIEW=false` in the subprocess env to skip the
  requirement.

## 7. Quick start (post-unification)

```bash
# Install
.venv/bin/python -m pip install -e ".[dev,notebook]"   # or use uv pip install

# Run the server
OSSIA_API_KEY=demo .venv/bin/python -m uvicorn ossia.api:app --host 127.0.0.1 --port 8000

# Hit it
curl -s http://127.0.0.1:8000/health
curl -s -H "X-API-Key: demo" http://127.0.0.1:8000/v1/tools

# Run the audit (spins up the server, hits /v1/audit, tears down)
.venv/bin/python scripts/audit_ossia.py

# Run the eval
.venv/bin/python scripts/eval_ossia.py

# Test suite
.venv/bin/python -m pytest tests/
```
