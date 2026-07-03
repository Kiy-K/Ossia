"""Validate feature spec structure and cross-references.

Mirrors the ``test_openapi_drift.py`` pattern: enumerates all feature
specs in ``specs/features/``, validates required sections, checks that
endpoint references match actual routes in the pinned OpenAPI spec,
and validates that ADR cross-references resolve to files in ``docs/adr/``.

To add a new feature spec, create a file in ``specs/features/<slug>.md``
following the template at ``specs/features/TEMPLATE.md``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = REPO_ROOT / "specs" / "features"
OPENAPI_PATH = REPO_ROOT / "specs" / "openapi.checked.json"
ADRS_DIR = REPO_ROOT / "docs" / "adr"

# Required sections that every feature spec must have
_REQUIRED_SECTIONS: set[str] = {
    "What it does",
    "Scope table",
    "Endpoint impact",
    "Safety/Permissions",
    "NFRs",
    "Affected modules",
    "Testing notes",
}

SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
STATUS_RE = re.compile(r"^-\s*(?:\*\*)?Status(?:\*\*)?:\s*(\S+)", re.MULTILINE)
SCOPE_RE = re.compile(r"^-\s*(?:\*\*)?Scope(?:\*\*)?:\s*(\S+)", re.MULTILINE)
ADR_RE = re.compile(r"^-\s*(?:\*\*)?ADR(?:\*\*)?:\s*(docs/adr/\S+)", re.MULTILINE)
ENDPOINT_ROW_RE = re.compile(
    r"^\|\s*(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\|\s*`([^`]+)`\s*\|",
    re.IGNORECASE | re.MULTILINE,
)


@lru_cache(maxsize=1)
def _load_openapi_routes() -> set[str]:
    """Return set of route paths from the pinned OpenAPI spec."""
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    return set(spec.get("paths", {}).keys())


def _collect_feature_specs() -> list[Path]:
    """Return sorted list of feature spec paths (excluding TEMPLATE.md)."""
    specs = sorted(FEATURES_DIR.glob("*.md"))
    return [s for s in specs if s.name != "TEMPLATE.md"]


# ── Tests ────────────────────────────────────────────────────────────────────


def test_template_exists() -> None:
    """TEMPLATE.md must exist as the canonical starting point for new specs."""
    tmpl = FEATURES_DIR / "TEMPLATE.md"
    assert tmpl.exists(), (
        f"TEMPLATE.md not found at {tmpl}. Create it to define the canonical feature spec format."
    )


@pytest.mark.parametrize("spec_path", _collect_feature_specs(), ids=lambda p: p.name)
def test_required_sections_present(spec_path: Path) -> None:
    """Every feature spec must have all required sections."""
    text = spec_path.read_text(encoding="utf-8")
    sections = set(SECTION_RE.findall(text))
    missing = _REQUIRED_SECTIONS - sections
    assert not missing, (
        f"{spec_path.name} is missing required section(s): {', '.join(sorted(missing))}"
    )


@pytest.mark.parametrize("spec_path", _collect_feature_specs(), ids=lambda p: p.name)
def test_required_frontmatter_fields(spec_path: Path) -> None:
    """Every feature spec must have Status, Scope, and ADR fields in frontmatter."""
    text = spec_path.read_text(encoding="utf-8")

    status_m = STATUS_RE.search(text)
    assert status_m is not None, (
        f"{spec_path.name} is missing 'Status:' field in frontmatter. "
        "Add a line like '- Status: draft | accepted | implemented'."
    )
    status = status_m.group(1).lower()
    assert status in ("draft", "accepted", "implemented"), (
        f"{spec_path.name} has invalid Status '{status}'. "
        "Must be one of: draft, accepted, implemented."
    )

    scope_m = SCOPE_RE.search(text)
    assert scope_m is not None, (
        f"{spec_path.name} is missing 'Scope:' field in frontmatter. "
        "Add a line like '- Scope: tool | middleware | route | memory | subagent | infrastructure'."
    )

    adr_m = ADR_RE.search(text)
    assert adr_m is not None, (
        f"{spec_path.name} is missing 'ADR:' field in frontmatter. "
        "Add a line like '- ADR: docs/adr/NNNN-slug.md'."
    )


@pytest.mark.parametrize("spec_path", _collect_feature_specs(), ids=lambda p: p.name)
def test_endpoint_references_resolve(spec_path: Path) -> None:
    """Endpoint references in '## Endpoint impact' tables must match actual API routes."""
    text = spec_path.read_text(encoding="utf-8")
    openapi_routes = _load_openapi_routes()

    # Normalize: replace OpenAPI path params like {thread_id} with generic {}
    # Feature specs may use {id} or {thread_id}; we normalize both.
    def _normalize(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", path)

    normalized_routes = {_normalize(r) for r in openapi_routes}

    for m in ENDPOINT_ROW_RE.finditer(text):
        route = m.group(2).strip()
        norm = _normalize(route)
        assert norm in normalized_routes, (
            f"{spec_path.name} references endpoint `{route}` "
            f"which does not match any route in the pinned OpenAPI spec. "
            f"Available routes: {sorted(openapi_routes)}"
        )


@pytest.mark.parametrize("spec_path", _collect_feature_specs(), ids=lambda p: p.name)
def test_adr_references_resolve(spec_path: Path) -> None:
    """ADR cross-references in frontmatter must resolve to files in docs/adr/."""
    text = spec_path.read_text(encoding="utf-8")
    for m in ADR_RE.finditer(text):
        adr_path = REPO_ROOT / m.group(1)
        assert adr_path.exists(), (
            f"{spec_path.name} references ADR `{m.group(1)}` "
            f"but the file does not exist at {adr_path}. "
            f"Available ADRs: {sorted(p.name for p in ADRS_DIR.glob('*.md'))}"
        )


def test_endpoint_impact_matches_known_routes() -> None:
    """All feature specs combined must reference only valid OpenAPI routes."""
    openapi_routes = _load_openapi_routes()

    def _normalize(path: str) -> str:
        return re.sub(r"\{[^}]+\}", "{}", path)

    normalized_routes = {_normalize(r) for r in openapi_routes}
    invalid_refs: list[tuple[str, str]] = []

    for spec_path in _collect_feature_specs():
        text = spec_path.read_text(encoding="utf-8")
        for m in ENDPOINT_ROW_RE.finditer(text):
            route = m.group(2).strip()
            norm = _normalize(route)
            if norm not in normalized_routes:
                invalid_refs.append((spec_path.name, route))

    assert not invalid_refs, (
        "The following feature specs reference non-existent routes:\n"
        + "\n".join(f"  - {name}: `{route}`" for name, route in invalid_refs)
    )
