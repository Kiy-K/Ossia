# Feature: <Title>

- Status: draft | accepted | implemented
- ADR: docs/adr/NNNN-slug.md
- Scope: tool | middleware | route | memory | subagent | infrastructure

## What it does

[One paragraph describing the feature, its purpose, and its high-level behavior.]

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| <Concern 1> | <What is covered> | <What is explicitly excluded> |
| <Concern 2> | <What is covered> | <What is explicitly excluded> |

## Endpoint impact

| Method | Path | Change |
|---|---|---|
| <VERB> | `<path>` | <What changed — new, modified, removed> |

If no API endpoints are affected, state "None — this feature does not modify the HTTP contract."

## Safety/Permissions

[For tool/middleware features:]
- PTC allowlist additions or exclusions.
- `interrupt_on` impact.
- Filesystem rules affected.
- Security boundaries and rationale.

[For other features:]
- State isolation model.
- Cross-caller scoping rules.

## NFRs

- **Streaming:** [affected? how? Appear in which SSE event kind?]
- **Checkpointing:** [affected? how? Persisted in which state channel?]
- **HITL:** [affected? how? New interrupt points or bypasses?]
- **Performance:** [latency implications, parallelism, caching]

## Affected modules

[List of Python modules or files touched. Use relative paths.]

- `src/core/<module>.py` — <what changed>
- `<other files>` — <what changed>

## Testing notes

[What kinds of tests apply and how to run them.]

- Unit tests in `tests/<test_file>.py`.
- Integration tests in `tests/<test_file>.py`.
- Manual verification steps, if any.
- Known limitations in test coverage.
