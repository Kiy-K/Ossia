# ADR 0011: QuickJS Code Interpreter

**Date:** 2026-06-26

## Decision

Add a QuickJS JavaScript interpreter (via `langchain-quickjs`) as a Deep Agents
middleware that exposes an `eval` tool for sandboxed code execution. The
interpreter allows the model to compose multiple read-only tool calls within
a single turn instead of round-tripping through the LLM for each invocation.

## Context

The Ossia agent frequently needs to search code, read files, and recall thread
history in sequence. Without an interpreter, each of these operations is a
separate tool call — each inflating model context and adding latency. A
sandboxed JavaScript interpreter lets the model write short scripts that chain
read-only tools together programmatically, collapsing multiple round trips into
one.

## Decision Drivers

- **Context efficiency:** Composite read-only operations (search → read →
  recall) should not require N separate LLM rounds.
- **Safety:** The interpreter must be isolated from the host system — no
  filesystem access, no network, no native modules.
- **Simplicity:** QuickJS (via `langchain-quickjs`) is a well-maintained,
  sandboxed JS runtime with a LangChain tool adapter. No container or VM
  overhead.

## Alternatives Considered

- **Python via Pyodide/Pyodide-kernel:** Heavier, slower to start, and the
  model is less reliable at writing Python than JavaScript (which the model
  has seen extensively in training).
- **Docker sandbox:** Operator overhead, startup latency, and no LangChain
  native middleware adapter.
- **No interpreter (status quo):** Accept the N-tool-call overhead. Rejected
  because the read-only composite pattern is common enough to justify a
  middleware.

## Consequences

### Positive

- Model can search + read + recall in one `eval` call.
- Interpreter state snapshots persist across turns (`mode="thread"`).
- PTC calls surface as normal `tool_call` SSE events — no wire contract change.

### Negative

- PTC calls bypass `interrupt_on` (upstream Deep Agents behavior). Only
  read-only tools are in the allowlist, so no destructive operations are
  exposed to bypass.
- The `task` tool is excluded from PTC because it mutates subagent state.
  The `task()` global is available via `subagents=True` kwarg instead.

### Neutral / Future

- If a tool needs write capability inside the interpreter, it must be added
  to the PTC allowlist after a safety review. The feature spec at
  `specs/features/code-interpreter.md` documents the current allowlist and
  the rationale for each entry.
- `inspect.signature` is used to detect the middleware's `__init__` kwarg
  name (`snapshot_between_turns` vs `mode`) to guard against upstream
  signature changes.

## Status

Accepted. Implemented in v1.8.0.

## References

- Feature spec: `specs/features/code-interpreter.md`
- `src/core/agent.py` — `_build_middlewares()` wiring
- `pyproject.toml` — `langchain-quickjs` dependency
