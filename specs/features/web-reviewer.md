# Feature: Web reviewer (browser-use subagent)

- Status: implemented
- ADR: docs/adr/0009-tool-surface-and-tavily.md
- Scope: subagent, tool

## What it does

Adds a sync `web-reviewer` subagent that drives a real Chromium browser
via the [browser-use](https://browser-use.com) SDK. The subagent
exposes a single `browser_use_task` tool that takes a natural-language
task description, an optional `max_steps` cap, an optional `flash_mode`
toggle, and an optional `output_schema` dict for structured extraction.
The tool runs a browser-use `Agent` to completion and returns the
extracted content, the structured fields when a schema was supplied,
and the list of URLs visited.

Two browser modes:
  * **Cloud** (`Settings.browser_use_local=False`, default): the
    browser-use cloud browser. Free to launch; the LLM gateway and
    anti-bot bypass are paid-only.
  * **Local** (`Settings.browser_use_local=True`): a local Chromium
    you install once via `scripts/install_browser.py`. Free, no API
    key needed for the browser. The LLM is the main provider's model
    (routed through your `OPENROUTER_API_KEY` etc.). Recommended for
    sites the free-tier cloud browser can't reach.

The agent builder wires the subagent only when the `browser-use`
package is installed AND (`BROWSER_USE_API_KEY` is set OR
`browser_use_local=True`). Otherwise the subagent is silently
skipped — no startup failure, no degraded state in the rest of the
agent.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Sync subagent | New `web-reviewer` entry in `_DEV_CONCIERGE_SUBAGENTS` with read-only + `browser_use_task` tool. | Async subagent wiring. A separate async subagent would require a LangGraph Cloud deployment. |
| Tool | `src/core/browser_use_tool.py` exposes `@tool` `browser_use_task` with structured Pydantic input/output and an invocation counter. Tool args: `task`, `max_steps`, `flash_mode`, `output_schema`. | Other browser automation SDKs (Playwright, Stagehand). Browser-use is the chosen one. |
| Browser | Two modes via `Settings.browser_use_local` (default `False`):<br>• `False` → `Browser(use_cloud=True, cloud_timeout=15, viewport=1280x800)` — cloud browser. Free to launch, but anti-bot bypass is paid-tier only.<br>• `True` → `Browser(use_cloud=False, headless=True, chromium_sandbox=settings.browser_use_chromium_sandbox, args=_LOCAL_STEALTH_ARGS, ...)` — local Chromium with stealth flags. Free, no API key needed for the browser. | Other stealth approaches (undetected-chromedriver, playwright-extra). The browser-use SDK does its own fingerprint handling; we add a conservative arg list. |
| LLM | `ChatBrowserUse()` — the model the SDK recommends for browser automation. | Other LLM providers for the browser task itself. The supervisor's main model is unaffected. |
| Free-tier optimization | `flash_mode=True` default — skips the agent's internal thinking/evaluation/next-goal fields. One LLM call per step instead of two. Opt-out via the `flash_mode` arg. | Persistent cost guardrails across restarts. The user explicitly chose "counter + log only". |
| Structured output | Optional `output_schema: dict[str, str]` arg. The tool builds a Pydantic model dynamically via `pydantic.create_model` and passes it to `Agent(output_model_schema=...)`. Result lands in the tool's `extracted` field. | Typed (non-string) fields. All schema fields are currently `str` — the LLM stringifies numbers/booleans. |
| Config | `Settings.browser_use_api_key` read from `BROWSER_USE_API_KEY` (preferred) or `OSSIA_BROWSER_USE_API_KEY`. | Persisting usage to Redis/Postgres. The user explicitly chose "counter + log only". |
| Telemetry | `ANONYMIZED_TELEMETRY` is set to `"false"` at runtime (via `os.environ.setdefault`). | Sending our own telemetry for browser-use calls. |
| Cost guard | Per-process monotonic counter; WARNING log on every call with the running total. | Hard cap, persistence across restarts, or per-caller quotas. |

## Endpoint impact

None — this feature does not modify the HTTP contract. The tool is
exposed internally to the subagent; clients do not call it directly.

## Safety/Permissions

- Subagent tool allowlist: `_READ_ONLY_TOOLS` (search_codebase,
  search_knowledge_base) + `browser_use_task`. No file writes, no PR
  creation, no `create_pr`, no `run_tests`. The subagent is strictly
  read-only on the codebase and write-only into the live web (where
  the side-effects are the user's concern, not ours).
- `interrupt_on`: not affected. `browser_use_task` is not on the
  interrupt allowlist.
- Filesystem rules: not affected. The subagent has no write access.
- Security boundaries: the tool is gated by `BROWSER_USE_API_KEY`
  presence. Without the key the tool refuses with a clear error and
  the subagent is not even built. The `browser-use` package is
  imported lazily so the agent starts cleanly on systems that have
  not opted in.
- API key is never logged. The invocation log line truncates the task
  to 120 characters and shows the count, not the key.

## NFRs

- **Streaming:** not affected. Tool output is a structured Pydantic
  model that the subagent synthesizes into prose.
- **Checkpointing:** not affected. The tool is stateless.
- **HITL:** not affected. The subagent cannot call `send_response`.
- **Performance:** each call costs one free-tier task. The subagent
  is told to keep `max_steps` low (default 15) and use `flash_mode=True`
  (default). No parallelism — the browser-use SDK is sequential per
  run. The invocation counter adds a single thread-safe integer
  increment per call. Cloud browser adds network latency to the LLM
  loop (~hundreds of ms per step) but no local Chromium cold start.
- **Cost:** bounded by the user's free tier. `flash_mode=True` cuts
  per-step LLM calls in half. The counter is in-process and resets on
  restart; we do not track it across processes.

## Affected modules

- `src/core/browser_use_tool.py` — new. Defines `BrowserUseTaskInput`,
  `BrowserUseTaskOutput`, `_InvocationCounter`, `_check_prerequisites`,
  `_run_browser_use_task`, `browser_use_task` (the tool), and
  `get_browser_use_tool` (the agent-builder hook).
- `src/core/agent.py` — adds the 8th sync subagent `web-reviewer` to
  `_DEV_CONCIERGE_SUBAGENTS`. `_build_subagents` filters the
  web-reviewer out when the tool is not usable.
- `src/core/config.py` — adds `browser_use_api_key` setting.
- `pyproject.toml` — adds optional `browseruse` extra
  (`browser-use>=0.11.0`).
- `.env.example` — adds the `BROWSER_USE_API_KEY` block with install
  instructions.
- `tests/test_browser_use_tool.py` — new. 13 tests covering the
  invocation counter, prereq gate (missing key, missing package),
  error-result surface, module reload, ImportError handling, input
  schema bounds, the agent-builder hook, the cloud-browser constructor
  kwargs, the Agent kwargs plumbed by `_run_browser_use_task`
  (flash_mode + output_model_schema), and the structured-output
  surface path.
- `tests/test_subagent_descriptions.py` — bumps the subagent count
  from 10 (7 sync + 3 async) to 11 (8 sync + 3 async) and updates
  the docstring.

## Testing notes

- Unit tests: `.venv/bin/python -m pytest tests/test_browser_use_tool.py -v`
- Subagent description conformance:
  `.venv/bin/python -m pytest tests/test_subagent_descriptions.py -v`
- Manual smoke test (local mode, free):
  1. `uv pip install -e ".[browseruse]"`
  2. `.venv/bin/python scripts/install_browser.py` (one-time, ~200MB)
  3. Set `BROWSER_USE_LOCAL=true` in `.env` (and `BROWSER_USE_CHROMIUM_SANDBOX=false` if running as root / in Docker)
  4. Start the server and ask the agent to "verify https://example.com is up". The web-reviewer subagent should be invoked and the WARNING log should show `browser_use_task invocation #1`.
- Manual smoke test (cloud mode): set `BROWSER_USE_API_KEY` and use the same steps without `BROWSER_USE_LOCAL`.
- Known limitation: the unit tests stub the `browser-use` package
  via `sys.modules` to avoid requiring a real browser/network. The
  real run path is exercised only by manual smoke tests. This is
  intentional — running a browser in CI would burn the free tier
  and is slow.
