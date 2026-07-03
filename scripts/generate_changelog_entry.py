#!/usr/bin/env python3
"""Generate a draft changelog entry from new or changed feature specs.

Scans ``specs/features/*.md`` for specs whose ``Status:`` field is
``implemented``, extracts the "What it does" paragraph and the endpoint
impact table, and generates a draft markdown entry following the
``specs/changelog.md`` format.

Usage:
    .venv/bin/python scripts/generate_changelog_entry.py

Options:
    --since <git-ref>   Only consider specs changed since this git ref
                        (default: HEAD~1).
    --all               Include all implemented specs, not just changed ones.
    --dry-run           Print to stdout instead of appending to changelog.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURES_DIR = REPO_ROOT / "specs" / "features"
CHANGELOG_PATH = REPO_ROOT / "specs" / "changelog.md"

# Headers that mark sections in a feature spec
STATUS_RE = re.compile(r"^-\s*\*\*Status:\*\*\s*(\S+)", re.MULTILINE)
STATUS_RE_ALT = re.compile(r"^-\s*Status:\s*(\S+)", re.MULTILINE)
WHAT_IT_DOES_RE = re.compile(
    r"^##\s*What it does\s*\n+(.+?)(?=\n##\s|\Z)", re.MULTILINE | re.DOTALL
)
ENDPOINT_RE = re.compile(r"^##\s*Endpoint impact\s*\n+(.+?)(?=\n##\s|\Z)", re.MULTILINE | re.DOTALL)
FEATURE_NAME_RE = re.compile(r"^#\s*Feature:\s*(.+)$", re.MULTILINE)
SCOPE_RE = re.compile(r"^-\s*\*\*Scope:\*\*\s*(\S+)", re.MULTILINE)
SCOPE_RE_ALT = re.compile(r"^-\s*Scope:\s*(\S+)", re.MULTILINE)
ADR_RE = re.compile(r"^-\s*(?:\*\*)?ADR(?:\*\*)?:\s*(\S+)", re.MULTILINE)


def _git_changed_files(since: str) -> set[str]:
    """Return set of file paths changed since ``since`` git ref.

    Handles repos with only one commit gracefully (returns empty set
    when ``since`` ref does not exist).
    """
    try:
        # Verify the ref exists before diffing
        subprocess.run(
            ["git", "rev-parse", "--verify", since],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError:
        return set()
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since, "--", "specs/features/"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        )
        return set(result.stdout.strip().splitlines())
    except subprocess.CalledProcessError:
        return set()


def _extract_status(text: str) -> str:
    """Extract the Status value from a feature spec."""
    m = STATUS_RE.search(text) or STATUS_RE_ALT.search(text)
    return m.group(1).lower() if m else "draft"


def _extract_name(text: str) -> str:
    """Extract the feature name from a feature spec."""
    m = FEATURE_NAME_RE.search(text)
    return m.group(1).strip() if m else "Unnamed feature"


def _extract_what_it_does(text: str) -> str:
    """Extract the What it does paragraph."""
    m = WHAT_IT_DOES_RE.search(text)
    if not m:
        return ""
    # Take only the first paragraph (split on double newline)
    para = m.group(1).strip()
    return para.split("\n\n")[0].strip()


def _extract_endpoint_table(text: str) -> str:
    """Extract the Endpoint impact table."""
    m = ENDPOINT_RE.search(text)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_scope(text: str) -> str:
    """Extract the scope value."""
    m = SCOPE_RE.search(text) or SCOPE_RE_ALT.search(text)
    return m.group(1).strip() if m else ""


def _extract_adr(text: str) -> str:
    """Extract the ADR reference."""
    m = ADR_RE.search(text)
    return m.group(1).strip() if m else ""


def _generate_entry(name: str, text: str) -> str:
    """Generate a single changelog entry for a feature spec."""
    what = _extract_what_it_does(text)
    endpoints = _extract_endpoint_table(text)
    adr = _extract_adr(text)

    entry_parts: list[str] = []
    entry_parts.append(f"### {name}")
    if what:
        entry_parts.append("")
        entry_parts.append(what)
    if endpoints:
        entry_parts.append("")
        entry_parts.append("**Endpoint impact:**")
        entry_parts.append("")
        entry_parts.append(endpoints)
    if adr:
        entry_parts.append("")
        entry_parts.append(f"See `{adr}` for the full decision record.")
    return "\n".join(entry_parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a draft changelog entry from feature specs."
    )
    parser.add_argument(
        "--since",
        default="HEAD~1",
        help="Git ref to diff against (default: HEAD~1)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all implemented specs, not just changed ones",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print to stdout instead of updating changelog",
    )
    args = parser.parse_args()

    if not FEATURES_DIR.exists():
        print(f"Error: features dir not found at {FEATURES_DIR}", file=sys.stderr)
        return 1

    changed_files = _git_changed_files(args.since)

    specs: list[tuple[str, str]] = []
    for fpath in sorted(FEATURES_DIR.glob("*.md")):
        if fpath.name == "TEMPLATE.md":
            continue
        text = fpath.read_text(encoding="utf-8")
        status = _extract_status(text)
        if status != "implemented":
            continue
        if not args.all:
            rel_path = str(fpath.relative_to(REPO_ROOT))
            if rel_path not in changed_files:
                continue
        name = _extract_name(text)
        specs.append((name, text))

    if not specs:
        print("No new or changed implemented feature specs found.")
        return 0

    today = date.today().isoformat()
    lines: list[str] = []
    lines.append(f"## v<next> — {today}")
    lines.append("")
    for name, text in specs:
        entry = _generate_entry(name, text)
        if entry:
            lines.append(entry)
            lines.append("")

    output = "\n".join(lines).strip() + "\n"

    if args.dry_run:
        print(output)
    else:
        # Prepend to changelog
        existing = CHANGELOG_PATH.read_text(encoding="utf-8")
        updated = output + "\n" + existing
        CHANGELOG_PATH.write_text(updated, encoding="utf-8")
        print(f"Changelog entry prepended to {CHANGELOG_PATH}")

    print(f"\n{len(specs)} feature(s) processed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
