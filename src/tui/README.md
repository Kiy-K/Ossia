# Ossia Terminal UI

A real-time terminal client for Ossia, built with **OpenTUI** and **React 19**.

Connects to the Ossia backend via SSE (`/v1/chat/stream`) and renders the agent run as a multi-pane terminal app.

## Quick Start

```bash
# Install dependencies
bun install

# Start the TUI (connects to http://localhost:8000 by default)
bun dev

# Or with custom backend URL
OSSIA_API_URL=http://my-server:8000 OSSIA_API_KEY=my-key bun dev
```

## Prerequisites

- **Bun** runtime (install: `curl -fsSL https://bun.sh/install | bash`)
- **Ossia backend** running on `http://localhost:8000` (or set `OSSIA_API_URL`)

## Development

```bash
# Run in watch mode (auto-restarts on file changes)
bun dev

# TypeScript typecheck
bun run typecheck

# Run tests
bun test

# Run tests with coverage
bun run test:coverage

# Run tests in watch mode
bun run test:watch

# Run React Doctor scan
bun run doctor

# Build for production
bun run build
```

## Project Structure

```
src/
  index.tsx              # Entry point: CLI renderer + React root
  App.tsx                # Main app: layout grid, event stream, state management
  types.ts               # AppState type definitions
  events/
    types.ts             # OssiaEvent type definitions
    stream.ts            # SSE stream parser (sendMessage → AsyncGenerator)
    reducer.ts           # State reducer (reduceEvent: OssiaEvent → AppState)
    reducer.test.ts      # Reducer unit tests
  components/
    StatusBar.tsx         # Thread ID, agent/tool counts, run state
    statusBar.helpers.ts  # Helper functions extracted for testability
    TimelinePanel.tsx     # Chronological event log
    ReActPanel.tsx        # Agent reasoning loop (Thought → Action → Observation)
    SubagentPanel.tsx     # Active subagent lifecycle
    ToolPanel.tsx         # Active/completed tool calls
    BackgroundTasksPanel.tsx  # Long-running async subagent tasks
    InterruptModal.tsx    # HITL interrupt overlay
    InputBar.tsx          # User input field
    primitives.tsx        # OpenTUI primitive wrappers (Box, Text, Input, ScrollBox)
tests/
  components.test.tsx    # Component rendering tests
  reducer.test.ts        # Reducer unit tests
  stream.test.ts         # SSE stream parser tests
  integration.test.ts    # End-to-end tests against live backend
```

## Architecture

```
User Input → sendMessage() → POST /v1/chat/stream
                                   ↓
                          SSE Event Stream (OssiaEvent[])
                                   ↓
                          parseSSEStream → AsyncGenerator
                                   ↓
                          reduceEvent() → AppState
                                   ↓
                          React Components render
```

### Event types handled

| Kind | Example | State impact |
|------|---------|-------------|
| `message_started` / `message_delta` / `message_completed` | AI text output | Timeline, messages, run_state |
| `tool_started` / `tool_completed` / `tool_failed` | Tool calls | Tools list, timeline, react_steps |
| `subagent_spawned` / `subagent_completed` / `subagent_failed` / `subagent_interrupted` | Subagent lifecycle | Subagents map, timeline |
| `pipeline_*` | Bugfix/audit/refactor pipelines | Subagents map, timeline |
| `async_task_*` | Background async tasks | async_tasks array, timeline |
| `interrupt` / `error` / `complete` | System events | run_state, error, interrupts |

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `OSSIA_API_URL` | `http://localhost:8000` | Backend server URL |
| `OSSIA_API_KEY` | `dev` | API key for authentication |

## Testing

### Unit tests (fast, no backend)

```bash
# Run all unit tests
bun test

# Run specific test file
bun test src/tui/tests/reducer.test.ts

# Run with coverage
bun test --coverage
```

The unit tests cover:
- **Reducer** (80+ tests): event-to-state transitions for all 20+ event types
- **Components** (40+ tests): rendering of StatusBar, TimelinePanel, ToolPanel, ReActPanel, SubagentPanel, etc.
- **Stream parser**: SSE event parsing and error handling

### Integration tests (require running backend)

```bash
# Start the backend first
(cd ../.. && ENABLE_HUMAN_REVIEW=false make dev)

# Then run integration tests
bun test src/tui/tests/integration.test.ts
```

Integration tests connect to a live Ossia backend, send messages via SSE, collect all events, feed them through the reducer, and assert the final state.

## Coverage

Current coverage: **84.85%** (functions + lines). The coverage threshold is enforced by `scripts/check-coverage.sh` (default: 80%).

## Coverage Badge

Coverage is tracked in `badges/coverage.json` (Shields.io format). The CI pipeline uploads this as an artifact on every run.

## See also

- [README.md](../../README.md) — Main project documentation
- [docs/agents/CONTEXT.md](../../docs/agents/CONTEXT.md) — Agent context reference
- [specs/SPEC.md](../../specs/SPEC.md) — API specification
