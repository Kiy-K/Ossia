"""Tests that async subagent ``graph_id`` values match actual graph modules.

Every async subagent registered in ``_build_async_subagents()`` has a
``graph_id`` that must:

1. Have a corresponding Python module at ``src/core/graphs/<graph_id>.py``.
2. Export a ``graph`` attribute (a ``CompiledStateGraph`` instance).
3. Be registered in ``langgraph.json`` for LangGraph deployments.

Additionally, no orphaned graph modules should exist — every module in
``src/core/graphs/`` (except ``__init__.py`` and ``supervisor.py``) must
be referenced by at least one async subagent.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from core.agent import _build_async_subagents
from core.config import get_settings

# Paths relative to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRAPHS_DIR = PROJECT_ROOT / "src" / "core" / "graphs"
LANGGRAPH_JSON = PROJECT_ROOT / "langgraph.json"


def _get_async_subagent_graph_ids() -> list[str]:
    """Return all ``graph_id`` values from ``_build_async_subagents()``."""
    settings = get_settings()
    return [spec["graph_id"] for spec in _build_async_subagents(settings)]


def _get_graph_modules() -> list[str]:
    """Return all graph module file stems (minus ``.py``) in ``src/core/graphs/``.

    Excludes ``__init__`` and ``supervisor`` because ``supervisor.py`` is the
    main entry-point graph, not an async subagent.
    """
    modules: list[str] = []
    for fpath in sorted(GRAPHS_DIR.iterdir()):
        if fpath.suffix != ".py":
            continue
        stem = fpath.stem
        if stem in ("__init__", "supervisor"):
            continue
        modules.append(stem)
    return modules


def _load_langgraph_config() -> dict[str, Any]:
    """Load and return the parsed ``langgraph.json`` config."""
    if not LANGGRAPH_JSON.exists():
        return {}
    return json.loads(LANGGRAPH_JSON.read_text())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_graph_ids_have_corresponding_modules() -> None:
    """Every async subagent ``graph_id`` must have a matching module in ``src/core/graphs/``."""
    graph_ids = _get_async_subagent_graph_ids()
    assert graph_ids, "No async subagent graph_ids found"

    errors: list[str] = []
    for gid in graph_ids:
        module_path = GRAPHS_DIR / f"{gid}.py"
        if not module_path.exists():
            errors.append(
                f"graph_id '{gid}' has no matching module at "
                f"src/core/graphs/{gid}.py"
            )
    assert not errors, f"\n\n{chr(10).join(errors)}"


def test_all_graph_modules_export_graph_variable() -> None:
    """Every graph module must export a ``graph`` variable."""
    graph_ids = _get_async_subagent_graph_ids()
    assert graph_ids, "No async subagent graph_ids found"

    errors: list[str] = []
    for gid in graph_ids:
        try:
            mod = importlib.import_module(f"core.graphs.{gid}")
            if not hasattr(mod, "graph"):
                errors.append(
                    f"Module core.graphs.{gid} does not export 'graph'"
                )
        except ImportError as exc:
            errors.append(
                f"Could not import core.graphs.{gid}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"Error loading core.graphs.{gid}.graph: {exc}"
            )
    assert not errors, f"\n\n{chr(10).join(errors)}"


def test_all_graph_ids_registered_in_langgraph_json() -> None:
    """Every async subagent ``graph_id`` must be registered in ``langgraph.json``."""
    config = _load_langgraph_config()
    registered = set(config.get("graphs", {}).keys())
    graph_ids = set(_get_async_subagent_graph_ids())

    missing = graph_ids - registered
    assert not missing, (
        f"graph_ids {missing} are not registered in langgraph.json. "
        f"Add entries like:\n"
        + "\n".join(
            f'  "{gid}": "./src/core/graphs/{gid}.py:graph"'
            for gid in sorted(missing)
        )
    )


def test_no_orphaned_graph_modules() -> None:
    """Every module in ``src/core/graphs/`` (except __init__) must be referenced.

    Orphaned graph modules suggest a stale file that should be cleaned up.
    """
    modules = set(_get_graph_modules())
    graph_ids = set(_get_async_subagent_graph_ids())

    # Also check supervisor (explicitly excluded from _get_graph_modules)
    orphaned = modules - graph_ids
    assert not orphaned, (
        f"Unreferenced graph modules found (no async subagent uses these "
        f"graph_ids): {sorted(orphaned)}. Either add a corresponding async "
        f"subagent or remove the stale module."
    )


def test_langgraph_json_entries_point_to_valid_modules() -> None:
    """Every entry in langgraph.json must point to an existing module that exports graph."""
    config = _load_langgraph_config()
    errors: list[str] = []

    for graph_name, ref in config.get("graphs", {}).items():
        # ref format: "./src/core/graphs/foo.py:graph"
        # Strip leading "./" and trailing ":graph" to get the filesystem path.
        module_path = ref.lstrip("./").split(":")[0]
        full_path = PROJECT_ROOT / module_path
        if not full_path.exists():
            errors.append(
                f"langgraph.json entry '{graph_name}' -> '{ref}' points to "
                f"non-existent file {module_path}"
            )
            continue

        try:
            # Convert filesystem path to dotted module path
            rel = full_path.relative_to(PROJECT_ROOT / "src")
            dotted = str(rel.with_suffix("")).replace("/", ".")
            mod = importlib.import_module(dotted)
            if not hasattr(mod, "graph"):
                errors.append(
                    f"langgraph.json entry '{graph_name}' -> '{ref}' points "
                    f"to {dotted} which does not export 'graph'"
                )
        except ImportError as exc:
            errors.append(
                f"langgraph.json entry '{graph_name}' -> '{ref}' failed "
                f"to import: {exc}"
            )

    assert not errors, f"\n\n{chr(10).join(errors)}"
