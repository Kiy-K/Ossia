# ADR-0008: Subagent design and routing policy

**Status:** accepted.
**Date:** 2026-06-22.
**Supersedes:** none (first subagent ADR).

## Context

The Deep Agents subagent docs distinguish between "context
quarantine" (delegate to a subagent to keep the coordinator's
context clean) and "specialized behavior" (give a subagent custom
instructions or tools for a domain). They recommend:

- One subagent per specialized role, with a **specific,
  action-oriented** description so the coordinator can pick.
- **Minimal tool sets** per subagent (do not pass every tool).
- **Detailed system prompts** that include output-format
  requirements and a length cap, so subagents return concise
  summaries instead of bloated raw data.
- **Skill isolation** by default — only `general-purpose` inherits
  main-agent skills; custom subagents get their own (when
  configured).
- The `task` tool is added automatically when at least one
  synchronous subagent exists; otherwise the agent runs without
  delegation.

Prior state: the agent defined four custom subagents
(`code-researcher`, `bug-diagnostician`, `fix-proposer`,
`test-runner`) as a list of dicts matching the
`SubAgent` schema, with the `model` field set to the main
agent's `BaseChatModel`. The default `general-purpose` subagent
was implicitly added by Deep Agents.

The descriptions and system prompts were one-liners. They worked,
but the doc's best-practices section calls out that "Analyzes
financial data and generates investment insights with confidence
scores" is a better description than "Does finance stuff", and
that the subagent's `system_prompt` should pin a specific output
format and a length cap to keep the coordinator's context lean.

## Decision

Keep the four-subagent design; tighten descriptions and system
prompts to follow the docs' best-practices. The dict shape stays
the same — name, description, system_prompt, tools, model — so
the existing `create_deep_agent(subagents=...)` call is unchanged.

Each subagent now has:

- A **specific, action-oriented** description (one sentence, with
  the "Delegates here when ..." trigger spelled out so the
  coordinator's routing is unambiguous).
- A **detailed system_prompt** with:
  - A role introduction.
  - A numbered "expected workflow" (when applicable).
  - A pinned output format (sections, lengths, what to omit).
  - A word cap (200-250 words) on the response.

The default `general-purpose` subagent is left in place. It
inherits main-agent skills (none today), uses the main model, and
serves as a fallback for any question the four custom subagents
don't cover. We do not customize or disable it; doing so is a
v2 concern if the routing gets noisier.

We do **not** use `response_format` (structured output) on
subagents today. The eval harness checks response *contents*
(substring match against expected terms), not structured
fields, and structured output requires `deepagents>=0.5.3`
(we're on 0.6.11, so the requirement is met). Add later when a
consumer actually needs JSON from a subagent; today's UI/eval
paths are text.

We do **not** set per-subagent `interrupt_on`. Subagent
interrupts inherit from the main agent's config; HITL on
`send_response` is what we want, and that lives at the top level
where the API enforces it.

## Consequences

- **Pro:** subagent outputs are now bounded (200-250 words) and
  follow a fixed format, so the coordinator's context stays
  lean and parsing is cheap.
- **Pro:** descriptions read like routing rules — the model
  sees a clear "Delegates here when ..." predicate for each
  subagent, which is what the docs recommend.
- **Pro:** the system prompts separate "expected workflow" from
  "output format", making the subagent's behavior auditable
  without a live run.
- **Con:** the prompts are longer; per-`ainvoke` cost on
  subagent runs is slightly higher (negligible at 200-250 word
  caps).
- **Con:** if a future use case needs JSON from a subagent
  (e.g. for downstream tool chaining), we still need to add
  `response_format=` and a Pydantic schema. Deferred.
- **Con:** `general-purpose` is still auto-added; it inherits
  the main model and can do anything. We trust the routing
  descriptions to keep the coordinator from delegating
  gratuitously. Monitor LangSmith traces (filter on
  `lc_agent_name="general-purpose"`) if it becomes noisy.

## Alternatives considered

1. **Use a single general-purpose subagent** and drop the four
   custom ones. Simpler, but the doc's guidance and our eval
   dataset (which expects specific subagent routing per
   `expected_intent`) both assume specialized roles. The
   current 4-subagent design matches the eval's intent
   vocabulary one-to-one.
2. **Per-subagent model override** (Gemini for the
   contract-reviewer, GPT for the financial-analyst, per the
   doc's "Choose models by task"). We use the main model for
   every subagent today. Switching per-subagent models adds
   API key surface for marginal accuracy gain on a dev-concierge
   workload; defer until a subagent's quality is a measured
   problem.
3. **Add a `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)`**
   to remove the default. The default hasn't caused any
   observable problems; removing it would force the four custom
   subagents to cover every intent in the eval dataset, which
   they don't (free-form questions go to general-purpose).
   Keep the default.
