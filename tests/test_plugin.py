"""Tests for the plugin loader and the Ponytail plugin.

The loader has a few important contracts:
  1. A file that defines ``register(api)`` is loaded and contributes
     to the agent. A file that doesn't is silently skipped.
  2. A bad plugin (import error, register() raises) is logged and
     skipped — the agent keeps booting.
  3. Bundled plugins load before user plugins; alphabetical within
     each dir; first plugin wins on name collision.
  4. Plugin-contributed tools / subagents / middlewares are merged
     into the agent's lists in discovery order.
"""

from __future__ import annotations

import logging
import sys
import textwrap
from pathlib import Path

import pytest
from langchain_core.tools import tool

from core.plugin import (
    _BUNDLED_PLUGINS_DIR,
    PluginAPI,
    _candidate_files,
    _load_module_from_path,
    _load_one,
    _resolve_user_plugins_dir,
    discover_plugins,
    load_plugins_into,
)


@pytest.fixture
def isolated_plugin_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point OSSIA_PLUGINS_DIR at a fresh tmp dir for each test."""
    user_dir = tmp_path / "user_plugins"
    user_dir.mkdir()
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(user_dir))
    # Clear the settings cache so the new env var takes effect
    from core.config import get_settings

    get_settings.cache_clear()
    yield user_dir
    get_settings.cache_clear()


def _write_plugin(path: Path, body: str, name: str | None = None) -> None:
    """Write a Python file at ``path`` with the given body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    if name:
        # Re-import the module under a unique alias so the alias is
        # known. Module alias is set when we load it, not here.
        del name


def test_plugin_api_records_tools_subagents_middlewares() -> None:
    """The PluginAPI surface is the only contract a plugin sees."""
    api = PluginAPI(name="t")

    @tool
    def my_tool() -> str:
        """A tool."""
        return ""

    class _Mw:
        pass

    api.add_tool(my_tool)
    api.add_subagent(
        name="sa",
        description="d",
        system_prompt="p",
        tools=[my_tool],
    )
    api.add_middleware(_Mw())

    assert [t.name for t in api.tools] == ["my_tool"]
    assert [sa["name"] for sa in api.subagents] == ["sa"]
    assert isinstance(api.middlewares[0], _Mw)


def test_candidate_files_skips_private_and_non_py(tmp_path: Path) -> None:
    """Only top-level ``.py`` files and package ``__init__.py`` files count."""
    root = tmp_path / "plugins"
    root.mkdir()
    (root / "alpha.py").write_text("")
    (root / "_private.py").write_text("")
    (root / "README.md").write_text("")
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "module.py").write_text("")  # nested: ignored
    files = sorted(p.name for p in _candidate_files(root))
    # ``_candidate_files`` yields top-level files first (in iterdir
    # order, sorted) and then package ``__init__.py`` files. With one
    # file (``alpha.py``) and one package (``pkg/__init__.py``), the
    # expected set is just those two.
    assert set(files) == {"alpha.py", "__init__.py"}
    assert len(files) == 2  # not double-counted


def test_load_one_skips_file_without_register(tmp_path: Path) -> None:
    """A file with no ``register`` function is silently skipped."""
    f = tmp_path / "no_register.py"
    f.write_text("x = 1\n")
    assert _load_one(f) is None


def test_load_one_skips_broken_plugin(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A file that raises on import is logged and skipped — never propagates."""
    f = tmp_path / "broken.py"
    f.write_text("raise RuntimeError('boom')\n")
    with caplog.at_level(logging.WARNING, logger="core.plugin"):
        result = _load_one(f)
    assert result is None
    assert any("broken" in rec.message for rec in caplog.records)


def test_load_one_runs_register_and_collects_tool(tmp_path: Path) -> None:
    """A well-formed plugin's register() contribution is captured."""
    f = tmp_path / "good.py"
    f.write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool

            PLUGIN_NAME = "good"

            @tool
            def hello() -> str:
                '''Says hi.'''
                return "hi"

            def register(api):
                api.add_tool(hello)
            """
        )
    )
    plugin = _load_one(f)
    assert plugin is not None
    assert plugin.name == "good"
    assert [t.name for t in plugin.tools] == ["hello"]


def test_load_one_calls_register_with_a_fresh_api(tmp_path: Path) -> None:
    """A second call to _load_one does NOT inherit state from the first."""
    f1 = tmp_path / "a.py"
    f1.write_text(
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "a"
            @tool
            def ta() -> str:
                '''a tool'''
                return ""
            def register(api):
                api.add_tool(ta)
            """
        )
    )
    f2 = tmp_path / "b.py"
    f2.write_text(
        textwrap.dedent(
            """
            PLUGIN_NAME = "b"
            def register(api):
                pass
            """
        )
    )
    p1 = _load_one(f1)
    p2 = _load_one(f2)
    assert p1 is not None and p2 is not None
    # The second plugin did NOT inherit tools from the first.
    assert p1.tools and not p2.tools


