# AGENTS.md

Repo-specific guidance for OpenCode and other coding sessions in `/home/khoi/ossia`. Read this before touching code; it captures the things that took multiple file reads to figure out.

## What this repo is

Ossia — a portable, model-agnostic support agent built on LangChain Deep Agents. The unified HTTP API at `/v1/*` is the only runtime entry point; CLI scripts, the notebook, and the TUI are thin HTTP clients. Spec-driven: `specs/openapi.checked.json` is the pinned contract, `tests/test_openapi_drift.py` fails the suite on drift.

Architecture and intent: `README.md` (overview), `ARCHITECTURE.md` (full architectural map), `specs/SPEC.md` (narrative spec), `docs/adr/0001..0010.md` (the ten design decisions). Read those before changing behavior.

## Quick start (the commands you actually need)

The venv is uv-managed; there is **no `pip` binary** in `.venv`. Use `.venv/bin/python` directly, or `uv pip install ...` from the host.

```bash
# Install (one-time)
uv pip install -e ".[dev,notebook]"

# Run a focused test
.venv/bin/python -m pytest tests/test_api.py::test_health -v

# Run the whole suite (excludes the two pre-existing flaky HITL tests in test_graph.py)
.venv/bin/python -m pytest tests/

# Lint + typecheck
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy src
.venv/bin/python -m pyright src

# Regenerate the pinned OpenAPI spec after a deliberate contract change
.venv/bin/python scripts/update_openapi_spec.py

# Run the audit (spins up uvicorn, hits /v1/audit, tears down)
OSSIA_API_KEY=dev .venv/bin/python scripts/audit_ossia.py

# Run the eval
OSSIA_API_KEY=dev .venv/bin/python scripts/eval_ossia.py

# Start the server
OSSIA_API_KEY=dev .venv/bin/python -m uvicorn core.api:app --host 127.0.0.1 --port 8000

# Terminal UI (separate bun + OpenTUI/React project)
cd src/tui && bun install && bun dev
```

## Agent skills

