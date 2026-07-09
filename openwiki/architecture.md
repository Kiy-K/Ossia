# Architecture

This page summarizes the runtime shape of Ossia: one FastAPI backend, one Deep Agents runtime, one CopilotKit middleware layer, and two client UIs on top of the same HTTP contract. The current branch also shifts more capability into named subagents so the coordinator prompt stays bounded even as integrations grow.

## Runtime shape

- `src/core/api.py` defines the FastAPI app, the `/v1/*` REST routes, and the CopilotKit AG-UI endpoint (`/agui`).
- `src/core/cli.py` starts the backend and, by default, the TUI in one process tree.
- `src/core/agent.py` contains the agent runtime, orchestration logic, and middleware stack.
- `src/core/middleware.py` and related middleware modules compose the request stack.
- `src/core/events/` and `src/core/v3_projector.py` handle event projection and replay.
- `src/core/plugin.py` and `src/core/plugin_config.py` handle plugin discovery and config-driven extension.

The repository README describes the backend as a unified HTTP API and shows the high-level subsystem map. The current source confirms the main route set includes chat, streaming chat, thread state/history/resume, tools, eval, audit, health, and metrics.

`src/core/agent.py` appends `CopilotKitMiddleware()` after the code-interpreter (QuickJS) middleware and before `_ForceToolChoice`, so frontend-declared tools are merged into the agent stack without breaking tool-choice enforcement. The `_ForceToolChoice` middleware binds tools directly onto the model when needed, which is important because `eager_tools_langgraph` (enabled by default) short-circuits the model call and never reaches LangChain's normal bound-model path. See [Tool surfaces and delegation](#tool-surfaces-and-delegation) and `HANDOFF.md` for the incident that motivated this.

## API surface

`src/core/api.py` documents the primary routes directly in the module docstring:

- `POST /v1/chat`
- `POST /v1/chat/stream`
- `GET /v1/threads/{id}/state`
- `GET /v1/threads/{id}/history`
- `POST /v1/threads/{id}/resume`
- `GET /v1/tools`
- `POST /v1/eval`
- `GET /v1/audit`
- `GET /health`

The app also exposes `GET /metrics` via Prometheus instrumentation. The request layer attaches request IDs, caller context, CORS, and rate limiting before the agent runs.

### CopilotKit AG-UI endpoint (`/agui`)

In addition to the `/v1/*` REST API, `src/core/api.py` mounts a CopilotKit AG-UI endpoint at `/agui` during app lifespan (it is added at lifespan time, not import time). This exposes the same compiled DeepAgents graph over the AG-UI streaming protocol so the Next.js/CopilotKit Web UI can connect directly. The `/agui` path and `/v1/*` share the same agent instance, checkpointer, and store. Because the route is registered at lifespan time, `scripts/update_openapi_spec.py` regenerates the pinned spec inside a `TestClient` so it captures `/agui`.

## Middleware and request flow

The README and ADRs describe a multi-layer middleware stack. The current architecture docs in `docs/diagrams.md` and ADR-0013 show the stack as a defense-in-depth chain that covers:

- PII redaction
- model retry and fallback
- circuit breaking
- tool retry
- revision and tool-call limits
- code interpreter execution
- async subagent handling
- caller-context injection

The practical change points are `src/core/middleware.py` and the middleware adapters in `src/core/middleware_adapters.py`.

## Event streaming and replay

Streaming is a first-class part of the architecture:

- `/v1/chat/stream` emits the live event stream.
- `src/core/events/buffer.py` stores per-thread events in memory for replay.
- `GET /v1/threads/{id}/events` replays buffered events.
- `src/core/v3_projector.py` and the event modules normalize the stream into typed events for clients.

This is the main reason both client UIs can render incremental agent progress instead of only final answers.

## Tool surfaces and delegation

`src/core/agent.py` keeps the coordinator's direct tools intentionally small. `create_core_tools()` binds exactly 10 tools to the coordinator: `qna_search`, `fetch_issue`, `create_pr`, `grade_response`, `send_response`, `search_memory`, `add_memory`, `run_bugfix_pipeline`, `run_audit_pipeline`, `run_refactor_pipeline`. `send_response` stays because it is wired into `interrupt_on` for human-in-the-loop review; `create_pr` stays because it is a terminal action that should require coordinator-level authority.

Capabilities that would otherwise bloat the per-turn prompt are delegated to subagents instead:

| Subagent | Tools / capability |
| --- | --- |
| `code-researcher` | `search_codebase`, `search_knowledge_base` |
| `test-runner` | read-only + `run_tests` |
| `fix-proposer` | read-only (`propose_fix` lives on the subagent) |
| `research` | `internet_search`, `fetch_url` |
| `integrations` | all MCP tools from connected MCP servers |
| `bug-diagnostician`, `ui-debugger`, `diagram-analyzer`, `visual-regression-reviewer` | read-only code search |
| `web-reviewer` | browser-use wrapper (lazily resolved; subagent omitted if unavailable) |

MCP tools are never bound on the coordinator. `MCPToolkit.get_tools()` is passed to `_build_subagents(model, mcp_tools=...)` and only the `integrations` subagent receives them; when no MCP servers are configured, the `integrations` subagent is skipped entirely (a zero-tool subagent would waste a delegation turn). This keeps the coordinator prompt at a constant 10 tools regardless of how many connectors are active (guarded by `tests/test_mcp_tools.py::test_coordinator_tool_count_is_capped_regardless_of_mcp`).

The prompt contract in `src/core/prompts/system.md` reinforces that split by telling the agent when to delegate to each subagent.

This split matters when making changes because:
- coordinator changes affect every turn
- subagent prompt changes affect behavior and token cost without changing the HTTP API
- adding new connected services belongs in the `integrations`/MCP path, not the coordinator
- the middleware order is load-bearing: `CopilotKitMiddleware()` must run after `CodeInterpreterMiddleware` and before `_ForceToolChoice`, which must run last (closest to the model) so upstream `request.override()` calls don't strip the tool binding

## Plugins and extension points

Plugins are a real part of the runtime shape, not a separate addon system:

- `src/core/plugin.py` scans the bundled `plugins/` directory and the optional `OSSIA_PLUGINS_DIR` path.
- `ossia.json` can add plugins from custom paths, disable plugins, and pass config dictionaries.
- Plugins can register tools, subagents, and middlewares.

When changing agent capabilities, check whether the change belongs in core code or a plugin registration path.

## Deployment and state

The backend is designed to run with either Postgres-backed persistence or Redis-backed variants when configured. The README and `src/core/api.py` show that the app lifespan handles checkpoint teardown and graceful shutdown. The codebase also wires CORS for the Web UI and uses `X-API-Key` based authentication.

## When changing architecture code

Start in `src/core/api.py`, then inspect the linked runtime modules for the affected path. Watch for:

- route/docstring drift in the API module
- event shape changes that affect both clients
- middleware order changes that can alter request behavior
- replay semantics in the event buffer
- plugin loading changes that can alter the tool/subagent surface

For the most useful source references, follow `README.md`, `docs/diagrams.md`, and the ADRs linked from there.
