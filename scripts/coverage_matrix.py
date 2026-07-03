#!/usr/bin/env python3
"""Coverage matrix: map API routes against feature specs.

Reads ``specs/openapi.checked.json`` for the current route set and
all ``specs/features/*.md`` for their ``## Endpoint impact`` tables,
then produces ``specs/coverage.md``: a markdown table with rows =
routes, columns = feature specs, cells = ✓ (covered) or — (uncovered).

Usage:
    .venv/bin/python scripts/coverage_matrix.py

Exits 0 on success, 1 on error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = REPO_ROOT / "specs" / "openapi.checked.json"
FEATURES_DIR = REPO_ROOT / "specs" / "features"
OUTPUT_PATH = REPO_ROOT / "specs" / "coverage.md"

# Matches a table row like ``| GET | ``/v1/chat`` | New endpoint |``
ENDPOINT_ROW_RE = re.compile(
    r"^\|\s*(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\|\s*`([^`]+)`\s*\|",
    re.IGNORECASE | re.MULTILINE,
)


def _load_openapi_routes() -> list[str]:
    """Return sorted list of route paths from the OpenAPI spec."""
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    paths = sorted(spec.get("paths", {}).keys())
    return paths


def _load_feature_specs() -> list[tuple[str, list[str]]]:
    """Return list of (feature_name, covered_routes) from feature specs.

    Scans each ``.md`` in ``FEATURES_DIR`` (excluding TEMPLATE.md) for
    ``## Endpoint impact`` section and extracts route paths from table rows.
    """
    specs: list[tuple[str, list[str]]] = []
    for fpath in sorted(FEATURES_DIR.glob("*.md")):
        if fpath.name == "TEMPLATE.md":
            continue
        text = fpath.read_text(encoding="utf-8")
        # Extract feature name from first heading
        name_match = re.search(r"^# Feature:\s*(.+)$", text, re.MULTILINE)
        name = name_match.group(1).strip() if name_match else fpath.stem
        # Extract routes from Endpoint impact tables
        routes: list[str] = []
        # We look for all table rows that match the endpoint pattern
        for m in ENDPOINT_ROW_RE.finditer(text):
            route = m.group(2).strip()
            if route not in routes:
                routes.append(route)
        specs.append((name, routes))
    return specs


def _build_matrix(routes: list[str], specs: list[tuple[str, list[str]]]) -> str:
    """Build a markdown coverage matrix.

    Columns are feature spec names, rows are API routes. A ✓ means
    the route is mentioned in that spec's Endpoint impact table.
    The final column counts how many features cover each route.
    """
    # Header
    col_names = [name for name, _ in specs]
    lines: list[str] = []
    header = f"| Route | {' | '.join(col_names)} | Coverage count |"
    lines.append(header)
    sep = f"| --- |{'|'.join(' --- ' for _ in col_names)} | --- |"
    lines.append(sep)

    # Build a lookup: route -> set of feature names
    route_features: dict[str, set[str]] = {}
    for route in routes:
        route_features[route] = set()

    for name, covered_routes in specs:
        # Normalize routes with path params: /v1/threads/{id}/state -> /v1/threads/{thread_id}/state
        # but we use exact match from the spec
        for cr in covered_routes:
            # Try exact match first
            if cr in route_features:
                route_features[cr].add(name)
            else:
                # Try path-parameter normalization
                # e.g. feature spec says {id} but OpenAPI says {thread_id}
                # We'll normalize by treating any {name} as a generic param
                norm_cr = re.sub(r"\{[^}]+\}", "{}", cr)
                for r in routes:
                    norm_r = re.sub(r"\{[^}]+\}", "{}", r)
                    if norm_cr == norm_r:
                        route_features[r].add(name)

    # Rows
    for route in routes:
        covered = route_features.get(route, set())
        cells = ["✓" if name in covered else "—" for name, _ in specs]
        count = len(covered)
        lines.append(f"| `{route}` | {' | '.join(cells)} | {count} |")

    # Totals row
    total_cols = [
        sum(1 for r in routes if name in route_features.get(r, set())) for name, _ in specs
    ]
    totals = " | ".join(str(t) for t in total_cols)
    lines.append(f"| **Totals** | {totals} | **{len(routes)}** |")

    return "\n".join(lines) + "\n"


def main() -> int:
    routes = _load_openapi_routes()
    if not routes:
        print("Error: No routes found in OpenAPI spec.", file=sys.stderr)
        return 1

    specs = _load_feature_specs()
    if not specs:
        print("Warning: No feature specs found (excluding TEMPLATE.md).", file=sys.stderr)

    matrix = _build_matrix(routes, specs)
    OUTPUT_PATH.write_text(matrix, encoding="utf-8")
    print(f"Coverage matrix written to {OUTPUT_PATH}")
    print(f"  Routes: {len(routes)}")
    print(f"  Feature specs: {len(specs)}")

    # Check for uncovered routes
    route_features: dict[str, set[str]] = {r: set() for r in routes}
    for name, covered_routes in specs:
        for cr in covered_routes:
            norm_cr = re.sub(r"\{[^}]+\}", "{}", cr)
            for r in routes:
                norm_r = re.sub(r"\{[^}]+\}", "{}", r)
                if norm_cr == norm_r:
                    route_features[r].add(name)

    uncovered = [r for r in routes if not route_features.get(r)]
    if uncovered:
        print("\nUncovered routes (0 feature specs reference them):")
        for r in uncovered:
            print(f"  {r}")
    else:
        print("\nAll routes are covered by at least one feature spec.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