- **Issue tracker:** GitHub Issues on `Kiy-K/Ossia` (https://github.com/Kiy-K/Ossia/issues).
- **Triage labels:** `bug`, `feature`, `enhancement`, `ready-for-agent`, `needs-triage`, `blocked`, `good-first-issue`.
- **Domain docs:** `docs/agents/CONTEXT.md` (glossary, ADRs, architecture).
- **ADR index:** `docs/adr/0001..0010.md` — the ten design decisions.

- **Project name** is **Ossia** (brand, PyPI, env-var prefix `OSSIA_*`,
  Docker container name `ossia-postgres`).
- **Importable module** is **`core`**, not `ossia`. The repo path
  `…/ossia/` would duplicate the brand if the source dir were also
  `ossia/`, so source lives in `src/core/` and the importable name is
  `core`. Every `from core.X import …` resolves to `src/core/X.py`.
  Uvicorn targets `core.api:app`.
- If you change either side, the other two follow:
  - Renaming the source dir → update `[tool.hatch.build.targets.wheel]
    packages` in `pyproject.toml` and the test runner's `pythonpath`.
  - Renaming the importable module → update every `from core.X` in
    `src/core/`, `tests/`, and `scripts/`.

## Environment quirks that will bite you

- **`.env` is required for the audit/runtime paths** (LangSmith, OpenRouter, etc.). `api.py` and the CLIs call `load_dotenv(find_dotenv(usecwd=True))` — keep that exact form. A plain `load_dotenv()` from elsewhere fails to find `.env` because it's cwd-relative.
- **`OSSIA_API_KEY` is required to boot the server** — the FastAPI lifespan fails fast (`_require_api_key_at_startup`) if it's missing. The audit/eval CLIs also fail fast on a missing key (no hard-coded fallback). Set it in `.env` or export it.
- **`ENABLE_HUMAN_REVIEW=true` requires `POSTGRES_URL`** to be set, because interrupts need a checkpointer. The audit/eval CLIs force `ENABLE_HUMAN_REVIEW=false` in the subprocess env; running the server with HITL on without Postgres will not start.
- **API tests set `ENABLE_HUMAN_REVIEW=false` and `POSTGRES_URL=""` around the module lifetime** (see `tests/test_api.py::_api_test_env`). They restore the original env on teardown. Don't add tests that depend on the user's real `.env` env inside the same suite.
- **The HITL tests in `tests/test_graph.py` use a custom `_FakeToolModel`** that scripts a list of `AIMessage`s. Do not pass `messages=iter([...])` to `GenericFakeChatModel` — Pydantic's `model_copy` / `model_validate` (called by `create_deep_agent` and the langchain 1.x middleware) drains the iterator, leaving subsequent `_generate` calls with no scripted response. The repo's fake uses a deque-backed list and overrides `_generate` to pop from the front. Follow that pattern for any new test fake.
- **`Settings` is `lru_cache`'d.** The API test module clears the cache after mutating env vars. If you add a test that needs different settings, clear `get_settings.cache_clear()` in your fixture's teardown.

## Spec-driven workflow (do not skip this)

1. Edit handlers in `src/core/api.py` and/or models in `src/core/schemas.py`.
2. Run `pytest -k openapi_drift`. It will fail with a unified diff and a one-line fix command.
3. If the change is intentional, run `scripts/update_openapi_spec.py` and commit the new `specs/openapi.checked.json` alongside the code.
4. Add an entry to `specs/changelog.md`. New routes, new fields, type changes, renames are all breaking — bump `/v1/...` to `/v2/...` and document the migration.
5. Add a test in `tests/test_api.py` for new routes; add a test in `tests/test_graph.py` or `tests/test_mcp_tools.py` for new agent or MCP behavior.

There are **no deprecated aliases** in this codebase by house style. Do not add back-compat shims; remove and migrate.

## Layout (current)

```
src/core/            # Library: agent, memory, tools, mcp_tools, middleware,
                     # schemas, audit, eval, cli_helper, api, async_agents
src/tui/             # OpenTUI/React terminal client (bun)
tests/               # test_api, test_graph, test_mcp_tools, test_openapi_drift,
                     # test_context, test_episodic, test_memory, test_tools
scripts/             # audit_ossia, eval_ossia, update_openapi_spec (HTTP clients)
specs/               # SPEC.md, openapi.checked.json (pinned), changelog.md
docs/adr/            # 0001..0010 — design decisions
notebooks/demo.ipynb # HTTP client via httpx
```

The real entrypoints:
- Server: `core.api:app` (FastAPI). Lifespan builds the agent via `build_agent_async`.
- Audit: `core.audit.run_audit()` returns `AuditReport`.
- Eval: `core.eval.run_eval()` returns `EvalReport`.
- CLI helpers: `core.cli_helper` (subprocess, health-check, require_api_key).
- MCP: `core.mcp_tools.MCPToolkit` — worker-per-task, graceful degradation.
- TUI: `src/tui/` — separate package; consumes `/v1/chat/stream` over SSE.

## DeepAgents / LangGraph specifics

- Installed `deepagents==0.6.11` (see `pyproject.toml`). The signature has `store=` and `backend=` kwargs; later versions may drop them. The repo's `agent.py` passes both. Verify against the installed signature (`inspect.signature(deepagents.create_deep_agent)`) before bumping.
- HITL resume: `agent.invoke(Command(resume={"decisions": [...]}), config, version="v2")`. Each decision has shape `{"type": "approve"|"edit"|"reject"|"respond", ...}`. There is **no top-level `feedback` field** — feedback lives as `message` *inside* each decision. See `docs/adr/0004` and the test `test_resume_rejects_top_level_feedback`.
- For v2 streaming, `astream_events(..., version="v2")` yields flat `{event, name, data}` dicts. v3 (`astream_events(..., version="v3")`) returns a typed projection with `.messages`, `.interrupts`, etc. **`/v1/chat/stream` is built on v3** (see ADR-0006). The internal audit harness still uses v2 for its own event enumeration — that's an implementation detail, not part of the public contract.
- The v3 streaming protocol is marked `@beta` upstream. The `core.api.chat_stream` handler adapts the typed projections to our wire contract (`kind` + per-kind `data`). If upstream changes projection attribute names, only the adapter needs to update; clients are insulated by the wire contract.
- **Memory surfaces** (see ADR-0007):
  - **Long-term / semantic** lives in the LangGraph store at
    `("ossia",)` namespace, exposed as `/memories/AGENTS.md`. The
    agent is built with `memory=[AGENTS_MEMORY_KEY]`; the seed is
    written by `core.memory.seed_memory` on first boot and is
    idempotent. **Agent-scoped only** — every caller shares the
    same `AGENTS.md`. There is no per-user scoping wired today.
  - **Episodic / per-thread recall** is the `recall_thread_turns`
    tool from `core.episodic`. It calls
    `checkpointer.list({"configurable": {"thread_id": ...}})`,
    which is the only stable cross-turn primitive on a bare
    `BaseCheckpointSaver`. Cross-thread enumeration requires the
    LangGraph SDK's `client.threads.search`; not wired in v1.
- **Subagents** (see ADR-0008) are wired as the canonical
  `SubAgent` dict shape (name, description, system_prompt, tools,
  model) per the Deep Agents subagents doc. Four custom roles:
  `code-researcher`, `bug-diagnostician`, `fix-proposer`,
  `test-runner`. Each has a specific, action-oriented description
  starting with "Delegates here when ..." and a system prompt that
  pins role + expected workflow + output format + a 200-250 word
  cap. The Deep Agents `general-purpose` subagent is auto-added and
  serves as a fallback. LangSmith traces carry
  `lc_agent_name=<subagent>` automatically; filter on that key
  to isolate a subagent's runs.
- **Async subagents** (preview, see `changelog.md` v1.6.0) re-use the
  same role catalogue as `AsyncSubAgent` specs. The
  `AsyncSubAgentMiddleware` is auto-injected by `create_deep_agent`
  when async subagents are wired; it exposes five tools
  (`start_async_task`, `check_async_task`, `update_async_task`,
  `cancel_async_task`, `list_async_tasks`) and a `async_tasks` state
  channel. Gated by `Settings.enable_async_subagents` (default
  `true`).
- **Code interpreter** (`langchain-quickjs`):
  `CodeInterpreterMiddleware` adds an `eval` tool for sandboxed
  QuickJS JavaScript execution. PTC (Programmatic Tool Calling)
  allowlist: `search_codebase`, `read_file`, `recall_thread_turns`
  — read-only tools only; no destructive operations are exposed to
  JS. The `task` tool is **not** in the PTC allowlist because it
  mutates state. `inspect.signature` introspects the middleware's
  `__init__` to pick the right persistence kwarg
  (`snapshot_between_turns` vs `mode`) — this guards against
  upstream signature changes. Interpreter events surface in
  `/v1/chat/stream` as `tool_call` SSE events (no wire contract
  change).
- **Tools** (see ADR-0009) follow the Deep Agents tools doc: every
  tool is a plain `@tool`-decorated function with a Pydantic
  `args_schema`; Deep Agents infers the schema from the signature
  and docstring. Tavily-backed web tools (`internet_search`,
  `fetch_url`, `qna_search`) read `TAVILY_API_KEY` (alias:
  `OSSIA_TAVILY_API_KEY`). When the key is unset, every tool has
  a working fallback: `internet_search` falls back to DuckDuckGo
  search; `fetch_url` falls back to a direct `httpx` + `bs4`
  text extraction (the canonical pattern from the deep-research
  doc); with `question` set, it falls back to a DDG search + top
  hit. `qna_search` falls back to a DDG-synthesized answer.
  Every fallback is clearly tagged ``backend="duckduckgo"``.
- **Runtime context** (see ADR-0010) flows through every call as
  a frozen ``OssiaContext`` dataclass (``caller``, ``request_id``,
  ``provider``). The FastAPI layer constructs one per request and
  passes it as ``context=`` to ``agent.ainvoke`` /
  ``agent.astream_events``. Tools that want the caller's identity
  read ``runtime.context.caller`` (the deepagent ``ToolRuntime`` is
  injected at call time). Context propagates to all subagents
  automatically. No middleware yet surfaces the caller in the
  system prompt itself — the doc's ``@dynamic_prompt`` pattern
  is a future enhancement.
- `interrupt_on` is `dict[str, bool | InterruptOnConfig]`; it is silently skipped when there is no checkpointer (see `_interrupt_config` in `agent.py`).

## MCP gotchas

- `mcp.client.streamable_http.streamable_http_client` uses an anyio task group with task-affine cancel scopes. The worker-per-task pattern in `MCPToolkit` keeps the cancel scope out of the parent's task. Don't try to "simplify" it back to a direct `try/except Exception` around connect — that will resurrect the original bug (transport `CancelledError` is `BaseException`, the parent's `except Exception` misses it, and anyio then refuses to exit the scope from a different task).
- Per-server connect timeout is bounded (`mcp_connect_timeout` 1.0–60.0 s); misconfiguration cannot block startup indefinitely.
- `MCPToolkit.mcp_tool_servers` (a `dict[tool_name, server_name]`) is the source of truth for `/v1/tools` provenance. The `_mcp_server` attribute on wrapped tools is a hint; Pydantic drops unknown attrs on `model_validate`, so don't rely on the attribute alone.

## Terminal UI (src/tui)

`src/tui/` is a separate OpenTUI/React 19 project (Bun runtime). It is
purely a client — it consumes `/v1/chat/stream` over SSE and renders
the run as a multi-pane terminal app (Coordinator, subagents, tool
activity, todo board, interrupt modal).

- **Do not import from `src/tui/`** in Python. The TUI has no Python
  module path; it's a TypeScript project with its own `package.json`
  and `bun.lock`.
- The TUI is a thin consumer: every state mutation is driven by SSE
  events from `/v1/chat/stream` (see `src/tui/src/events/normalize.ts`).
  When the wire contract changes, update the normalizer and the
  reducers in lockstep.
- Default `API_URL=http://localhost:8000`, `API_KEY="dev"` (matches
  `OSSIA_API_KEY=dev` in the server env). These are inlined for the
  preview; promote to env-driven config when this graduates out of
  preview.

## Deploy

`./nebius/deploy.sh` is the one-command path. Requires `docker`, `kubectl`, `envsubst` (from `gettext`), and `NEBIUS_PROJECT_ID` exported. Image is pinned to `v0.1.0` by default; override with `IMAGE_TAG=...`.

## What not to do

- Don't add deprecated aliases. The API is `/v1/*`; breaking changes go to `/v2/*`.
- Don't bypass the FastAPI server in new CLIs. Use `httpx` against the running server (or spin one up via `core.cli_helper.run_server_subprocess`).
- Don't import `core.agent` from CLIs, the notebook, or the TUI. Drive the agent through the HTTP API.
- Don't change the audit/eval logic in `scripts/` — that lives in `src/core/audit.py` and `src/core/eval.py`. The CLIs are presentational.
- Don't edit `specs/openapi.checked.json` by hand. Regenerate via `scripts/update_openapi_spec.py`.
- Don't rename `src/core/` to `src/ossia/` (or vice-versa) without
  updating `pyproject.toml`, the test `pythonpath`, and every doc
  reference in lockstep. The repo on disk is `ossia/` and the importable
  module is `core` by design — keep them that way.
