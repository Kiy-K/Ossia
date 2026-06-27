# AGENTS.md

Repo-specific guidance for OpenCode and other coding sessions in `/home/khoi/ossia`. Read this before touching code; it captures the things that took multiple file reads to figure out.

## What this repo is

Ossia — a portable, model-agnostic support agent built on LangChain Deep Agents. The unified HTTP API at `/v1/*` is the only runtime entry point; CLI scripts, the notebook, and the TUI are thin HTTP clients. Spec-driven: `specs/openapi.checked.json` is the pinned contract, `tests/test_openapi_drift.py` fails the suite on drift.

Architecture and intent: `README.md` (overview), `specs/SPEC.md` (narrative spec), `docs/adr/0001..0012.md` (twelve design decisions). Read those before changing behavior.

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

# Run the coverage matrix (generates specs/coverage.md)
.venv/bin/python scripts/coverage_matrix.py

# Generate a draft changelog entry from implemented feature specs
.venv/bin/python scripts/generate_changelog_entry.py --dry-run

# Run the audit (spins up uvicorn, hits /v1/audit, tears down)
OSSIA_API_KEY=dev .venv/bin/python scripts/audit_ossia.py

# Run the eval
OSSIA_API_KEY=dev .venv/bin/python scripts/eval_ossia.py

# Start the server
OSSIA_API_KEY=dev .venv/bin/python -m uvicorn core.api:app --host 127.0.0.1 --port 8000

# Terminal UI (separate bun + OpenTUI/React project)
cd src/tui && bun install && bun dev
```

## Using the Makefile

The project has a comprehensive `Makefile` with 40+ targets. **Prefer `make` over raw commands** for most workflows:

```bash
make install          # Install deps (auto-creates .venv)
make env              # Create .env from .env.example
make dev              # Start dev server with hot reload
make test             # Run full test suite
make docker-up        # Start full Docker stack (ossia + postgres + caddy)
make monitor-up       # Start monitoring stack (prometheus + loki + grafana)
make format           # Format + lint code with ruff
make typecheck        # Typecheck with mypy + pyright
make clean            # Stop Docker + remove Python caches
```

Run `make help` to see all targets with descriptions.

## Makefile — key targets reference

| Target | What it does |
|--------|-------------|
| `install` | Create venv + install deps |
| `setup` | Alias for install |
| `env` | Copy `.env.example` → `.env` |
| `dev` | `uvicorn core.api:app --reload` on port 8000 |
| `format` | `ruff check --fix` + `ruff format` |
| `lint` | `ruff check src tests scripts` |
| `typecheck` | `mypy src` + `pyright src` |
| `check` | `lint` + `typecheck` (sequential) |
| `test` | `pytest tests/ -v` |
| `test-focused path=...` | Run a specific test |
| `test-coverage` | Tests with `--cov` report |
| `spec-docs` | Regenerate `openapi.checked.json` |
| `spec-coverage` | Generate route×feature coverage table |
| `docker-build` | `docker build -t ossia .` |
| `docker-up` | `docker compose up -d --build` (ossia + postgres + caddy) |
| `docker-down` | `docker compose down` |
| `docker-logs` | `docker compose logs -f` |
| `docker-ps` | `docker compose ps` |
| `monitor-up` | Start stack with monitoring profile |
| `monitor-down` | Stop monitoring services |
| `metrics` | `curl localhost:9090/api/v1/query?query=up` |
| `audit` | Run `scripts/audit_ossia.py` |
| `eval` | Run `scripts/eval_ossia.py` |
| `tui` | Start TUI (bun dev in src/tui) |
| `clean` | Stop Docker + remove Python caches |
| `clean-all` | Nuclear: removes `.venv`, `.env` too |

## Agent skills

- **Issue tracker:** GitHub Issues on `Kiy-K/Ossia` (https://github.com/Kiy-K/Ossia/issues).
- **Triage labels:** `bug`, `feature`, `enhancement`, `ready-for-agent`, `needs-triage`, `blocked`, `good-first-issue`.
- **Domain docs:** `docs/agents/CONTEXT.md` (glossary, ADRs, architecture).
- **ADR index:** `docs/adr/0001..0012.md` — the twelve design decisions (incl. 0011 QuickJS code interpreter, 0012 thread event buffer replay).

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
- **System `OSSIA_API_KEY` env var may override .env.** If you have a stray `export OSSIA_API_KEY=...` in your shell profile, it will override the `.env` file. Use `unset OSSIA_API_KEY` before `make docker-up` to ensure the .env value is used.

## Spec-driven workflow (do not skip this)

1. Edit handlers in `src/core/api.py` and/or models in `src/core/schemas.py`.
2. Run `pytest -k openapi_drift`. It will fail with a unified diff and a one-line fix command.
3. If the change is intentional, run `scripts/update_openapi_spec.py` and commit the new `specs/openapi.checked.json` alongside the code.
4. Add an entry to `specs/changelog.md`. New routes, new fields, type changes, renames are all breaking — bump `/v1/...` to `/v2/...` and document the migration.
5. Add a test in `tests/test_api.py` for new routes; add a test in `tests/test_graph.py` or `tests/test_mcp_tools.py` for new agent or MCP behavior.

There are **no deprecated aliases** in this codebase by house style. Do not add back-compat shims; remove and migrate.

## Feature specs

Feature specs live in `specs/features/<slug>.md`. They formalize capability
coverage (what a feature does, which routes it touches, what NFRs it carries)
and are validated by `tests/test_feature_specs.py`.

### Creating a new feature spec

Copy `specs/features/TEMPLATE.md` to `specs/features/<slug>.md` and fill in
the sections. Required sections: What it does, Scope table, Endpoint impact,
Safety/Permissions, NFRs, Affected modules, Testing notes.

```bash
cp specs/features/TEMPLATE.md specs/features/my-feature.md
# Edit the new file, then validate:
.venv/bin/python -m pytest tests/test_feature_specs.py -v
```

### Key scripts

- `scripts/coverage_matrix.py` — generates `specs/coverage.md`, a route×feature
  coverage table from the OpenAPI spec and all feature specs. Run after adding
  or changing a feature spec.
- `scripts/generate_changelog_entry.py` — scans implemented feature specs and
  generates a draft `specs/changelog.md` entry. Use `--dry-run` to preview.

## Feature spec validation

`pytest tests/test_feature_specs.py` validates:
- All required sections are present.
- Status/Scope/ADR frontmatter fields exist and are valid.
- Endpoint references in `## Endpoint impact` tables match actual API routes.
- ADR cross-references resolve to existing files in `docs/adr/`.

