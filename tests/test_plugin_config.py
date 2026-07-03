"""Tests for the ``ossia.json`` config-driven plugin organization.

Covers:
  - JSONC (comments, trailing commas) parsing
  - Location priority (env > project > global)
  - Plugin entry forms (string and object)
  - ``enabled: false`` disables a plugin
  - ``config`` dict is passed to ``register(api, config=...)``
  - Bad JSON / wrong shape is logged + skipped (never raises)
  - End-to-end: a config-declared plugin's tool ends up in the agent
"""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path

import pytest

from core.plugin import (
    discover_plugins,
    load_plugins_into,
)
from core.plugin_config import (
    _parse,
    _strip_jsonc,
    load_ossia_config,
)

# ---------------------------------------------------------------------------
# Unit tests for plugin_config.py
# ---------------------------------------------------------------------------


def test_strip_jsonc_removes_line_and_block_comments() -> None:
    # Inline ``//`` is intentionally not supported — only line-start
    # and block comments. The test reflects that contract.
    text = textwrap.dedent(
        """
        // line comment
        /* block
            comment */
        {"a": 1}
        """
    )
    out = _strip_jsonc(text)
    parsed = json.loads(out)
    assert parsed == {"a": 1}


def test_strip_jsonc_removes_trailing_commas() -> None:
    text = '{"a": 1, "b": 2,}'
    parsed = json.loads(_strip_jsonc(text))
    assert parsed == {"a": 1, "b": 2}


def test_parse_string_entry() -> None:
    cfg = _parse('{"plugins": ["alpha", "beta"]}', source=Path("x"))
    assert [p.name for p in cfg.plugins] == ["alpha", "beta"]
    assert all(p.enabled for p in cfg.plugins)
    assert all(p.path is None for p in cfg.plugins)
    assert all(p.config == {} for p in cfg.plugins)


def test_parse_object_entry_with_full_fields() -> None:
    text = textwrap.dedent(
        """
        {
            "plugins": [
                {
                    "name": "gamma",
                    "path": "./vendor/g",
                    "enabled": false,
                    "config": {"api_key": "x"}
                }
            ]
        }
        """
    )
    cfg = _parse(text, source=Path("x"))
    assert len(cfg.plugins) == 1
    g = cfg.plugins[0]
    assert g.name == "gamma"
    assert g.path is not None and g.path.name == "g"
    assert g.enabled is False
    assert g.config == {"api_key": "x"}


def test_parse_missing_name_is_logged_and_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.plugin_config"):
        cfg = _parse('{"plugins": [{}, {"name": "ok"}]}', source=Path("x"))
    assert [p.name for p in cfg.plugins] == ["ok"]
    assert any("missing" in r.message for r in caplog.records)


def test_parse_invalid_json_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.plugin_config"):
        cfg = _parse("not json", source=Path("x"))
    assert cfg.plugins == []
    assert any("not valid JSON" in r.message for r in caplog.records)


def test_parse_wrong_top_level_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.plugin_config"):
        cfg = _parse("[1, 2, 3]", source=Path("x"))
    assert cfg.plugins == []


def test_parse_plugins_not_a_list(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.plugin_config"):
        cfg = _parse('{"plugins": "nope"}', source=Path("x"))
    assert cfg.plugins == []


def test_parse_ignores_non_string_non_object_entries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="core.plugin_config"):
        cfg = _parse('{"plugins": [42, null, "ok"]}', source=Path("x"))
    assert [p.name for p in cfg.plugins] == ["ok"]


def test_load_ossia_config_returns_empty_when_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OSSIA_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "core.plugin_config.Path.expanduser",
        lambda p: tmp_path / p.name,  # not used here, but safe
    )
    cfg = load_ossia_config()
    assert cfg.plugins == []
    assert cfg.source is None


def test_load_ossia_config_reads_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "myconfig.json"
    f.write_text('{"plugins": ["x"]}')
    monkeypatch.setenv("OSSIA_CONFIG", str(f))
    cfg = load_ossia_config()
    assert [p.name for p in cfg.plugins] == ["x"]
    assert cfg.source == f


def test_load_ossia_config_reads_cwd_jsonc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "ossia.jsonc"
    f.write_text(
        textwrap.dedent(
            """
            // comment
            {"plugins": ["a", "b"]}
            """
        )
    )
    monkeypatch.chdir(tmp_path)
    cfg = load_ossia_config()
    assert [p.name for p in cfg.plugins] == ["a", "b"]


def test_load_ossia_config_reads_cwd_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "ossia.json"
    f.write_text('{"plugins": ["a"]}')
    monkeypatch.chdir(tmp_path)
    cfg = load_ossia_config()
    assert [p.name for p in cfg.plugins] == ["a"]


