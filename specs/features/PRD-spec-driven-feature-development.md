# Feature: Spec-Driven Feature Development

- Status: draft
- ADR: docs/adr/0005-spec-driven-openapi-as-contract.md
- Scope: infrastructure

## What it does

Establishes a formal feature spec system under `specs/features/` with a template, validation tests, coverage matrix, and changelog auto-generation to document new features alongside the existing OpenAPI contract.

## Problem Statement

(Background context from the original PRD.)

Ossia's existing spec-driven workflow (`specs/openapi.checked.json` + drift test) catches API contract regressions, but it doesn't help developers document new features, reason about the interpreter middleware's safety boundaries, or compose multi-tool pipelines efficiently. Three gaps exist:

1. **New features lack a spec template.** When someone adds a route, middleware, or tool, there's no standard place to write down what it does, what NFRs it carries, and which ADRs cover the decisions. The `changelog.md` is a chronological log, not a feature-centric one.

2. **The code interpreter's PTC allowlist is implicit.** The middleware is wired, but the safety rationale lives only in code comments and the AGENTS.md gotcha list. A new contributor adding a tool to PTC has no document to consult about why `task` is excluded or why the allowlist is read-only.

3. **Multi-tool workflows waste context.** Without the interpreter, an agent that needs to search code, read a file, and recall thread history emits three separate tool calls — each inflating model context. The interpreter collapses this into one `eval` call, but the pattern is not documented as a deliberate design choice.

## Solution

A **feature-spec template** under `specs/features/` (one per feature) that mirrors the structure of the existing `specs/SPEC.md` but is scoped to a single feature. Each feature spec includes a scope table, NFRs, an endpoint impact summary, and an explicit safety rationale (for interpreter/tooling changes). Supporting scripts validate the specs, auto-generate changelog entries, and track endpoint coverage.

The existing `CodeInterpreterMiddleware` integration is formalized as the first feature spec (`specs/features/code-interpreter.md`), documenting the PTC allowlist rationale, streaming visibility, and persistence guarantees that are currently spread across `agent.py`, `AGENTS.md`, and `changelog.md`.

## User Stories

1. As a **developer adding a new tool**, I want a spec template that prompts me to document what routes are affected and what safety boundaries exist, so I don't forget the drift test, the changelog entry, or the NFRs.

2. As a **reviewer of a PR that touches the code interpreter**, I want to see a feature spec that explains the PTC allowlist choices, so I can verify that no destructive tools leaked into the allowlist without reading Python source.

3. As a **new contributor reading the repo**, I want a `specs/features/` directory that lists every feature with a one-page summary, so I understand what exists without piecing together AGENTS.md, ADRs, and changelog entries.

4. As a **CI pipeline**, I want a feature-spec validator that fails if a spec references an endpoint that doesn't exist in the pinned OpenAPI contract, so stale spec docs are caught early.

5. As a **release manager**, I want a script that diffs feature specs against the last release and generates a draft changelog entry, so writing release notes is a review step, not a research step.

6. As a **developer debugging a production issue**, I want a coverage matrix that shows which features exercise which endpoints, so I know where to look when a route behaves unexpectedly.

7. As a **developer wiring the code interpreter**, I want the PTC allowlist rationale documented in a feature spec that references ADR-0011, so the next person who asks "why isn't `task` in PTC?" finds the answer in the spec, not in a Slack thread.

8. As a **developer adding a new subagent role**, I want the feature spec template to prompt me to list the affected tools, routes, and ADRs, so the subagent's impact is documented before implementation.

## Implementation Decisions

### 1. Feature spec template location and format

Feature specs live at `specs/features/<slug>.md`. Each spec follows a fixed structure:

```
# Feature: <Title>
- Status: draft | accepted | implemented
- ADR: docs/adr/0005-spec-driven-openapi-as-contract.md
- Scope: tool | middleware | route | memory | subagent | infrastructure

## What it does
[One paragraph]

## Scope table
| Concern | In scope | Out of scope |
|---|---|---|

## Endpoint impact
| Method | Path | Change |
|---|---|---|

## Safety/Permissions
[PTC allowlist, interrupt_on impact, filesystem rules affected]

## NFRs
- Streaming: [affected? how?]
- Checkpointing: [affected? how?]
- HITL: [affected? how?]

## Affected modules
[List of Python modules touched]

## Testing notes
[What kinds of tests apply]
```

This mirrors the structure of `specs/SPEC.md` (scope table, NFRs, endpoint contracts) but is per-feature. The "Safety/Permissions" section is mandatory for any feature that touches the code interpreter or the PTC allowlist.

### 2. Code Interpreter feature spec as the first entry

The existing `CodeInterpreterMiddleware` integration is documented as `specs/features/code-interpreter.md`. This spec captures the PTC allowlist rationale that currently lives in code comments:

- **PTC allowlist:** `search_codebase`, `read_file`, `recall_thread_turns` — all read-only. The `task` tool is excluded because it mutates subagent state; the `task()` global is available in the interpreter via the `subagents=True` kwarg, which is the correct path for programmatic subagents.
- **Safety bounds:** 5-second timeout, 32 max PTC calls per turn, no write-capable tools in PTC.
- **Persistence:** `mode="thread"` ensures interpreter state snapshots survive conversation turns.
- **Streaming:** interpreter `eval` calls surface as `tool_call` SSE events in `/v1/chat/stream` via the existing `relay_tool_calls()` projection — no wire contract change.
- **HITL:** PTC calls bypass `interrupt_on` (documented upstream behavior); `send_response` still fires the interrupt.