## Graph architecture (langgraph.json)

Four graphs registered for the LangGraph Platform deployment model:

| Graph | File | Purpose |
|-------|------|---------|
| `supervisor` | `src/core/graphs/supervisor.py` | Main agent — same as `build_agent_async()`. All sync subagents, middleware stack, 14 tools. |
| `researcher` | `src/core/graphs/researcher.py` | Standalone graph for async `researcher` subagent tasks |
| `tester` | `src/core/graphs/tester.py` | Standalone graph for async `tester` subagent tasks |
| `auditor` | `src/core/graphs/auditor.py` | Standalone graph for async `auditor` subagent tasks |

**Important:** All 4 graph files are structurally identical — they all call `core.agent.build_agent()`. They exist so the LangGraph Platform has separate `graph_id` values to route async subagent runs to. When running locally via `uvicorn core.api:app`, `langgraph.json` is not read; the main app creates the agent in-process.

The 3 async subagents (researcher, tester, auditor) are wired into the main agent via `AsyncSubAgentMiddleware` in `core/agent.py`, which exposes `start_async_task`, `check_async_task`, etc. tools. They require a LangGraph Cloud deployment to actually execute.

## Layout (current)

```
src/core/            # Library: agent, memory, tools, mcp_tools, middleware,
                     # schemas, audit, eval, cli_helper, api, events,
                     # graphs (supervisor, researcher, tester, auditor),
                     # orchestrators (bugfix, audit, refactor pipelines)
src/tui/             # OpenTUI/React terminal client (bun)
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
docs/adr/            # 0001..0012 — design decisions
docs/skills/         # SKILL.md files (web-search, code-review)
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
    `checkpointer.list({"configurable": {"thread_id": ...}})`.
- **Subagents** (see ADR-0008) are wired as the canonical
  `SubAgent` dict shape (name, description, system_prompt, tools,
  model). Seven custom roles: `code-researcher`, `bug-diagnostician`,
  `fix-proposer`, `test-runner`, `ui-debugger`, `diagram-analyzer`,
  `visual-regression-reviewer`. The Deep Agents `general-purpose`
  subagent is auto-added as a fallback.
- **Async subagents** (see `changelog.md` v1.6.0) re-use the
  same role catalogue as `AsyncSubAgent` specs. The
  `AsyncSubAgentMiddleware` is auto-injected by `create_deep_agent`
  when async subagents are wired; it exposes five tools
  (`start_async_task`, `check_async_task`, `update_async_task`,
  `cancel_async_task`, `list_async_tasks`). Gated by
  `Settings.enable_async_subagents` (default `true`).
- **Code interpreter** (`langchain-quickjs`):
  `CodeInterpreterMiddleware` adds an `eval` tool for sandboxed
  QuickJS JavaScript execution. PTC allowlist: `search_codebase`,
  `read_file`, `recall_thread_turns` — read-only tools only.
- **Tools** (see ADR-0009): every tool is a plain `@tool`-decorated
  function with a Pydantic `args_schema`. Tavily-backed web tools
  fall back to DuckDuckGo when key is unset.
- **Runtime context** (see ADR-0010) flows through every call as
  a frozen `OssiaContext` dataclass (`caller`, `request_id`,
  `provider`).
- `interrupt_on` is `dict[str, bool | InterruptOnConfig]`; silently skipped when there is no checkpointer.

## MCP gotchas

- `mcp.client.streamable_http.streamable_http_client` uses an anyio task group with task-affine cancel scopes. The worker-per-task pattern in `MCPToolkit` keeps the cancel scope out of the parent's task.
- Per-server connect timeout is bounded (`mcp_connect_timeout` 1.0–60.0 s).
- `MCPToolkit.mcp_tool_servers` (a `dict[tool_name, server_name]`) is the source of truth for `/v1/tools` provenance.

## Terminal UI (src/tui)

`src/tui/` is a separate OpenTUI/React 19 project (Bun runtime). It is
purely a client — it consumes `/v1/chat/stream` over SSE and renders
the run as a multi-pane terminal app.

- **Do not import from `src/tui/`** in Python.
- Default `API_URL=http://localhost:8000`, `API_KEY="dev"`.

