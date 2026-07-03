"""ossia.json loader — config-driven plugin organization.

Mirrors opencode.json's pattern. The file is optional: when missing,
the plugin loader falls back to the filesystem scan defined in
``core.plugin``.

Schema (v0.1)::

    {
        "$schema": "https://ossia.dev/schemas/ossia.json",
        "plugins": [
            "ponytail",                                  # string form: enable by name
            {                                            # object form: full control
                "name": "my-plugin",
                "path": "./vendor/my_plugin",            # optional; default = lookup by name
                "enabled": true,                         # default true; false to skip
                "config": {"api_key": "..."}             # passed to register(api, config=...)
            }
        ]
    }

Locations (first found wins):
  1. ``$OSSIA_CONFIG`` env var (full path to a file)
  2. ``./ossia.json`` in cwd
  3. ``./ossia.jsonc`` in cwd (JSON with comments — also supported
     for the env-override file)
  4. ``~/.config/ossia/ossia.json``
  5. ``~/.config/ossia/ossia.jsonc``

JSONC support: ``//`` line comments, ``/* */`` block comments, and
trailing commas are stripped before ``json.loads``. Ponytail: no
``$ref`` resolution, no ``{env:VAR}`` substitution, no
``.well-known`` remote config. Add when someone needs it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_FILE_NAMES = ("ossia.json", "ossia.jsonc")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"(?m)^\s*//.*$")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


@dataclass(frozen=True)
class PluginConfig:
    """One entry from the ``plugins`` array in ossia.json.

    ``path`` is the explicit filesystem location to load from. When
    ``None``, the loader looks up the plugin by ``name`` in the
    bundled and user plugin dirs (matching the file stem).
    ``config`` is passed as the second argument to
    ``register(api, config=...)``.
    """

    name: str
    path: Path | None = None
    enabled: bool = True
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OssiaConfig:
    """The parsed ossia.json document. ``source`` is the file path
    the config was loaded from, for diagnostics."""

    plugins: list[PluginConfig] = field(default_factory=list)
    source: Path | None = None


def _strip_jsonc(text: str) -> str:
    """Strip C-style comments and trailing commas. Ponytail: not a
    full JSONC parser — just enough for hand-written config files."""
    text = _COMMENT_BLOCK.sub("", text)
    text = _COMMENT_LINE.sub("", text)
    text = _TRAILING_COMMA.sub(r"\1", text)
    return text


def _parse(text: str, source: Path) -> OssiaConfig:
    """Parse ossia.json text. On error, return an empty config and log."""
    try:
        data = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError as exc:
        logger.warning("ossia.json at %s is not valid JSON: %s", source, exc)
        return OssiaConfig(source=source)
    if not isinstance(data, dict):
        logger.warning("ossia.json at %s: top level must be an object", source)
        return OssiaConfig(source=source)
    raw_plugins = data.get("plugins", [])
    if not isinstance(raw_plugins, list):
        logger.warning("ossia.json at %s: 'plugins' must be a list", source)
        return OssiaConfig(source=source)
    parsed: list[PluginConfig] = []
    for entry in raw_plugins:
        if isinstance(entry, str):
            parsed.append(PluginConfig(name=entry))
        elif isinstance(entry, dict):
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                logger.warning("ossia.json at %s: plugin entry missing string 'name'", source)
                continue
            path_str = entry.get("path")
            path = Path(path_str).expanduser().resolve() if path_str else None
            enabled = entry.get("enabled", True)
            if not isinstance(enabled, bool):
                logger.warning(
                    "ossia.json at %s: plugin %r 'enabled' is not bool, defaulting true",
                    source,
                    name,
                )
                enabled = True
            cfg = entry.get("config", {})
            if not isinstance(cfg, dict):
                logger.warning(
                    "ossia.json at %s: plugin %r 'config' is not an object, ignoring",
                    source,
                    name,
                )
                cfg = {}
            parsed.append(PluginConfig(name=name, path=path, enabled=enabled, config=cfg))
        else:
            logger.warning("ossia.json at %s: ignoring plugin entry %r", source, entry)
    return OssiaConfig(plugins=parsed, source=source)


def _candidate_config_paths() -> list[Path]:
    """Return candidate config file locations, in priority order."""
    out: list[Path] = []
    env = os.environ.get("OSSIA_CONFIG")
    if env:
        out.append(Path(env).expanduser().resolve())
    cwd = Path.cwd()
    for name in _CONFIG_FILE_NAMES:
        out.append(cwd / name)
    global_dir = Path("~/.config/ossia").expanduser()
    for name in _CONFIG_FILE_NAMES:
        out.append(global_dir / name)
    return out


def load_ossia_config() -> OssiaConfig:
    """Find and parse the first ossia.json that exists.

    Returns an empty ``OssiaConfig`` (no plugins declared) when no
    config file is found anywhere on the search path. Errors in the
    config file are logged and treated as "no config" — the loader
    must not crash the agent.
    """
    for path in _candidate_config_paths():
        if path.is_file():
            return _parse(path.read_text(encoding="utf-8"), source=path)
    return OssiaConfig()
