# Ossia — Agent Context

Comprehensive reference for AI agents working on the Ossia project. Covers
subagents, tools, capabilities, conventions, deployment, and monitoring.

## Issue Tracker

**Type:** GitHub Issues
**Repository:** Kiy-K/Ossia
**URL:** https://github.com/Kiy-K/Ossia/issues

### Triage Labels

| Label | Meaning |
|-------|---------|
| `bug` | Something is broken |
| `feature` | New capability |
| `enhancement` | Improvement to existing capability |
| `ready-for-agent` | Fully specified, ready for an AFK agent to implement |
| `needs-triage` | Not yet triaged |
| `blocked` | Waiting on another issue or external dependency |
| `good-first-issue` | Accessible to new contributors |

---

## Makefile

The project has a `Makefile` with 40+ targets. Run `make help` to see all.

Key targets for development:
```
make install      # Install dependencies (auto-creates .venv)
make env          # Create .env from .env.example
make dev          # Start dev server with hot reload
make dev-web      # Start the Web UI (Vite dev server)
make dev-all-web  # Start backend + Web UI with one command
make test         # Run test suite
make format       # Format + lint with ruff
make typecheck    # mypy + pyright
```

Key targets for Docker:
```
make docker-up    # Start full stack (ossia + postgres + caddy)
make monitor-up   # Add Prometheus + Loki + Grafana
make docker-logs  # Tail all container logs
```

Key targets for the Web UI:
```
make dev-web      # Start Web UI dev server on port 5173
make dev-all-web  # Start backend (background) + Web UI (foreground)
make webui-e2e    # Run Playwright e2e tests
```

---

## Middleware stack (13 layers)

The 10-layer production stack from ADR-0013 is extended with 3 community middleware layers:

| # | Middleware | Purpose | Gating |
|---|-----------|---------|--------|
| 1 | PIIRedactionMiddleware | Redact secrets from tool inputs | Always |
| 2 | ToolResultCacheMiddleware | Cache exact-match tool results in Redis | `REDIS_URL` set |
| 3 | ModelRetryMiddleware | Retry transient LLM failures | Always |
| 4 | ModelFallbackMiddleware | Switch provider on outage | `fallback_provider` set |
| 5 | CircuitBreakerMiddleware | Fail-fast on overloaded services | Always |
| 6 | RetryToolMiddleware | Retry tool calls with backoff | Always |
| 7 | RevisionLoopCapMiddleware | Cap response revision loops | Always |
| 8 | ToolCallLimitMiddleware | Cap total tool calls per run | Always |
| 9 | Eager-tools | Concurrent tool dispatch (20-50% faster) | `enable_eager_tools=true` |
| 10 | CodeInterpreterMiddleware | Sandboxed QuickJS eval | Always |
| 11 | AsyncSubAgentMiddleware | Long-running background tasks | `enable_async_subagents=true` |
| 12 | Compact | Context window compaction | `enable_compact=true` (piloted) |
| 13 | Advisor | Proactive fast/slow model routing | `enable_advisor=true` (piloted) |

Each middleware layer addresses a specific failure domain — see
ADR-0013 for the full decision record and ordering rationale.

---

## NIM — Nvidia NIM provider (free tier)

Ossia supports Nvidia NIM as a model provider via the native ``ChatNVIDIA``
client from ``langchain-nvidia-ai-endpoints``:

- **Free tier** at https://build.nvidia.com — API key looks like ``nvapi-*``
- **Models**: 100+ LLMs available (llama-3.3-70b, mixtral, etc.)
- **Rate limits**: ~5 req/s shared across all models on the free tier
- **Default base URL**: ``https://integrate.api.nvidia.com/v1``
- **Local NIM**: override ``NIM_BASE_URL`` for self-hosted containers

```bash
# .env
PROVIDER=nim
MODEL=nvidia/llama-3.3-70b-instruct
NVIDIA_API_KEY=nvapi-...
```

---

## Web UI (externalStoreRuntime + custom SSE adapter)

