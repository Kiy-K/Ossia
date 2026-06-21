# Ossia — Handoff Document

**Date:** 2026-06-21
**Workspace:** `/home/khoi/ossia`
**Fresh-agent task:** Finish the one blocking bug (MCP graceful-degradation on connection failure), re-run the audit green, then the scaffold is review-ready.

---

## 1. What this project is

**Ossia** — a portable, model-agnostic support agent built on **LangChain Deep Agents** for the Nebius Serverless Challenge. Full spec lives in the workspace at `ossia-deepagents-prompt.md` (read it first — it is the PRD: architecture, tech stack, deliverables, success criteria, Nebius deployment contract). Do not re-derive requirements from this handoff; reference that file.

## 2. Current state (done)

- **Scaffold complete** per the prompt's directory layout. All deliverables exist:
  - `src/ossia/{agent,memory,tools,config,mcp_tools,middleware,api}.py`, `src/ossia/adapters/nebius.py`, `src/ossia/prompts/system.md`
  - `tests/test_graph.py` (5 tests, passing), `notebooks/demo.ipynb`, `README.md`, `pyproject.toml`, `.env.example`, `.gitignore`, `.mcp.json`
  - `nebius/{deploy.sh,docker/Dockerfile,endpoints/*.yaml,jobs/eval.yaml}`
  - `scripts/audit_ossia.py` — runtime/memory/process + LangSmith audit harness (see §4)
- **Dependencies installed** via `uv` into `.venv` (Python 3.14.5). Key versions: `deepagents==0.6.11`, `langchain==1.3.10`, `langchain-core==1.4.8`, `langgraph==1.2.6`, `mcp==1.28.0`, `langchain-nebius==0.1.3`, `langgraph-checkpoint-postgres==3.1.0`, `duckduckgo-search==8.1.1`.
- **MCP wiring done in two places:**
  - Project runtime: `.mcp.json` → LangChain Docs server (`https://docs.langchain.com/mcp`, streamable_http, with `Accept: application/json, text/event-stream` header). The Ossia agent loads it via `MCPToolkit` in `src/ossia/mcp_tools.py`.
  - Agent/session config: `~/.config/kilo/kilo.jsonc` → same server under `mcp.langchain-docs` (so the coding agent can use its tools). Vercel MCP was removed from that file.
- **Two rounds of `/local-review-uncommitted` completed.** Round 1's 10 findings all fixed. Round 2's 10 findings: **9 fixed, 1 pending** (see §3).
- **Lint clean** (`ruff check src tests`) and **tests green** (`pytest tests/test_graph.py` → 5 passed) as of this writing.
- **Audit harness** at `scripts/audit_ossia.py` — exercises memory, process (middleware), fix-verifications, runtime (real end-to-end OpenRouter call + streaming), and LangSmith trace query. It was passing except for the MCP-degradation check (§3).

## 3. Pending work — the one blocking bug

**MCP graceful degradation on connection failure.** When a configured MCP server is unreachable, the `mcp` Python SDK's `streamable_http_client` raises `asyncio.CancelledError` from an **anyio cancel scope entered in a different task**. `except Exception` does not catch `CancelledError` (it's a `BaseException`), and the current guard in `src/ossia/mcp_tools.py` (`MCPToolkit.__aenter__`, around the per-server `try/except`) using `task.cancelling()` does **not** work — it fails with:

```
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

**Goal:** one unreachable MCP server must be skipped (log + continue), while the agent still starts with the remaining/core tools. A genuinely externally-cancelled shutdown must still propagate.

**Repro:** see `scripts/audit_ossia.py` → `audit_fix_verifications()` (the `bad-server` at `http://localhost:1/x`), or run:
```python
# config mcpServers with url "http://localhost:1/x", then:
async with build_agent_async(settings=s, include_mcp_tools=True) as agent: ...
```

**Approaches to try (in order of promise):**
1. Run each server's connect/initialize/list_tools in a **separate `asyncio.create_task`** wrapped in `asyncio.wait_for(..., timeout)`. Inspect the failure via `task.exception()` / `task.cancelled()` rather than letting the exception unwind the parent's cancel scope. This keeps the anyio scope inside the child task.
2. `asyncio.shield` the init coroutine and catch `BaseException`, re-raising only when `asyncio.current_task().cancelling() > 0` indicates an external cancel.
3. Consult current `mcp` SDK docs (via the `find-docs` skill) for the sanctioned pattern for "ignore a failed remote MCP server" — the SDK may expose a timeout or a non-raising connect path.

**Constraint:** the fix must keep working MCP sessions alive (the existing `AsyncExitStack` ownership pattern in `MCPToolkit` is correct for the happy path — verified: `search_docs_by_lang_chain` was invoked end-to-end and returned real docs). Don't regress that.

## 4. After the fix

