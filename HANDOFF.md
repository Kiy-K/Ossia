# Handoff: Tool-calling — RESOLVED (GOAL-0002 M0)

**Status:** Fixed. Tool-calling now works end-to-end via `_ForceToolChoice`
binding tools onto the model. See `src/core/agent.py` for the implementation
and `tests/test_graph.py::test_force_tool_choice_*` for the regression tests.

## What was wrong

`eager_tools_langgraph` (added by default via
`Settings.enable_eager_tools=true`) short-circuits the model call: its
`awrap_model_call` does `del handler` and calls
`request.model.astream(...)` directly, **never reaching
`_get_bound_model`**. With nothing binding tools, the model emitted an
HTTP request with zero tools — the "I don't have web access" symptom.

The earlier diagnosis (`AnthropicPromptCachingMiddleware` / `_ToolExclusionMiddleware`)
was wrong: those middlewares do not strip tools. The chain never reached
them. The bug was one layer up, in eager_tools.

## What the fix does

`_ForceToolChoice` (in `src/core/agent.py`) now does **two** things when
`request.tools` is non-empty:

1. `request.model = request.model.bind_tools(tools, tool_choice=...)` — so
   when eager_tools (or any other short-circuiting middleware) calls
   `request.model.astream(...)`, the underlying chat model is already
   configured for tool dispatch.
2. `request.tools = []` — so when the normal handler path *is* reached
   and `_get_bound_model` runs, it falls into its empty-tools branch
   (`factory.py:1404`) and just applies `model_settings` to the existing
   binding instead of trying to call `bind_tools` a second time on a
   `RunnableBinding` (which would `AttributeError` because
   `RunnableBinding` is not a `BaseChatModel`).

The previous version only set `request.tool_choice="auto"` on the
`ModelRequest`, expecting `_get_bound_model` to honor it. With eager_tools
short-circuiting, `_get_bound_model` was never called — and the HTTP
request went out with zero tools.

## How to verify

```bash
# The five new regression tests pin the fix:
.venv/bin/python -m pytest tests/test_graph.py -v -k force_tool_choice
# All 36 test_graph.py tests still pass.
```

The regression tests cover:
- Tools are bound onto the model when `request.tools` is non-empty
- An explicit `tool_choice` (e.g. `"any"` for structured output) is preserved
- Empty tools = pass-through (no `bind_tools` call)
- `_ForceToolChoice` is the last middleware in the stack
- Integration simulation: a downstream short-circuit sees the bound model

## Why a regression test, not a monkey-patch log

The previous "next steps" suggested monkey-patching `_get_bound_model` to
log `request.tools` at runtime. The fix-and-test path is cleaner: pin
the contract in a unit test so a future change to `_ForceToolChoice` (or
a different short-circuiting middleware) gets caught immediately.

## Related

- `GOAL-0002-tool-modularization.md` — M0 was the precondition for M1–M5
- The `request.tools = []` trick is necessary because the pre-bound
  `RunnableBinding` is not a `BaseChatModel`; see the long docstring on
  `_ForceToolChoice` in `src/core/agent.py` for the full reasoning.