def test_load_plugins_into_merges_into_lists(isolated_plugin_dirs: Path) -> None:
    """discover_plugins + load_plugins_into wires tools/subagents/middlewares.

    The bundled ``ponytail`` plugin also loads (it ships in
    ``plugins/``), so this test asserts that the user-contributed
    plugin's contributions are PRESENT in the merged lists — not
    that the lists contain ONLY the user plugin.
    """
    _write_plugin(
        isolated_plugin_dirs / "userplug.py",
        textwrap.dedent(
            """
            from langchain_core.tools import tool
            PLUGIN_NAME = "userplug"
            @tool
            def utool() -> str:
                '''u'''
                return ""
            def register(api):
                api.add_tool(utool)
                api.add_subagent(name='usub', description='d', system_prompt='p')
            """
        ),
    )
    tools: list = []
    subs: list = []
    mws: list = []
    plugins = load_plugins_into(tools=tools, subagents=subs, middlewares=mws)
    names = [p.name for p in plugins]
    assert "userplug" in names
    # The user plugin's tool is present (alongside any bundled tools).
    assert "utool" in [t.name for t in tools]
    # The user plugin's subagent is present.
    assert "usub" in [s["name"] for s in subs]


def test_duplicate_plugin_name_is_skipped(
    isolated_plugin_dirs: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If bundled and user dirs both have a plugin with the same name, the first wins."""
    # Write a "ponytail"-named plugin to the user dir
    _write_plugin(
        isolated_plugin_dirs / "ponytail.py",
        textwrap.dedent(
            """
            PLUGIN_NAME = "ponytail"
            def register(api):
                pass
            """
        ),
    )
    with caplog.at_level(logging.WARNING, logger="core.plugin"):
        plugins = discover_plugins()
    names = [p.name for p in plugins]
    # The bundled one wins; the user one is logged and skipped.
    assert names.count("ponytail") == 1
    assert any("duplicate plugin name" in rec.message for rec in caplog.records)


def test_bundled_ponytail_plugin_loads() -> None:
    """The bundled ``plugins/ponytail/`` package is discoverable and registers its tool."""
    assert _BUNDLED_PLUGINS_DIR.is_dir()
    plugins = discover_plugins()
    pony = next((p for p in plugins if p.name == "ponytail"), None)
    assert pony is not None
    assert [t.name for t in pony.tools] == ["ponytail_review"]


def test_resolve_user_plugins_dir_creates_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user plugins dir is created if missing, and respects OSSIA_PLUGINS_DIR."""
    target = tmp_path / "fresh_plugins"
    monkeypatch.setenv("OSSIA_PLUGINS_DIR", str(target))
    from core.config import get_settings

    get_settings.cache_clear()
    try:
        resolved = _resolve_user_plugins_dir()
    finally:
        get_settings.cache_clear()
    assert resolved == target
    assert target.is_dir()


def test_load_module_from_path_uses_unique_alias(tmp_path: Path) -> None:
    """Each load gets a unique ``sys.modules`` alias so re-imports don't shadow."""
    f = tmp_path / "alias_test.py"
    f.write_text("VALUE = 42\n")
    mod = _load_module_from_path(f, "ossia_plugin_alias_test")
    assert mod.VALUE == 42
    assert "ossia_plugin_alias_test" in sys.modules
    # The alias is the path stem — two files with the same stem would
    # collide, but our _load_one uses path.stem so this is intrinsic.
    del sys.modules["ossia_plugin_alias_test"]