1. Re-run `scripts/audit_ossia.py` end-to-end and confirm **all** sections pass (memory, process, fix-verifications incl. degradation, runtime, LangSmith). Run: `.venv/bin/python scripts/audit_ossia.py`. LangSmith tracing is on (project `Ossia`, EU endpoint); the script queries recent runs.
2. Re-lint + re-test (`ruff check src tests`, `pytest tests/test_graph.py`).
3. The repo was `git init`'d **only** to run `/local-review-uncommitted`. Files are staged, **no commits exist**. Do not commit unless the user asks. If the user wants the git repo gone: `rm -rf .git`.
4. Optionally offer `/local-review-uncommitted` again after the fix lands.

## 5. Round-2 review findings — status

All from the second review are addressed except #5 (the MCP degradation above). For reference, the fixes already landed:
- **CRITICAL** `api.py`: constant-time API-key compare (`secrets.compare_digest`); startup validation of `OSSIA_API_KEY`; require `POSTGRES_URL` when human review on (fail fast).
- **CRITICAL** `agent.py`: `interrupt_on` now gated on checkpointer presence (`_interrupt_config`); extracted `_compile_agent` helper so sync/async builders share kwargs.
- **WARNING** `middleware.py`: retry jitter precedence fixed (`_wait_seconds`); `get_running_loop()`; revision counter reclaimed via `after_agent`/`aafter_agent` (`_cleanup`); shared `_reset`/`_cleanup`.
- **WARNING** `agent.py`/`config.py`: Ollama uses `ollama_base_url` setting (no more URL-as-model-name no-op).
- **WARNING** `nebius/jobs/eval.yaml` + `deploy.sh`: image pinned to `v0.1.0`, registry templated via `${NEBIUS_PROJECT_ID}`, `envsubst` guard + `NEBIUS_PROJECT_ID` required.

## 6. Key gotchas / environment

- **No `pip` in `.venv`** — it's a uv-managed venv. Use `uv pip install ...` (uv 0.11.16 at `/home/khoi/.local/bin/uv`). Activate with `.venv/bin/python` directly; `source .venv/bin/activate` works for python but `pip` binary is absent.
- **Secrets:** `.env` contains real `LANGSMITH_API_KEY` and `OPENROUTER_API_KEY` (**REDACTED — do not print or commit**). `.env` is gitignored. The keys are used by the audit/runtime.
- **`POSTGRES_URL`** now defaults to `None` (no more hardcoded `postgres:postgres`). No Postgres is running locally; the memory audit uses `InMemoryStore` as a stand-in. `get_checkpointer`/`get_store` raise a clear `ValueError` when unset.
- **`OSSIA_API_KEY`** env var is required for the FastAPI server (`src/ossia/api.py` validates at startup in `lifespan`).
- **Env loading:** `api.py` and the audit script call `load_dotenv(find_dotenv(usecwd=True))` before imports so LangSmith tracing picks up `.env`. A plain `load_dotenv()` from a file elsewhere fails to find `.env` (cwd-relative) — keep the `find_dotenv(usecwd=True)` form.
- **Ollama provider** needs `langchain-ollama` (optional extra `ossia[ollama]`); not installed — `create_chat_model` raises a clear `ImportError` with install instructions.
- **deepagents API notes:** `create_deep_agent(model, tools, *, system_prompt, middleware, interrupt_on, checkpointer, ...)` — there is **no** `interrupt_before` and **no** per-node retry arg; retry is implemented via a `AgentMiddleware.awrap_tool_call` (see `src/ossia/middleware.py`). `interrupt_on` is `dict[str, bool | InterruptOnConfig]` keyed by tool name.

## 7. Suggested skills

Invoke these in the next session (in this rough order):

1. **`diagnose`** — top priority. The pending bug is a concurrency/cancel-scope issue (`CancelledError` crossing task boundaries). Follow its reproduce → minimise → hypothesise → instrument → fix → regression-test loop.
2. **`find-docs`** — verify the current `mcp` Python SDK (`mcp.client.streamable_http`) semantics around cancellation, timeouts, and "ignore a failed server" against live docs before committing to an approach. Don't rely on training data for the SDK API.
3. **`tdd`** — write a failing test for "unreachable MCP server → agent still builds with core tools" first (`tests/test_graph.py` or a new `tests/test_mcp_tools.py`), then make it pass. Keeps the degradation guarantee locked in.
4. **`code-reviewer`** — after the fix + green audit, run a review pass on the changed files.
5. **`kilo-config`** — only if more MCP-server config changes are needed in `~/.config/kilo/kilo.jsonc` or project `.mcp.json`; reference, don't re-load unless editing config.

(Skip `always-verify-gcp` — this is Nebius, not GCP.)

## 8. Files most relevant to the pending work

- `src/ossia/mcp_tools.py` — `MCPToolkit.__aenter__` (the per-server loop + current broken `CancelledError` guard)
- `src/ossia/agent.py` — `build_agent_async` (the `try/finally` around `MCPToolkit.__aenter__` that falls back to core tools)
- `scripts/audit_ossia.py` — `audit_fix_verifications()` (the degradation + interrupt-without-checkpointer + revision-cleanup checks) and `audit_runtime_and_langsmith()` (real run)