def test_load_ossia_config_env_wins_over_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd_cfg = tmp_path / "ossia.json"
    cwd_cfg.write_text('{"plugins": ["from_cwd"]}')
    env_cfg = tmp_path / "env.json"
    env_cfg.write_text('{"plugins": ["from_env"]}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OSSIA_CONFIG", str(env_cfg))
    cfg = load_ossia_config()
    assert [p.name for p in cfg.plugins] == ["from_env"]


# ---------------------------------------------------------------------------
# End-to-end: discover_plugins + load_plugins_into honoring config
# ---------------------------------------------------------------------------


def test_config_with_disabled_plugin_skips_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(user_dir))
    (user_dir / "alpha.py").write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "alpha"
            @tool
            def alpha_tool() -> str:
                '''a'''
                return ""
            def register(api):
                api.add_tool(alpha_tool)
            """
        )
    )
    cfg_file = tmp_path / "ossia.json"
    cfg_file.write_text('{"plugins": [{"name": "alpha", "enabled": false}]}')
    monkeypatch.setenv("OSSIA_CONFIG", str(cfg_file))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        plugins = discover_plugins()
    finally:
        get_settings.cache_clear()
    assert "alpha" not in [p.name for p in plugins]


def test_config_passes_dict_to_register(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin's ``register(api, config=...)`` receives the config dict.

    The bundled ``ponytail`` plugin is still in the bundled scan
    path (it always loads), so this test checks that the user
    plugin's config dict is passed — not that it's the only plugin.
    """
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(user_dir))
    (user_dir / "echo.py").write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "echo"
            _RECEIVED = []
            @tool
            def echo_tool() -> str:
                '''e'''
                return ""
            def register(api, config=None):
                _RECEIVED.append(config)
                api.add_tool(echo_tool)
            """
        )
    )
    cfg_file = tmp_path / "ossia.json"
    cfg_file.write_text('{"plugins": [{"name": "echo", "config": {"api_key": "k-123"}}]}')
    monkeypatch.setenv("OSSIA_CONFIG", str(cfg_file))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        plugins = discover_plugins()
    finally:
        get_settings.cache_clear()
    echo = next((p for p in plugins if p.name == "echo"), None)
    assert echo is not None
    assert echo.config == {"api_key": "k-123"}


def test_config_with_explicit_path_loads_from_there(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config entry with ``path`` loads the plugin from that path."""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "third.py").write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "third"
            @tool
            def third_tool() -> str:
                '''t'''
                return ""
            def register(api):
                api.add_tool(third_tool)
            """
        )
    )
    cfg_file = tmp_path / "ossia.json"
    cfg_file.write_text(
        json.dumps({"plugins": [{"name": "third", "path": str(vendor / "third.py")}]})
    )
    monkeypatch.setenv("OSSIA_CONFIG", str(cfg_file))
    # Make sure the user plugins dir is empty so we don't pick anything else up
    empty_user = tmp_path / "user_plugins"
    empty_user.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(empty_user))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        plugins = discover_plugins()
    finally:
        get_settings.cache_clear()
    names = [p.name for p in plugins]
    assert "third" in names


def test_old_signature_register_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plugins using the old ``register(api)`` signature still load."""
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(user_dir))
    (user_dir / "old.py").write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "old"
            @tool
            def old_tool() -> str:
                '''o'''
                return ""
            def register(api):  # no config kwarg
                api.add_tool(old_tool)
            """
        )
    )
    # And give it a config dict via ossia.json — the loader must fall
    # back to the old signature without raising.
    cfg_file = tmp_path / "ossia.json"
    cfg_file.write_text('{"plugins": [{"name": "old", "config": {"k": "v"}}]}')
    monkeypatch.setenv("OSSIA_CONFIG", str(cfg_file))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        plugins = discover_plugins()
    finally:
        get_settings.cache_clear()
    old = next(p for p in plugins if p.name == "old")
    assert [t.name for t in old.tools] == ["old_tool"]


def test_config_only_path_is_loaded_with_config_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config entry with both ``path`` and ``config`` passes both."""
    vendor = tmp_path / "v"
    vendor.mkdir()
    (vendor / "x.py").write_text(
        textwrap.dedent(
            """
            PLUGIN_NAME = "x"
            _GOT = []
            def register(api, config=None):
                _GOT.append(config)
            """
        )
    )
    cfg_file = tmp_path / "ossia.json"
    cfg_file.write_text(
        json.dumps({"plugins": [{"name": "x", "path": str(vendor / "x.py"), "config": {"k": 1}}]})
    )
    monkeypatch.setenv("OSSIA_CONFIG", str(cfg_file))
    empty_user = tmp_path / "user_plugins"
    empty_user.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(empty_user))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        plugins = discover_plugins()
    finally:
        get_settings.cache_clear()
    x = next(p for p in plugins if p.name == "x")
    assert x.config == {"k": 1}


def test_load_plugins_into_with_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a config-declared plugin's tools land in the merged list."""
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(user_dir))
    (user_dir / "fin.py").write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "fin"
            @tool
            def fin_tool() -> str:
                '''f'''
                return ""
            def register(api):
                api.add_tool(fin_tool)
            """
        )
    )
    cfg_file = tmp_path / "ossia.json"
    cfg_file.write_text('{"plugins": ["fin"]}')
    monkeypatch.setenv("OSSIA_CONFIG", str(cfg_file))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        tools: list = []
        subs: list = []
        mws: list = []
        plugins = load_plugins_into(tools=tools, subagents=subs, middlewares=mws)
    finally:
        get_settings.cache_clear()
    assert "fin_tool" in [t.name for t in tools]
    assert "fin" in [p.name for p in plugins]
