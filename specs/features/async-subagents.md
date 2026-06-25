# Feature: Async Subagents

- Status: implemented
- ADR: docs/adr/0008-subagent-design-and-routing.md
- Scope: subagent

## What it does

Background async subagents for long-running tasks. The supervisor graph
dispatches work to async subagents via `start_async_task`, `check_async_task`,
etc., without blocking the main conversation turn.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Subagent dispatch | ✅ `start_async_task`, `check_async_task`, ... | ❌ Cross-thread search |
| Roles | ✅ code-researcher, bug-diagnostician, fix-proposer, test-runner | ❌ Per-user model overrides |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| GET | `/v1/tools` | Five new tools appear in listing |

## Safety/Permissions

- No additional permissions beyond the core tool surface.
- Async subagents respect the same `permissions()` function as sync subagents.
- State is isolated to the `async_tasks` channel.

## NFRs

- **Streaming:** async subagent status updates appear as `subagent` kind events.
- **Checkpointing:** Task metadata survives via the `async_tasks` state channel.
- **HITL:** Async tasks run in background; their results re-enter the main graph
  for approval on the next `send_response`.

## Affected modules

- `src/core/async_agents.py` — `AsyncSubAgent` specs
- `src/core/agent.py` — conditionally appends async specs
- `src/core/config.py` — `enable_async_subagents` flag

## Testing notes

- `tests/test_graph.py::test_async_subagents_enabled_processes_start_async_task`
- `tests/test_graph.py::test_async_subagents_disabled_rejects_start_async_task`