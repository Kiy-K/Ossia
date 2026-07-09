# ADR-0017: Coordinator Tool Modularization

**Status:** accepted
**Date:** 2026-07-09
**Related:** GOAL-0002-tool-modularization.md, ADR-0016 (DeepAgents adoption),
the `_ForceToolChoice` tool-calling bug (HANDOFF.md, 2026-07-09).

## Context

After the DeepAgents adoption, the coordinator's per-turn prompt bound
~29 fixed tools (`create_core_tools()`) plus every tool from every
connected MCP server, appended with no cap
(`tools = [*tools, *toolkit.get_tools()]` in `build_agent_async`).
The unbounded MCP contribution meant the real production tool count grew
with every connector enabled.

This is the exact condition the codebase's own `_ForceToolChoice`
comment warned about: *"some models interpret `tool_choice=None` as 'don't
call tools' with large prompts containing many tool definitions."* The
same-day tool-calling incident (HANDOFF.md, 33 tools in the closure but
zero reaching the LLM) is the proximate motivation. Even after the
`_ForceToolChoice` fix lands, the same failure mode can recur on any
provider quirk, model swap, or new MCP connector if the tool payload
stays this large. Shrinking the payload at the source — not just
guarding against its consequences — is the more durable fix.

Secondary benefits, not the primary driver: lower per-turn token cost
(tool schemas are re-sent every turn), lower latency, and a clearer
division between "things the coordinator decides" and "things a
subagent executes."

## Decision

Apply a three-part modularization to the coordinator's tool surface.
Each part is independent; the order below matches the audit table in
GOAL-0002 §2.

### 1. Move tools that duplicate a subagent's capability

Four tools on the coordinator duplicate capability an existing subagent
already has, so the model has two ways to do the same job and the
coordinator's tool-choice decision gets more expensive for no benefit.
Remove them from the coordinator and rely on the `task` tool for
delegation:

| Tool | Subagent that already does this |
|---|---|
| `search_codebase` | `code-researcher` |
| `search_knowledge_base` | `code-researcher` |
| `run_tests` | `test-runner` |
| `propose_fix` | `fix-proposer` |

The four tools stay bound on their respective subagents via
`_SUBAGENT_TOOL_MAP` — only the coordinator's direct binding changes.
`_SUBAGENT_TOOL_MAP` itself is untouched. None of these tools is
removed from the codebase.

### 2. Introduce a `research` subagent for `internet_search` + `fetch_url`

These two were the same shape of problem as the four above: read-only,
no side effects, no reason the coordinator needs them bound directly
rather than delegated. Add a new `research` subagent with
`tools=[internet_search, fetch_url]`, patterned on the existing
`_DEV_CONCIERGE_SUBAGENTS` entries, and remove both from the
coordinator.

`qna_search` stays on the coordinator — it's a single-string answer
tool, structurally different from the two it sits next to, and the
`research` subagent's description ("look something up on the live web")
would be misleading for it.

We did not profile whether the coordinator invokes these tools directly
on a majority of turns. The default per GOAL-0002 §3.2 is to delegate;
we can revisit if production telemetry shows the round-trip cost is
material.

### 3. Route MCP tools to a single `integrations` subagent

This is the highest-leverage and highest-risk change because MCP was
the only *unbounded* source. Stop the pattern
`tools = [*tools, *toolkit.get_tools()]` in `build_agent_async`. Route
all MCP tools to a dedicated `integrations` subagent, wired only when
`MCPToolkit.get_tools()` is non-empty. The coordinator delegates via
`task` whenever a request needs an external integration.

This is **Option A** from GOAL-0002 §3.3 (one delegation point).
**Option B** (per-connector subagents) was rejected: more setup, harder
to keep in sync as connectors change, and no concrete reason favors it
over the simpler approach. The fewer moving parts here, the less likely
this regresses the next time a connector is added or removed.

### What stays on the coordinator

`qna_search`, `fetch_issue`, `create_pr`, `grade_response`,
`send_response`, `search_memory`, `add_memory`, `run_bugfix_pipeline`,
`run_audit_pipeline`, `run_refactor_pipeline` — 10 tools after this
goal. Reasoning per tool is in the audit table at GOAL-0002 §2; the two
worth calling out are:

- **`send_response`** is wired into `interrupt_on` for HITL. Removing
  it from the coordinator breaks the human-review flow. Non-negotiable.
- **`create_pr`** is a terminal action that should require
  coordinator-level authority. The pipeline tools (`run_*_pipeline`)
  encapsulate the diagnosis/draft/review steps and remain coordinator-
  bound because they are themselves terminal-style multi-step actions.

