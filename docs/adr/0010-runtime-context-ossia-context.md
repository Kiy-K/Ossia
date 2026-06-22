# ADR-0010: Runtime context propagation (OssiaContext)

**Status:** accepted.
**Date:** 2026-06-22.
**Supersedes:** the implicit "context comes from env vars" path for
per-invoke identity.

## Context

The Deep Agents "Context engineering" doc lists five context types;
ADR-0007 and ADR-0008 already cover input context (system prompt,
memory, skills), context compression (built in), subagent isolation,
and long-term memory. The fifth — **runtime context** — was missing.

Runtime context is per-invoke configuration that is **not** automatically
included in the model prompt. The model only sees it if a tool,
middleware, or other logic reads it and adds it to messages or the
system prompt. Runtime context **propagates to all subagents**.

The doc's canonical pattern: define a `dataclass` or `TypedDict`,
pass it as ``context_schema=...`` to ``create_deep_agent``, then read
it inside tools via the injected ``ToolRuntime``.

Prior state: the FastAPI layer computed a `caller` hash from the
X-API-Key and threaded it into the thread_id and a few query
parameters. Tools had no way to learn who is calling. The
"caller" value was implicitly everywhere and nowhere — embedded
in the thread_id but invisible to any tool that wanted to log
the caller or branch on it.

## Decision

Add a runtime context dataclass :class:`ossia.context.OssiaContext`
with three fields:

- ``caller: str`` — short hash of the X-API-Key. Required.
- ``request_id: str | None`` — UUID for tracing. Optional.
- ``provider: str`` — model provider; defaults to ``"openrouter"``.

Wire it into the agent via ``context_schema=OssiaContext`` on
``create_deep_agent``. The FastAPI layer constructs an
``OssiaContext`` from the validated API key and the request id
(already in ``request.state``), then passes it as ``context=`` to
both ``agent.ainvoke`` (the ``/v1/chat`` handler) and
``agent.astream_events`` (the ``/v1/chat/stream`` handler).

Demo: ``grade_response`` reads ``runtime.context.caller`` from the
injected ``ToolRuntime`` and logs it. The function is still callable
without a runtime (the parameter defaults to ``None`` and is not
in the Pydantic schema) so existing tests and one-off scripts work
unchanged.

## Consequences

- **Pro:** tools can now read the caller's identity from
  ``runtime.context`` without scraping thread ids. This unlocks
  per-caller logging, per-caller rate limiting, and per-caller
  feature flags in future passes.
- **Pro:** the runtime context propagates to all subagents
  automatically — the doc's guarantee. A subagent running a
  delegated task can read the parent's caller without any
  plumbing.
- **Pro:** the dataclass is frozen, so context is immutable for
  the lifetime of a run.
- **Con:** the ``runtime`` parameter in tools is typed as ``Any``
  so the function is callable without the runtime (e.g. from tests
  or one-off scripts). The trade-off: tools that want to branch
  on context must guard ``if runtime is not None: ...``.
- **Con:** no middleware yet reads ``OssiaContext`` to inject
  caller-specific guidance into the system prompt. That is a
  future enhancement (per the doc's `@dynamic_prompt` example);
  for v1 the context is available to tools but not surfaced in
  the prompt itself.
- **Con:** ``request_id`` is set when the FastAPI layer generates
  one (every request gets a UUID if the client did not provide
  one). For non-FastAPI callers (CLI scripts, tests) it is
  ``None`` and tools should handle that gracefully.

## Alternatives considered

1. **Pass caller via env vars** (the prior state). Tools that
   wanted the caller would read it from ``os.environ``. Rejected:
   the doc is explicit that runtime context is the right primitive
   for this; env vars are cross-process, cross-invocation, and do
   not propagate to subagents.
2. **Pass caller via the `config["configurable"]` dict** (per the
   langgraph runtime-context pattern). This is what the FastAPI
   layer does today for thread_id scoping. Rejected for the same
   reason: it would not be visible to tools via ``ToolRuntime``;
   tools would still have to read ``config["configurable"]``
   which is an anti-pattern.
3. **Add a middleware that injects caller into the system
   prompt** (per the doc's `@dynamic_prompt` example). Deferred;
   requires a dynamic-prompt helper that we don't yet have. The
   first step is the runtime context plumbing (this ADR); a
   follow-up ADR can layer the dynamic prompt on top.
4. **Use a `TypedDict` instead of a `dataclass`** (per the doc
   example uses a dataclass; the langgraph runtime context docs
   also show `TypedDict`). Keep the dataclass for the immutability
   guarantee (`frozen=True`); if a TypedDict is needed later for
   JSON-roundtripping, the dataclass can subclass one.
