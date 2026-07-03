"""Tests that all subagent descriptions follow the Deep Agents best practices.

Every subagent — both sync (``_DEV_CONCIERGE_SUBAGENTS``) and async
(``_build_async_subagents``) — must have a description that tells the
main agent *when* to delegate to it. Descriptions must contain a
"when to use" directive (``"Use this when..."``, ``"Use this for..."``,
or ``"Use this after..."``) after the opening sentence.

Adding a new subagent without these description elements will fail the
test, reminding the author to include usage guidance.
"""

from __future__ import annotations

import re

from core.agent import _DEV_CONCIERGE_SUBAGENTS, _build_async_subagents
from core.config import get_settings

# Pattern matching the "when to use" guidance sentence.
_WHEN_PATTERN = re.compile(r"Use this (?:when|for|after|before|whenever)\b")


def _collect_subagents() -> list[tuple[str, str]]:
    """Collect every subagent as ``(name, description)`` pairs.

    Combines all 8 sync subagents (from ``_DEV_CONCIERGE_SUBAGENTS``)
    with all 3 async subagents (from ``_build_async_subagents``).
    """
    collected: list[tuple[str, str]] = []

    # Sync subagents: tuple of (name, description, system_prompt)
    for name, description, _system_prompt in _DEV_CONCIERGE_SUBAGENTS:
        collected.append((name, description))

    # Async subagents: TypedDict with "name" and "description" keys
    settings = get_settings()
    for spec in _build_async_subagents(settings):
        collected.append((spec["name"], spec["description"]))

    return collected


_COLLECTED = _collect_subagents()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_all_subagent_descriptions_have_when_to_use_guidance() -> None:
    """Every subagent description must contain a 'Use this when/for/after...' sentence."""
    errors: list[str] = []
    for name, description in _COLLECTED:
        if not description.strip():
            errors.append(f"'{name}' has no description")
        elif not _WHEN_PATTERN.search(description):
            errors.append(
                f"'{name}' is missing a 'Use this when...' guidance sentence.\n"
                f"Current description:\n{description}"
            )
    assert not errors, f"\n\n{chr(10).join(errors)}"


def test_all_subagent_descriptions_open_with_what_it_does() -> None:
    """The first sentence of every subagent description must state *what* the subagent does."""
    errors: list[str] = []
    for name, description in _COLLECTED:
        if not description.strip():
            errors.append(f"'{name}' has no description")
            continue
        first_sentence = description.strip().split(".")[0].strip()
        if len(first_sentence) < 20:
            errors.append(
                f"'{name}' first sentence too short "
                f"({len(first_sentence)} chars): {first_sentence!r}"
            )
    assert not errors, f"\n\n{chr(10).join(errors)}"


def test_subagent_count_is_stable() -> None:
    """Verify the total subagent count hasn't changed unexpectedly.

    Update this assertion when subagents are intentionally added or removed.
    """
    assert len(_COLLECTED) == 11, (
        f"Expected 11 subagents (8 sync + 3 async), got {len(_COLLECTED)}.\n"
        f"Subagent names: {[n for n, _ in _COLLECTED]}"
    )