### Memory / episodic tools

`search_memory` / `add_memory` / episodic / semantic stay on the
coordinator (4 tools). Writes in particular should stay
coordinator-controlled per the existing per-caller namespace isolation
logic (`_make_memory_namespace` / `_make_scratch_namespace`).
Re-evaluate only if a future audit shows a subagent needs direct
memory access.

## Net result

| Surface | Before | After |
|---|---:|---:|
| `create_core_tools()` | 16 | 10 |
| MCP tools on coordinator | unbounded (N) | **0** |
| Subagents (sync) | 8 | 10 (`research`, `integrations`) |

The coordinator's per-turn prompt is now bounded regardless of how
many MCP connectors are active. The `test_coordinator_tool_count_is_capped_regardless_of_mcp`
test in `tests/test_mcp_tools.py` enforces the ceiling with a
parameterized guard (0, 1, 5, 25 MCP tools) so a future regression
that re-introduces `tools = [*tools, *toolkit.get_tools()]` is caught
before it ships.

## Consequences

- **Pro:** smaller per-turn prompts, lower token cost, lower latency.
  The same-day tool-calling bug is no longer sensitive to MCP growth.
- **Pro:** clearer separation of concerns — coordinator-level actions
  vs. delegated research/integration. Easier to reason about what
  each component owns.
- **Pro:** MCP connectors can be added or removed without changing the
  coordinator's prompt at all (only the `integrations` subagent's tool
  list changes, and it isn't visible to the coordinator).
- **Con:** every request that needs `search_codebase`, `run_tests`,
  `propose_fix`, `internet_search`, `fetch_url`, or an MCP tool pays
  one subagent round-trip. For short, cheap lookups this is the cost
  of the modularization. We accepted the trade-off because (a) the
  coordinator's tool payload was the dominant cost, and (b) the
  subagent prompts are short and return concise summaries, so the
  round-trip is bounded.
- **Con:** `integrations` is a "kitchen sink" subagent. If a future
  connector has unusual side-effect characteristics that warrant a
  tighter per-connector system prompt, this design forces an
  Option-B-style refactor. Acceptable: no such connector exists today,
  and YAGNI applies.
- **Con:** `qna_search` is the only Tavily-backed tool left on the
  coordinator. The other two Tavily tools (`internet_search`,
  `fetch_url`) moved to the `research` subagent. If a future change
  moves `qna_search` to `research` too, the coordinator's surface
  drops to 9 — the ceiling test will need to be updated consciously,
  not silently.

## Alternatives considered

1. **Shrink tool descriptions rather than the count.** Cheaper diff,
   but the symptom (provider treats large tool list as "don't call
   tools") is driven by the count and the schema size, not just the
   prose. A 30-tool compact-prompt variant is still larger than the
   fixed 10-tool surface we have now.
2. **Per-connector subagents (Option B in GOAL-0002 §3.3).** Better
   per-connector system prompts, but the maintenance cost (every
   connector add/remove rewrites the subagent catalogue) outweighs
   the benefit for our current set of MCP servers.
3. **Disable eager-tools to reduce the round-trip cost of every
   delegation.** Considered alongside the `_ForceToolChoice` fix;
   not chosen because the latency optimization is real (20–50% on
   multi-tool turns) and the bug was fixable without sacrificing it.
4. **Lazy-load tool descriptions on demand.** Would reduce the
   initial prompt cost without changing the wiring. Deferred — the
   count reduction is a bigger win and a simpler change.

## Acceptance criteria recap (from GOAL-0002 §5)

- M0: `_ForceToolChoice` fix lands (see HANDOFF.md — done in this PR
  series).
- M1: four tools removed from `create_core_tools()`. Subagent tool
  map unchanged. Verified by `test_core_tool_count_is_stable`
  (asserts 10, was 16) and the existing subagent tests.
- M2: `research` subagent added; `internet_search` and `fetch_url`
  removed. Verified by `test_subagent_count_is_stable` (asserts 11,
  was 9).
- M3: `integrations` subagent added; MCP tools not appended to the
  coordinator's binding. Verified by
  `test_coordinator_tool_count_is_capped_regardless_of_mcp`
  (parameterized: 0, 1, 5, 25 MCP tools, coordinator always 10).
- M4: this ADR.
- M5: the same `test_coordinator_tool_count_is_capped_regardless_of_mcp`
  test serves as the regression guard. It fails the build if a
  future change re-introduces MCP tools on the coordinator.
