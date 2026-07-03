# Feature: Plugin system

- Status: implemented
- ADR: docs/adr/0009-tool-surface-and-tavily.md
- Scope: infrastructure, subagent, tool, middleware

## What it does

Adds a file-based, config-driven plugin system. A plugin is a Python
file or package that defines a top-level
``register(api: PluginAPI, config: dict | None = None) -> None``
function. At agent-build time, the loader:

1. Reads ``ossia.json`` (or ``ossia.jsonc``) from the project root
   (or ``$OSSIA_CONFIG``, or ``~/.config/ossia/ossia.json``).
2. Scans the bundled ``plugins/`` directory and the user
   ``$OSSIA_PLUGINS_DIR`` (default ``./plugins_local/``) for
   candidate files.
3. Imports every file that defines ``register``; honors the
   config's ``enabled`` flag, custom ``path``, and ``config`` dict
   (passed as the second arg to ``register``).
4. Merges the plugin's contributions (tools, subagent specs,
   middlewares) into the agent.

The first shipped plugin is **Ponytail** (``plugins/ponytail/``).
It registers a ``ponytail_review`` tool that the agent (or any
reviewer subagent) can call to check a diff, code snippet, or design
proposal against the Ponytail ladder — the "laziest solution that
actually works" rubric. The tool is deterministic (regex heuristics,
no LLM) and returns a structured ``PonytailReview`` with a verdict
(``ship`` / ``simplify`` / ``over_engineered``), a list of findings,
and an optional lazy alternative.

### ossia.json schema (v0.1)

```jsonc
{
  "$schema": "https://ossia.dev/schemas/ossia.json",
  "plugins": [
    "ponytail",                              // string: enable by name
    {                                        // object: full control
      "name": "my-plugin",
      "path": "./vendor/my_plugin",          // optional; default: lookup by name
      "enabled": true,                       // default true
      "config": { "api_key": "..." }         // passed to register(api, config=...)
    }
  ]
}
```

Locations (first found wins): ``$OSSIA_CONFIG`` env > ``./ossia.json``
> ``./ossia.jsonc`` > ``~/.config/ossia/ossia.json`` >
``~/.config/ossia/ossia.jsonc``. JSONC supports ``//`` line
comments, ``/* */`` block comments, and trailing commas.

## Scope table

| Concern | In scope | Out of scope |
|---|---|---|
| Discovery | Flat scan of bundled + user plugin dirs. Top-level ``.py`` files and subpackage ``__init__.py``. Optional ``ossia.json`` config. | Recursive scan, plugin dependencies, version constraints, remote config, ``{env:VAR}`` substitution. |
| Plugin contract | A ``register(api, config=None)`` function. The ``api`` exposes ``add_tool``, ``add_subagent``, ``add_middleware``, ``log``. | Lifecycle hooks (on_start, on_stop), async register, plugin-to-plugin calls. |
| Failure modes | Import error → log + skip. ``register`` raises → log + skip. Duplicate name → first wins, second logged + skipped. Bad config → log + treat as empty. | Crashing the agent on a bad plugin; quarantining broken plugins. |
| Contributions | Tools, subagent specs, middlewares. | Commands, providers, prompt fragments, agent-state migrations. |
| Re-loading | None — plugins load once at agent build. | Hot-reload, per-request plugin activation. |
| Ponytail tool | Static regex heuristics + verdict rubric. | LLM-driven review, GitHub integration, IDE hooks. |

## Endpoint impact

None — the plugin system is internal. The ``/v1/tools`` route
(when it exists) will surface plugin-contributed tools alongside the
core tools, since they are merged into the same `tools` list before
the agent compiles.

## Safety/Permissions

- The plugin loader runs with the same Python process as the agent.
  A malicious plugin has full access to the filesystem, network,
  and secrets. We do not sandbox plugins. Ponytail: explicit
  warning in the loader docstring; users install plugins at their
  own risk, just like `pip install`.
- A bad plugin (import error, broken ``register``) is logged at
  WARNING and skipped — the agent never crashes on a plugin.
- Plugin-contributed subagents inherit the **read-only** tool
  defaults; plugins can choose to pass other tools but the agent
  builder does not enforce it. Ponytail: add a per-plugin tool
  allowlist when this becomes a real concern.
- Plugin-contributed middlewares run AFTER the core stack and
  BEFORE the caller-context middleware (which must remain closest
  to the model call). Ponytail: the order is documented in
  ``_compile_agent``.

## NFRs

- **Streaming:** unaffected. Plugins contribute tools, not events.
- **Checkpointing:** unaffected. The agent state schema does not
  change.
- **HITL:** unaffected. Plugin-contributed tools follow the same
  interrupt config as core tools (``send_response`` interrupts by
  default).
- **Performance:** plugin discovery happens once at agent build;
  a few `importlib` calls and a few module evaluations. Sub-second
  for the bundled + a few user plugins.
- **Security:** see Safety/Permissions. Plugins are not sandboxed.

## Affected modules

- `src/core/plugin.py` — new. Defines `PluginAPI`, `LoadedPlugin`,
  `discover_plugins`, `load_plugins_into`, plus the discovery
  helpers (`_candidate_files`, `_load_one`, `_load_module_from_path`,
  `_resolve_user_plugins_dir`).
- `src/core/agent.py` — both `build_agent` and `build_agent_async`
  call `load_plugins_into` after the core tools / MCP tools are
  assembled. `_compile_agent` accepts a new `plugin_middlewares`
  kwarg and appends it to the middleware list before the
  caller-context middleware.
- `src/core/config.py` — new `ossia_plugins_dir` setting
  (alias `OSSIA_PLUGINS_DIR`).
- `plugins/ponytail/__init__.py` — new. The Ponytail plugin.
- `plugins/ponytail/README.md` — new. Plugin user-facing docs.
- `tests/test_plugin.py` — new. 12 tests covering the loader
  contract.
- `tests/test_ponytail.py` — new. 14 tests covering the heuristic
  verdicts and input schema.
- `.env.example` — adds the `OSSIA_PLUGINS_DIR` block.

## Testing notes

- Unit tests:
  - `tests/test_plugin.py` — loader contract, duplicate handling,
    candidate-file discovery, broken-plugin resilience.
  - `tests/test_ponytail.py` — verdict rubric, lazy-ladder
    coverage, ``# ponytail:`` exclusion, input schema.
- Manual smoke test:
  1. Add a one-line plugin to `plugins_local/inspect.py`:
     ```python
     PLUGIN_NAME = "inspect"
     def register(api):
         api.log("inspect plugin loaded")
     ```
  2. Start the server. The log line `[plugin:inspect] inspect plugin
     loaded` should appear once at boot.
- Known limitation: the plugin loader does not validate that a
  plugin's declared tool names do not collide with core tool
  names. If two plugins register a tool named `search_codebase`,
  the second silently overrides the first at the LLM's discretion.
  Add a name-collision check when this becomes a real concern.
