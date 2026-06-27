# Feature: QuickJS Code Interpreter

- Status: implemented
- ADR: docs/adr/0011-quickjs-code-interpreter.md
- Scope: middleware

## What it does

Adds an `eval` tool backed by a QuickJS JavaScript interpreter, letting the
model compose multiple read-only tool calls (search, read, recall) within a
single turn instead of round-tripping through the LLM for each invocation.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Interpreter | `eval` tool, sandboxed JS execution | Full OS sandbox |
| PTC | Read-only tool composition in JS | Write-capable tools |
| Streaming | Events flow through existing `/v1/chat/stream` | Protocol changes |
| HITL | Unaffected — PTC bypasses interrupts | Nested approvals |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| GET | `/v1/tools` | New `eval` tool appears in listing |

## Safety/Permissions

- **PTC allowlist:** `search_codebase`, `read_file`, `recall_thread_turns` — all
  read-only tools with no side effects.
- **Excluded tools:** `task` is NOT in PTC — it mutates subagent state. Use
  `subagents=True` kwarg (default) to enable the `task()` global instead.
- **Bounds:** 5-second timeout per eval, 32 max PTC calls per turn.
- **No destructive ops:** The PTC allowlist is intentionally read-only; JS code
  cannot invoke `write_file`, `run_command`, or other mutating tools.

## NFRs

- **Streaming:** PTC tool calls inside the interpreter appear as `tool_call`
  kind SSE events in `/v1/chat/stream` via the existing projection adapter.
  No wire contract change required.
- **Checkpointing:** Interpreter state snapshots (`mode="thread"`) persist across
  turns. The snapshot is stored in graph state and restored on thread replay.
- **HITL:** PTC calls bypass `interrupt_on`. The outer `eval` tool call is still
  subject to normal interrupt logic, but individual PTC invocations are not
  gated. The `send_response` interrupt still fires after the model completes.

## Affected modules

- `src/core/agent.py` — `_build_middlewares()` adds `CodeInterpreterMiddleware`
- `pyproject.toml` — adds `langchain-quickjs` dependency

## Testing notes

- Feature spec validator (`test_feature_specs.py`) should verify this spec
  exists and has all required sections.
- PTC behavior verified indirectly via the existing streaming tests in
  `test_graph.py` — PTC calls surface as tool_call events.
- No dedicated unit tests for the interpreter itself — it's a thin wrapper
  around `langchain-quickjs` which has its own test suite.