## Docker compose

`docker compose up -d --build` starts three services by default:
- **ossia** (the FastAPI agent server)
- **postgres** (state persistence)
- **caddy** (reverse proxy with auto HTTPS)

With `--profile monitoring`, also starts:
- **prometheus** (metrics scraping)
- **loki** (log aggregation)
- **grafana** (pre-configured dashboards)

See `docker-compose.yml` for full service definitions and env var references.

## Monitoring stack

Config files in `monitoring/`:
- `monitoring/prometheus.yml` — scrape config (ossia, prometheus, loki, grafana)
- `monitoring/loki-config.yml` — single-node Loki with filesystem storage
- `monitoring/grafana/datasources.yml` — auto-provisions Prometheus + Loki
- `monitoring/grafana/dashboard.json` — 11-panel pre-loaded dashboard
- `monitoring/grafana/dashboard-provider.yml` — auto-loads dashboards

Start with: `make monitor-up` (or `docker compose --profile monitoring up -d`)

## Deploy

- **Docker-based:** `make docker-build` + `make docker-up` (or `docker compose up -d --build`)
- **Raw process:** `uvicorn core.api:app --host 0.0.0.0 --port 8000`
- **LangGraph Platform** (`make docker-langgraph-build`): builds a LangGraph Platform server image. Only serves the 4 sub-graphs via generic `/runs` API — does NOT serve custom `/v1/*` routes. Not recommended unless you're deploying the async subagent infrastructure separately.

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

## Ponytail — lazy senior dev mode

Ponytail is an always-on set of rules that makes the AI agent think like a lazy
senior dev. The best code is the code never written. Load the full skill with
`skill ponytail` for progressional levels (lite, full, ultra) and details.

### The ladder
Before writing any code, stop at the first rung that holds:
1. **Does this need to exist at all?** (YAGNI)
2. **Already in this codebase?** Reuse it, don't rewrite.
3. **Stdlib does it?** Use it.
4. **Native platform feature covers it?** Use it.
5. **Already-installed dependency solves it?** Use it.
6. **Can it be one line?** One line.
7. **Only then:** the minimum code that works.

The ladder runs *after* you understand the problem, not instead of it.

### Rules
- No abstractions that weren't explicitly requested.
- No new dependency if it can be avoided.
- No boilerplate nobody asked for.
- Deletion over addition. Boring over clever. Fewest files possible.
- Shortest working diff wins, but only once you understand the problem.
- Question complex requests: "Do you actually need X, or does Y cover it?"
- Mark intentional simplifications with a `ponytail:` comment naming the ceiling and upgrade path.

### Not lazy about
Understanding the problem (read it fully before picking a rung), input
validation at trust boundaries, error handling that prevents data loss,
security, accessibility, anything explicitly requested.

### Available skills
| Skill | What it does |
|-------|-------------|
| `ponytail` | Always-on lazy mode. Levels: lite, full (default), ultra. |
| `ponytail-review` | Over-engineering review of diffs. |
| `ponytail-audit` | Whole-repo over-engineering audit. |
| `ponytail-debt` | Collect shortcuts into a debt ledger. |
| `ponytail-gain` | Show benchmark impact scoreboard. |
| `ponytail-help` | Quick reference card for all commands. |