### 3. Spec validation

A new test `tests/test_feature_specs.py` mirrors the existing `test_openapi_drift.py` pattern:

- Enumerates all feature specs in `specs/features/`.
- Checks that required sections are present.
- Validates that endpoint references in `## Endpoint impact` match actual routes in the pinned OpenAPI spec.
- Validates that ADR cross-references resolve to files in `docs/adr/`.
- Fails with a clear message and a one-line fix command (same UX as the drift test).

### 4. Coverage matrix

A standalone script `scripts/coverage_matrix.py` runs without a server:

- Reads `specs/openapi.checked.json` for the current route set.
- Reads all feature specs for their endpoint impact tables.
- Produces `specs/coverage.md`: a table with rows = routes, columns = feature specs, cells = `[covered]` or `[uncovered]`).
- The final column counts how many features touch each route. Routes with 0 are flagged.

### 5. Changelog auto-generation

A script `scripts/generate_changelog_entry.py` that:

- Diffs `specs/features/` against the last release tag (by comparing feature spec `Status: implemented` dates).
- For each new or changed feature spec, extracts the "What it does" paragraph and the endpoint impact table.
- Generates a draft changelog entry following the existing `specs/changelog.md` format.
- Outputs to stdout for human review before committing.

### 6. No API contract changes

None of the above modifies routes, schemas, or the OpenAPI contract. The `test_openapi_drift.py` test must continue to pass. The feature specs are documentation artifacts, not runtime code.

## Testing Decisions

### What makes a good test

- Test the validator's logic, not the content of individual feature specs.
- Use the same fixture pattern as `test_openapi_drift.py`: pin a known-good spec, regenerate, diff.
- Tests must not require a running server or a live LLM.

### Modules tested

- `tests/test_feature_specs.py` — validates spec correctness (required sections, endpoint references, ADR cross-references).
- Existing tests (`test_api.py`, `test_graph.py`, etc.) continue to cover runtime behavior.

### Prior art

- `tests/test_openapi_drift.py` — identical pattern (generate expected, diff against pinned).
- `tests/test_api.py` — same FastAPI `TestClient` fixture pattern.
- `tests/test_graph.py` — same `_FakeToolModel` pattern for any agent-internal tests.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Feature spec template | `specs/features/TEMPLATE.md` with required sections | Auto-enforcing spec writing before code commits |
| Spec validation test | `tests/test_feature_specs.py` checks sections, endpoints, ADRs | Runtime behavior validation (covered by existing tests) |
| Coverage matrix | `scripts/coverage_matrix.py` produces `specs/coverage.md` | A web UI for browsing coverage |
| Changelog generation | `scripts/generate_changelog_entry.py` auto-drafts entries | Client SDK generation from specs |

## Endpoint impact

None — this feature does not modify the HTTP contract.

## NFRs

- **Streaming:** Not affected.
- **Checkpointing:** Not affected.
- **HITL:** Not affected.
- **Performance:** The scripts and tests are offline artifacts; no runtime impact.

## Affected modules

- `specs/features/TEMPLATE.md` — feature spec template
- `specs/features/code-interpreter.md` — updated to conform to template
- `specs/features/async-subagents.md` — updated to conform to template
- `scripts/coverage_matrix.py` — coverage matrix generator
- `scripts/generate_changelog_entry.py` — changelog entry generator
- `tests/test_feature_specs.py` — spec validation test

## Testing notes

- `tests/test_feature_specs.py` validates required sections, frontmatter fields, endpoint references, and ADR cross-references.
- The coverage matrix can be verified by running `scripts/coverage_matrix.py` and checking `specs/coverage.md`.
- The changelog generator can be verified via `--dry-run`.

## Out of Scope

- **Client SDK generation** (Speakeasy, Fern, NSwag). Not related to feature specs.
- **A web UI for browsing feature specs.** The specs are markdown; GitHub renders them natively.
- **Enforcing spec-writing before code.** The validator catches missing specs post-hoc; we don't gate commits on spec existence.
- **Automated issue creation from feature specs.** The `scripts/link_feature_to_issues.py` idea from the initial proposal is deferred; it's a nice-to-have that requires GitHub API auth.
- **Modifying the existing ADR format or numbering.** Feature specs reference ADRs but don't replace them.

## Further Notes

- The `specs/features/code-interpreter.md` is populated from the existing `changelog.md` v1.8.0 entry, `AGENTS.md` §Code interpreter, and `agent.py:_build_middlewares` comments. No new research needed — this is consolidation.
- The spec template's "Safety/Permissions" section is the key innovation. It forces the author to answer the question "what tools are exposed to the interpreter and why?" — the same question that would otherwise go unasked and cause a security review finding later.
- Feature specs do not replace ADRs. ADRs are decision records (why we chose X over Y). Feature specs are capability documents (what X does, what routes it touches, what NFRs it carries). They complement each other.
