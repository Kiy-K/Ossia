# Orchestration Audit: Deep Agents Patterns × Ossia

Generated from a Deep Agents orchestration skill (subagents, TodoList, HITL) review
against the live codebase. New agents should read this before touching subagent or
orchestrator code.

## What was checked

Against the `deep-agents-orchestration` skill (`create_deep_agent()` defaults):

| Pattern | Skill states | What Ossia does | Verdict |
|---------|-------------|-----------------|---------|
| Custom subagents | `list[dict]` with name, description, system_prompt, tools, model | ✅ 7 sync subagents + 1 async subagent (researcher) in `core/agent.py`; tester/auditor covered by sync subagents + core tools per ADR-0016 | Aligned |
| Tool permission scoping | Subagent-level tool allowlists | ✅ `_SUBAGENT_TOOL_MAP` dict in `agent.py:479–487` | Aligned |
| SubAgentMiddleware (task tool) | Auto-included by `create_deep_agent()` | ✅ Orchestrators use `task()` in JS via CodeInterpreter | Aligned (see ⚠️ below) |
| Async subagents | `AsyncSubAgentMiddleware` with 5 tools | ✅ `_build_async_subagents()` in `agent.py:340–376` | Aligned |
| HITL interrupt_on | `dict[str, bool \| InterruptOnConfig]` | ✅ `interrupt_on={"send_response": True}` in `agent.py:459–463` | Aligned |
| Checkpointer guard | Required for interrupts | ✅ `api.py:196–199` fails fast without Postgres | Aligned |
| Resume decisions | approve / edit / reject / respond | ✅ `api.py:736–760` handles all 4 | Aligned |
| **TodoListMiddleware** | Auto-included by `create_deep_agent()` | ⚠️ **No `write_todos` references found** anywhere in `src/core/` | **Gap** |
| **Explicit middleware list** | Skill doesn't say whether passing `middleware=` overrides defaults | ⚠️ `agent.py:575` passes explicit `middleware=middlewares` to `create_deep_agent()` | **Unverified** |

## ⚠️ Gap 1: TodoListMiddleware may be absent

The skill says `TodoListMiddleware` (providing the `write_todos` tool) is
**automatically included**. A grep for `write_todos`, `todo_list`, and
`TodoList` across `src/core/` returns **zero results**.

**Impact:** The agent likely cannot use the `write_todos` tool to plan and
track multi-step tasks. This may be intentional (the orchestrator pipelines
serve this role) or an oversight.

**To verify:** Add a test that checks whether the agent can call `write_todos`,
or inspect the compiled middleware chain at runtime.

## ⚠️ Gap 2: Explicit middleware list may shadow defaults

At `agent.py:575`:

```python
agent = create_deep_agent(
    ...
    middleware=middlewares,
    ...
)
```

The skill does not specify whether `create_deep_agent()` always merges default
middleware (SubAgentMiddleware, TodoListMiddleware, HumanInTheLoopMiddleware)
or whether an explicit `middleware=` list replaces them.

**To verify:** Check the `deepagents` source for `create_deep_agent`'s
middleware merge logic. If explicit lists replace defaults, then
`TodoListMiddleware` and `SubAgentMiddleware` need to be added to the
`middlewares` list in `agent.py`.

## ✅ Well-aligned areas (no action needed)

### Sync subagent definitions
`agent.py:505–511` — 7 subagents with name, description, system_prompt, tools,
and model. Matches the skill's `TypedDict` pattern exactly.

### Async subagents
`agent.py:351–376` — 1 spec (`researcher`) with `graph_id`,
`description`. Wired via `AsyncSubAgentMiddleware` at
line 502.

### Orchestrator pipeline JS templates
`src/core/orchestrators/bugfix_pipeline.py:45–83` — uses `task()` with
`responseSchema` inside JavaScript executed by CodeInterpreterMiddleware.
This is the canonical pattern from Deep Agents docs.

### HITL interrupt config
`agent.py:459–463` — only `send_response` triggers an interrupt. Matches the
skill's recommendation: low-stakes tools skip interrupt, high-stakes delivery
tool requires approval.

### Resume endpoint
`api.py:736–760` — supports approve / edit / reject / respond decision types.
Bearer token auth on the endpoint.

## Key files

| File | What it contains |
|------|-----------------|
| `src/core/agent.py` | Subagent definitions, middleware wiring, HITL config, `build_agent()` |
| `src/core/graphs/supervisor.py` | Supervisor graph (calls `agent.build_agent()`) |
| `src/core/graphs/researcher.py` | Researcher graph (same build call) |
| `src/core/graphs/auditor.py` | Auditor graph (same build call) |
| `src/core/graphs/tester.py` | Tester graph (same build call) |
| `src/core/orchestrators/` | Pipeline orchestrators (bugfix, audit, refactor) with JS templates |
| `src/core/api.py` | Resume endpoint, checkpointer guard |
| `src/core/config.py` | `Settings.enable_async_subagents` flag |

## Recommended next steps

1. **Verify middleware defaults** — check `create_deep_agent` source to see if
   explicit `middleware=` replaces the default list
2. **Add TodoListMiddleware** if missing — add `TodoListMiddleware()` to the
   `middlewares` list in `agent.py`
3. **Test `write_todos` availability** — add a test that invokes the agent and
   checks that `write_todos` tool is registered