The Web UI at ``src/webui/`` uses ``useExternalStoreRuntime`` from
``@assistant-ui/react`` — NOT ``useStreamRuntime`` from ``@assistant-ui/react-langchain``.
Ossia is self-hosted FastAPI, not LangGraph Platform; ``react-langchain`` only
works with Managed Deep Agents / LangGraph Platform deployments.
The custom SSE adapter in ``src/webui/src/runtimes/`` handles thread management,
message streaming, and interrupt state against the Ossia ``/v1/*`` HTTP contract.

---

## Subagents

Ossia has **7 sync subagents** (delegated via the `task` tool) and **3 async
subagents** (long-running, non-blocking via `AsyncSubAgentMiddleware`).

### Sync subagents

| Subagent | What it does | When to delegate |
|----------|-------------|------------------|
| `code-researcher` | Read code, find symbols, and map repo structure | When the main agent needs a file path, snippet, or architectural map without filling context |
| `bug-diagnostician` | Investigate a reported bug, failing test, or runtime error | When the main agent needs structured diagnostic output (not a fix) |
| `fix-proposer` | Propose a code change or implementation strategy | After a diagnosis is in hand; produces a minimal concrete patch summary |
| `test-runner` | Run tests, check coverage, or validate a proposed patch | When the main agent needs empirical evidence the change is safe |
| `ui-debugger` | Analyze UI screenshots, browser errors, and stacktrace images | When the user uploads a screenshot of a bug, error, or unexpected UI state |
| `diagram-analyzer` | Parse architecture diagrams, flowcharts, and system dependency graphs | When the user needs structural understanding of a visual system diagram |
| `visual-regression-reviewer` | Compare before/after UI screenshots for regressions | When the user provides a pair of images for visual diff analysis |

All sync subagents receive `[search_codebase, search_knowledge_base]` tools
and the same model as the main agent. They return concise summaries (200-250
word limit) — raw tool outputs are stripped to keep main agent context clean.

### Async subagents

| Subagent | What it does | When to launch |
|----------|-------------|----------------|
| `researcher` | In-depth codebase research and repo-wide analysis | For broad searches, architectural mapping, and dependency tracing that would take many turns inline |
| `tester` | Run test suites and validation pipelines | For long test runs, coverage analysis, and flaky test detection that should not block the conversation |
| `auditor` | Repository audits and indexing tasks | For comprehensive codebase audits, lint sweeps, and batch analysis jobs |

Async subagents run via `AsyncSubAgentMiddleware`. The supervisor starts them,
checks progress, and retrieves results without blocking. Each spec maps to a
`graph_id` registered in `langgraph.json` (see graph architecture below).

---

## Graph architecture (langgraph.json)

The `langgraph.json` registers 4 graphs for LangGraph Platform deployments:

```
langgraph.json → supervisor → src/core/graphs/supervisor.py
               → researcher → src/core/graphs/researcher.py
               → tester     → src/core/graphs/tester.py
               → auditor    → src/core/graphs/auditor.py
```

All 4 call `core.agent.build_agent()` and produce identical compiled agents.
They exist so the async subagent middleware can route `start_async_task("researcher")`
to a registered graph_id. When running locally via `uvicorn core.api:app`,
`langgraph.json` is not used — the agent is built in-process.

---

## Tools

All 14 tools registered via `create_core_tools()`:

### Search & Research
- **`search_codebase`** — Search the local project codebase for tokens, symbols, error strings. Prefer over internet_search for anything inside the project.
- **`search_knowledge_base`** — Search local KB for project-specific docs, known issues, troubleshooting guides. Falls back to DuckDuckGo.
- **`internet_search`** — Web search via Tavily (with DuckDuckGo fallback). Use for external API docs, releases, vendor pages.
- **`fetch_url`** — Extract content from a known URL. Supports focused Q&A via `question=` parameter.
- **`qna_search`** — One-shot answer to "what is X?" questions. Single string, no citations.

### Code Change & Validation
- **`run_tests`** — Run tests to verify changes don't break existing functionality.
- **`propose_fix`** — Produce a concrete code fix suggestion from a diagnosed bug.
- **`fetch_issue`** — Fetch a GitHub issue or PR by repo and number.
- **`create_pr`** — Create a GitHub pull request with proposed changes.

### Quality & Delivery
- **`grade_response`** — Self-check draft response quality. Capped at 3 revisions by middleware.
- **`send_response`** — Deliver final approved response. Triggers HITL interrupt when configured.

### Orchestrator Pipelines
- **`run_bugfix_pipeline`** — End-to-end automated bug-fix: diagnose → propose → test.
- **`run_audit_pipeline`** — Comprehensive code audit: research → findings → report.
- **`run_refactor_pipeline`** — Automated code refactoring: research → plan → rewrite → validate.

---

## Key Capabilities

### Multimodal
Ossia accepts images (screenshots, diagrams, UI comparisons) via the
`ChatRequest.artifacts` field. Artifacts are normalized into LangChain content
blocks and passed to the agent context. Subagents (`ui-debugger`,
`diagram-analyzer`, `visual-regression-reviewer`) receive multimodal content
for structured analysis.

### Orchestrator Pipelines
Three programmatic pipelines (`bugfix`, `audit`, `refactor`) expose JavaScript
code via the `CodeInterpreterMiddleware` that uses `task()` to chain subagents.
Each pipeline returns JS code + instruction; the agent executes via `eval()`.

### Skills
Two SKILL.md files loaded via `SkillsMiddleware`:
- `docs/skills/web-search/SKILL.md` — Web search best practices, tool selection guide
- `docs/skills/code-review/SKILL.md` — Code review checklist and output format

Skills use progressive disclosure: metadata (name + description) is in the
system prompt; full content loads on demand via filesystem tools.

### Context Engineering
- **System prompt** loaded from `src/core/prompts/system.md` via `__file__`-relative path
- **Memory** — `/memories/AGENTS.md` seeded in LangGraph Store for cross-thread persistence
- **Dynamic prompt** — `@dynamic_prompt` middleware injects caller identity hash into every model call
- **Context compression** — built-in offloading (large tool I/O saved to filesystem) and summarization (old messages compacted at 85% context limit)
- **Runtime context** — `OssiaContext` (caller, request_id, provider) passed per-invoke, propagates to subagents

---

## Docker compose

`docker compose up -d --build` starts:
- **ossia** — the FastAPI agent server (port 8000 internal)
- **postgres** — state persistence, HITL checkpointing
- **caddy** — reverse proxy on port 80/443 (auto HTTPS via Let's Encrypt)

With `--profile monitoring`:
- **prometheus** — metrics collection (15s interval, 30d retention)
- **loki** — log aggregation (filesystem storage)
- **grafana** — pre-configured dashboards (port 3000, admin/ossia)

The stack uses internal Docker networking (`ossia-net` bridge). All inter-service
communication stays on the internal network.

---

## Monitoring

Prometheus scrapes `/metrics` from the ossia container every 15s. The endpoint
is exposed by `prometheus_fastapi_instrumentator` (added in `core/api.py`).

The Grafana dashboard (11 panels, auto-provisioned) includes:
- Request rate and HTTP status code distribution
- Latency percentiles (p50, p95, p99)
- Error rate tracking
- Log explorer (Loki datasource)
- CPU and memory usage
- Service uptime

Grafana datasources and dashboards are auto-provisioned on startup via
`monitoring/grafana/` config files — no manual setup needed.

---

## Deployment

Three deployment paths:

| Path | Command | Use case |
|------|---------|----------|
| **Docker compose** | `make docker-up` | Local dev, single-server prod |
| **Raw uvicorn** | `uvicorn core.api:app` | Dev, debugging |
| **LangGraph Platform** | `make docker-langgraph-build` | Async subagent infrastructure (EXPERIMENTAL) |

The Docker approach is recommended for production. Caddy provides automatic
HTTPS and security headers. Set `DOMAIN=your.domain` in `.env` for certs.

---

## Domain Glossary

- **Ossia** — the project (brand, PyPI, env-var prefix `OSSIA_*`)
- **core** — the importable module (`from core.X import ...`)
- **Deep Agents** — the agent framework (`deepagents` package)
- **HITL** — human-in-the-loop (approval workflow on `send_response`)
- **PTC** — programmatic tool calling (interpreter calling tools from JS)
- **MCP** — Model Context Protocol (external tool servers)
- **SSE** — server-sent events (streaming protocol)
- **checkpointer** — LangGraph persistence layer (Postgres or in-memory)
- **store** — LangGraph semantic memory store
- **subagent** — delegate worker spun up by the supervisor
- **tool** — agent-callable function with typed schemas
- **middleware** — pre/post-processing around tool calls and agent runs
- **skills** — progressive markdown instruction packs (`SKILL.md`)
- **ADR** — architecture decision record (in `docs/adr/`)
- **spec-driven** — pinned OpenAPI contract + drift test workflow
- **TUI** — terminal UI (OpenTUI/React client at `src/tui/`)
- **Web UI** — browser-based UI (React + Vite + Tailwind v4 client at `src/webui/`) with ChatGPT-style layout and session sidebar with ChatGPT-style layout and session sidebar
- **episodic memory** — per-thread recall via `recall_thread_turns`
- **semantic memory** — agent-scoped long-term store at `/memories/AGENTS.md`
- **orchestrator pipeline** — programmatic multi-step automations (bugfix, audit, refactor)
- **@dynamic_prompt** — decorator injecting runtime context into the system prompt
- **instrumentator** — `prometheus_fastapi_instrumentator` exposing `/metrics`
- **reverse proxy** — Caddy (default) or Nginx (alternative), routing traffic to ossia:8000
