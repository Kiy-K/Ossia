# Workflows

This page collects the main development workflows for Ossia: installation, local run loops, tests, linting, evaluation, plugin discovery, and API contract maintenance. It is especially important when changing agent behavior because several recent updates moved capability behind subagents and added prompt-level expectations that need regression tests.

## Local development

The repo is uv-managed on the Python side and keeps the backend entry points in `src/core/`. The top-level `AGENTS.md` recommends using `make` for most commands.

Common commands from the repo guidance:

- `make install` — create the venv and install dependencies
- `make env` — copy `.env.example` to `.env`
- `make dev` — start the backend with reload
- `make test` — run the backend test suite
- `make format` — format and lint with Ruff
- `make typecheck` — run MyPy and Pyright
- `make docker-up` — start the full Docker stack
- `make monitor-up` — start the monitoring stack

The direct backend command from `AGENTS.md` is:

```bash
OSSIA_API_KEY=dev .venv/bin/python -m uvicorn core.api:app --host 127.0.0.1 --port 8000
```

## CLI workflow

`src/core/cli.py` provides the unified `ossia` launcher. It can:

- start backend + TUI together by default
- start only the backend with `ossia server`
- start only the TUI with `ossia tui`
- run diagnostics with `ossia doctor`
- list loaded plugins with `ossia plugins list`

The CLI is intentionally diagnostic rather than a general-purpose shell.

## Tests and quality checks

The repo has separate layers of checks:

- Python tests in `tests/`
- TUI tests under `src/tui/tests/`
- Web UI Playwright tests under `src/webui/tests/`
- Linting with Ruff
- Static typing with MyPy and Pyright
- OpenAPI drift checks against `specs/openapi.checked.json`

The repo guidance also names focused commands for API health, audit, eval, and spec updates. Run the relevant layer instead of only the broad suite when you touch a single subsystem.

### Regression tests that pin recent behavior

These tests guard the GOAL-0002 changes and should be run when touching the agent runtime:

- `tests/test_graph.py::test_force_tool_choice_*` (5 tests) — verify `_ForceToolChoice` binds tools onto the model, preserves an explicit `tool_choice`, is a pass-through when tools are empty, is the last middleware in the stack, and survives the `eager_tools` short-circuit.
- `tests/test_mcp_tools.py::test_coordinator_tool_count_is_capped_regardless_of_mcp` — verifies the coordinator always binds exactly 10 tools whether 0, 1, 5, or 25 MCP tools are configured.
- `tests/test_memory.py` — large expansion covering memory seeding/store-key behavior.
- `tests/test_subagent_descriptions.py` / `tests/test_tool_descriptions.py` — subagent/tool description drift checks that moved with the new `research`/`integrations` subagents.

## Spec and contract maintenance

The repository is explicitly spec-driven:

- `specs/openapi.checked.json` is the pinned contract of record.
- `scripts/update_openapi_spec.py` regenerates the snapshot after intentional API changes.
- `tests/test_openapi_drift.py` fails when the implementation and pinned spec diverge.
- `scripts/coverage_matrix.py` generates the route/feature coverage summary.

If you change a route in `src/core/api.py`, update the spec and re-run the contract checks. Note that the `/agui` route is registered at app lifespan, so `scripts/update_openapi_spec.py` regenerates the snapshot inside a `TestClient` context; the script will not capture `/agui` (or other lifespan-time routes) without that context.

## Audit, eval, and plugin workflows

The repo includes two higher-level operational scripts called out in `AGENTS.md`:

- `scripts/audit_ossia.py`
- `scripts/eval_ossia.py`

The backend also discovers plugins from `plugins/`, `plugins_local/`, and optional `ossia.json` config. If a change affects tools or subagents, check whether plugin registration is the right place to add it.

## When changing workflow code

Use `AGENTS.md` as the command reference, then inspect the corresponding source path:

- `src/core/cli.py` for launch behavior
- `src/core/plugin.py` and `src/core/plugin_config.py` for plugin discovery and config
- `scripts/` for maintenance utilities
- `tests/` for regression coverage
- `specs/` for API contract behavior

Keep the commands in sync with what the source actually supports.
