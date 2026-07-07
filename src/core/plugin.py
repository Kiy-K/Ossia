"""Plugin loader and the ``PluginAPI`` surface plugins use to extend the agent.

A plugin is a Python module or package that defines a top-level
``register(api: PluginAPI, config: dict | None = None) -> None``
function. The loader discovers plugins in two ways:

  1. **Filesystem scan** (always runs): the bundled ``plugins/``
     directory at the repo root + the directory at
     ``$OSSIA_PLUGINS_DIR`` (defaults to ``./plugins_local/`` —
     created on first run if missing). Top-level ``.py`` files and
     subpackage ``__init__.py`` files.
  2. **Config-driven** (when ``ossia.json`` exists): see
     ``core.plugin_config``. Config can add plugins from custom
     paths, disable specific plugins, and pass a ``config`` dict to
     ``register()``.

Discovery is a flat scan; each plugin module is imported with
``importlib`` under a unique alias. If a file defines ``register``,
the function is called with a fresh ``PluginAPI`` instance and
(when ossia.json has an entry for the plugin) the entry's ``config``
dict.

Ponytail: no helper class for the loader state, no plugin metadata
schema beyond what the API needs. The discoverer is a single
function. If we ever need plugin dependencies, version constraints,
or per-plugin permissions, add a real manifest — same surface, more
checks.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from core.plugin_config import OssiaConfig, load_ossia_config

logger = logging.getLogger(__name__)

# Repo root — the bundled ``plugins/`` dir sits next to ``src/`` and
# ``pyproject.toml``. Importing ``__file__`` here works because
# ``core`` is installed under ``src/core/plugin.py`` and the
# packages on the same level as the package's parent are
# ``src/``, ``tests/``, ``scripts/``, ``plugins/``.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_BUNDLED_PLUGINS_DIR = _REPO_ROOT / "plugins"


@dataclass
class LoadedPlugin:
    """One successfully loaded plugin, for diagnostics / listing."""

    name: str
    module: str
    path: Path
    config: dict[str, Any] = field(default_factory=dict)
    tools: list[BaseTool] = field(default_factory=list)
    subagents: list[dict[str, Any]] = field(default_factory=list)
    middlewares: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PluginAPI:
    """The handle a plugin's ``register(api)`` function receives.

    Plugins accumulate tools, subagent specs, and middlewares on the
    API. After ``register`` returns, the agent builder reads them
    back out and wires them into the graph.

    Ponytail: no event hooks / lifecycle / async / config injection.
    The four things plugins can add are the four things users
    actually want to add. If we ever need hooks, add a fifth method
    here — same shape, one new line.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._tools: list[BaseTool] = []
        self._subagents: list[dict[str, Any]] = []
        self._middlewares: list[Any] = []

    @property
    def tools(self) -> list[BaseTool]:
        return list(self._tools)

    @property
    def subagents(self) -> list[dict[str, Any]]:
        return list(self._subagents)

    @property
    def middlewares(self) -> list[Any]:
        return list(self._middlewares)

    def add_tool(self, tool: BaseTool) -> None:
        """Register a tool. The agent will see it alongside the core tools."""
        if not isinstance(tool, BaseTool):
            raise TypeError(f"plugin {self.name!r} added {tool!r} which is not a BaseTool")
        self._tools.append(tool)
        logger.info("plugin %s: registered tool %s", self.name, tool.name)

    def add_subagent(
        self,
        *,
        name: str,
        description: str,
        system_prompt: str,
        tools: Iterable[BaseTool] = (),
    ) -> None:
        """Register a subagent spec in the same shape Deep Agents expects.

        See ``core/agent.py::_build_subagents`` for the canonical
        subagent dict shape. The agent builder calls
        ``api.subagents`` after every plugin registers and folds the
        result into the master subagent list.
        """
        subagent = {
            "name": name,
            "description": description,
            "system_prompt": system_prompt,
            "tools": list(tools),
        }
        self._subagents.append(subagent)
        logger.info("plugin %s: registered subagent %s", self.name, name)

    def add_middleware(self, mw: Any) -> None:
        """Register a Deep Agents middleware. Appended in registration order."""
        self._middlewares.append(mw)
        logger.info("plugin %s: registered middleware %s", self.name, type(mw).__name__)

    def log(self, message: str) -> None:
        """Log a plugin-internal message. Tagged with the plugin name."""
        logger.info("[plugin:%s] %s", self.name, message)


