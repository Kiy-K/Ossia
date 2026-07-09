# OpenWiki quickstart

Ossia is a portable, model-agnostic AI support agent built on LangChain Deep Agents. The repository centers on a FastAPI backend that exposes a unified `/v1/*` HTTP API, plus a separate CopilotKit AG-UI endpoint (`/agui`) for the browser UI, and a terminal UI. Recent changes tightened tool-calling behavior and modularized the tool surface: key capabilities moved into named subagents, and a middleware fix (`_ForceToolChoice`) makes tool calls work end-to-end even when upstream middleware short-circuits the model call.

Key recent change areas (see `specs/changelog.md` "Unreleased" and `HANDOFF.md`):
- Tool-calling fix: `_ForceToolChoice` in `src/core/agent.py` binds tools onto the model so `eager_tools_langgraph` (which short-circuits `request.model.astream`) no longer strips the tool list — the "I don't have web access" symptom is gone.
- Coordinator tools are intentionally bounded: `create_core_tools()` dropped from 16 to 10 tools; research/fetch/test/MCP capabilities move to subagents.
- New `research` and `integrations` subagents (`src/core/agent.py`); MCP tools route exclusively to `integrations` and are never bound on the coordinator.
- CopilotKit middleware is re-wired into the agent stack (after `CodeInterpreterMiddleware`, before `_ForceToolChoice`).
- The Web UI was rewritten to a Next.js + CopilotKit frontend that talks to the backend over the AG-UI protocol at `/agui` (see [Clients](clients.md)).
- `HANDOFF.md` documents the tool-calling incident (GOAL-0002) and the regression tests that pin the fix in `tests/test_graph.py`.

Start here:
- [Architecture](architecture.md)
- [Workflows](workflows.md)
- [Clients](clients.md)
- [Data and operations](data-and-ops.md)

## What this repository does

The backend is the source of truth for agent behavior, streaming, memory, tools, plugins, and orchestration. The clients are thin HTTP consumers of the same API.

Key entry points:
- Backend API: `src/core/api.py`
- CLI launcher: `src/core/cli.py`
- Agent runtime: `src/core/agent.py`
- Plugin loader: `src/core/plugin.py`
- Terminal UI: `src/tui/`
- Web UI: `src/webui/`

## How the codebase is organized

- `src/core/` — API, agent runtime, middleware, tools, persistence, metrics, plugins, and supporting orchestration code.
- `src/tui/` — terminal client that consumes `/v1/chat/stream`.
- `src/webui/` — browser client that consumes `/v1/threads`, `/v1/chat/stream`, and resume/history endpoints.
- `docs/` — ADRs and diagram references that explain the runtime design.
- `scripts/` — maintenance, audit, and evaluation utilities.
- `specs/` — pinned OpenAPI contract and changelog for HTTP behavior.
- `tests/` — backend tests; the client subtrees also carry their own test suites.

## First things to know

- The backend is a FastAPI app created in `src/core/api.py`.
- The main runtime entry point is the `ossia` CLI defined in `src/core/cli.py`.
- The request lifecycle includes auth, rate limiting, middleware, Deep Agent orchestration, SSE normalization, and event buffering.
- Plugins are discovered from the repo root and optional config in `ossia.json` via `src/core/plugin.py`.
- The repo uses spec-driven API maintenance: `specs/openapi.checked.json` is the pinned contract and drift tests guard it.
- The agent prompt tells the model to delegate code research, bug diagnosis, fix design, testing, web lookups, and connected-service actions. The coordinator binds only a small core tool set (10 tools); the rest live on subagents: `code-researcher`, `bug-diagnostician`, `fix-proposer`, `test-runner`, `research`, `integrations`, `ui-debugger`, `diagram-analyzer`, `visual-regression-reviewer`, and `web-reviewer`. See [Architecture](architecture.md#tool-surfaces-and-delegation).
- The top-level `README.md` gives the broad overview; this wiki adds change-oriented navigation and source pointers.

## Recommended reading order for changes

1. Read [Architecture](architecture.md) to understand the API, plugin, and runtime flow.
2. Read [Workflows](workflows.md) before changing install, test, lint, eval, or spec-generation behavior.
3. Read [Clients](clients.md) before touching the TUI or Web UI.
4. Read [Data and operations](data-and-ops.md) before changing memory, event replay, metrics, or deployment behavior.