def _resolve_user_plugins_dir() -> Path:
    """Return the user plugins dir (created if missing).

    Honors ``$OSSIA_PLUGINS_DIR`` and falls back to ``./plugins_local``
    next to the repo root. ``Settings.ossia_plugins_dir`` is the
    canonical source when the app is running through Pydantic; the
    env var is the override for one-off scripts and tests.
    """
    from core.config import get_settings

    override = os.environ.get("OSSIA_PLUGINS_DIR")
    if override:
        path = Path(override).expanduser().resolve()
    else:
        try:
            settings = get_settings()
            if settings.ossia_plugins_dir:
                path = Path(settings.ossia_plugins_dir).expanduser().resolve()
            else:
                path = _REPO_ROOT / "plugins_local"
        except Exception:  # noqa: BLE001
            # Settings not yet loadable (e.g. very early in tests).
            path = _REPO_ROOT / "plugins_local"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidate_files(root: Path) -> Iterable[Path]:
    """Yield ``.py`` files that look like plugin entry points.

    A file is a plugin candidate if it is a top-level ``.py`` file in
    ``root`` OR the ``__init__.py`` of an immediate subpackage. We
    skip anything starting with ``_`` (private modules) and the
    obvious infrastructure files (``conftest.py`` etc.).
    """
    if not root.is_dir():
        return
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix == ".py" and not path.name.startswith("_"):
            yield path
        elif path.is_dir() and (path / "__init__.py").is_file():
            yield path / "__init__.py"


def _load_module_from_path(path: Path, alias: str) -> Any:
    """Import a Python source file by path under ``alias``.

    Uses ``importlib.util.spec_from_file_location`` so the file does
    not need to be on ``sys.path`` and we don't pollute the global
    module namespace. Each plugin gets a stable, unique alias.
    """
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


def _call_register(register: Any, api: PluginAPI, config: dict[str, Any] | None) -> bool:
    """Call ``register(api, config=...)``, falling back to ``register(api)``
    for plugins that haven't been updated to the new signature.

    Returns True if the call succeeded. A failure is logged once and
    the plugin is considered broken (the loader skips it).
    """
    try:
        register(api, config=config)
        return True
    except TypeError as exc:
        # Old signature: register(api). Retry without config.
        if "config" in str(exc) or "argument" in str(exc):
            try:
                register(api)
                return True
            except Exception as inner:  # noqa: BLE001
                logger.warning("plugin %s register() raised: %s", api.name, inner)
                return False
        logger.warning("plugin %s register() raised: %s", api.name, exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("plugin %s register() raised: %s", api.name, exc)
        return False


def _load_one(path: Path, config: dict[str, Any] | None = None) -> LoadedPlugin | None:
    """Try to load one plugin file. Returns None when it is not a plugin."""
    alias = f"ossia_plugin_{path.stem}"
    try:
        module = _load_module_from_path(path, alias)
    except Exception as exc:  # noqa: BLE001
        # A bad plugin should not take down the whole agent. Log
        # and move on. Ponytail: load() never raises.
        logger.warning("failed to import plugin candidate %s: %s", path, exc)
        return None
    register = getattr(module, "register", None)
    if register is None:
        # Not a plugin file (no register() defined). Silently skip —
        # common to drop helpers in the same dir.
        sys.modules.pop(alias, None)
        return None
    name = getattr(module, "PLUGIN_NAME", path.stem)
    api = PluginAPI(name=name)
    if not _call_register(register, api, config):
        return None
    return LoadedPlugin(
        name=name,
        module=alias,
        path=path,
        config=config or {},
        tools=api.tools,
        subagents=api.subagents,
        middlewares=api.middlewares,
    )


def discover_plugins(
    extra_dirs: Iterable[Path] = (),
    config: OssiaConfig | None = None,
) -> list[LoadedPlugin]:
    """Discover and load every plugin in the bundled + user dirs.

    When ``ossia.json`` is present (or passed via ``config``), it is
    honored in three ways:

      1. Plugins with ``enabled: false`` are skipped.
      2. Plugins with an explicit ``path`` are loaded from that path.
      3. The ``config`` dict on an entry is passed to the plugin's
         ``register(api, config=...)`` call.

    Filesystem scanning still runs alongside the config (so bundled
    and ``$OSSIA_PLUGINS_DIR`` plugins keep loading). A name that
    appears in both is loaded once — the config entry wins (config
    dict applied, path used if given, otherwise the file is
    discovered by stem and the config dict is layered on top).

    Order: config-declared-explicit-path plugins first, then
    filesystem scan (bundled → user → extra_dirs, alphabetical
    within each). The first plugin wins on name collisions — the
    second is skipped with a warning.
    """
    cfg = config if config is not None else load_ossia_config()
    cfg_by_stem: dict[str, dict[str, Any]] = {
        pc.name: pc.config for pc in cfg.plugins if pc.enabled and pc.config
    }
    disabled: set[str] = {pc.name for pc in cfg.plugins if not pc.enabled}
    cfg_explicit: list[tuple[Path, dict[str, Any]]] = [
        (pc.path, pc.config)
        for pc in cfg.plugins
        if pc.enabled and pc.path is not None
    ]

    seen_names: set[str] = set()
    loaded: list[LoadedPlugin] = []

    # 1. Config-declared explicit paths load first.
    for path, cfg_dict in cfg_explicit:
        plugin = _load_one(path, config=cfg_dict or None)
        if plugin is None:
            continue
        if plugin.name in disabled:
            continue
        if plugin.name in seen_names:
            logger.warning(
                "duplicate plugin name %r from %s; skipping (first wins)",
                plugin.name,
                path,
            )
            continue
        seen_names.add(plugin.name)
        loaded.append(plugin)

    # 2. Filesystem scan (bundled, user, then extra_dirs).
    for root in [_BUNDLED_PLUGINS_DIR, _resolve_user_plugins_dir(), *extra_dirs]:
        for path in _candidate_files(root):
            # Stem-based config overlay: a config entry whose name
            # matches the file stem has its config dict passed in.
            stem_config = cfg_by_stem.get(path.stem)
            plugin = _load_one(path, config=stem_config)
            if plugin is None:
                continue
            if plugin.name in disabled:
                continue
            if plugin.name in seen_names:
                logger.warning(
                    "duplicate plugin name %r from %s; skipping (first wins)",
                    plugin.name,
                    path,
                )
                continue
            seen_names.add(plugin.name)
            loaded.append(plugin)

    return loaded


def load_plugins_into(
    *,
    tools: list[BaseTool],
    subagents: list[dict[str, Any]],
    middlewares: list[Any],
    extra_dirs: Iterable[Path] = (),
    config: OssiaConfig | None = None,
) -> list[LoadedPlugin]:
    """Discover plugins and merge their contributions into the agent lists.

    Mutates and returns the lists so the caller can wire them into
    ``create_deep_agent``. Returns the loaded plugins for diagnostics
    (the ``/v1/plugins`` route surfaces this). When ``config`` is
    None, ``load_ossia_config()`` is called to honor any
    ``ossia.json`` present on disk.
    """
    plugins = discover_plugins(extra_dirs=extra_dirs, config=config)
    for plugin in plugins:
        tools.extend(plugin.tools)
        subagents.extend(plugin.subagents)
        middlewares.extend(plugin.middlewares)
    return plugins
