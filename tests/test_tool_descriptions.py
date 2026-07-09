"""Tests that all tool descriptions follow the Deep Agents best practices.

Per the "Tool prompts" section of the Deep Agents context engineering docs:

> For tools you provide, make sure to provide a clear name, description, and
> argument descriptions. These guide the model's reasoning about when and how
> to use the tool. Include *when* to use the tool in the description.

Every tool registered via ``create_core_tools()`` must have a docstring that:

1. States *what* the tool does (opening sentence).
2. Contains a "when to use" directive (``"Use this when..."`` or
   ``"Use this for..."``) shortly after the opening.

Adding a new tool without these docstring elements will fail the test,
reminding the author to include usage guidance.
"""

from __future__ import annotations

import re
from typing import Any

from core.agent import create_core_tools

# Pattern matching the "when to use" guidance sentence.
_WHEN_PATTERN = re.compile(r"Use this (?:when|for|after|before|whenever)\b")


def _get_tools() -> list[tuple[str, Any]]:
    """Collect every tool as ``(name, callable)`` pairs from ``create_core_tools()``."""
    tools: list[tuple[str, Any]] = []
    for tool_fn in create_core_tools():
        name = getattr(tool_fn, "name", None) or getattr(tool_fn, "__name__", str(tool_fn))
        tools.append((name, tool_fn))
    return tools


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _get_tool_description(tool_fn: Any) -> str:
    """Return the description text the model actually sees.

    ``StructuredTool.__doc__`` returns a generic fallback string.
    The actual customized description lives in ``.description``.
    """
    desc = getattr(tool_fn, "description", None)
    if desc and isinstance(desc, str) and desc.strip():
        return desc
    return getattr(tool_fn, "__doc__", None) or ""


def test_all_tool_descriptions_have_when_to_use_guidance() -> None:
    """Every tool's description must contain a "Use this when/for/after..." sentence."""
    errors: list[str] = []
    for tool_name, tool_fn in _get_tools():
        desc = _get_tool_description(tool_fn)
        if not desc.strip():
            errors.append(f"'{tool_name}' has no description")
        elif not _WHEN_PATTERN.search(desc):
            errors.append(
                f"'{tool_name}' is missing a 'Use this when...' guidance sentence.\n"
                f"Current description:\n{desc}"
            )
    assert not errors, f"\n\n{chr(10).join(errors)}"


def test_all_tool_descriptions_open_with_what_it_does() -> None:
    """The first paragraph of every tool description must state *what* the tool does."""
    errors: list[str] = []
    for tool_name, tool_fn in _get_tools():
        desc = _get_tool_description(tool_fn)
        if not desc.strip():
            errors.append(f"'{tool_name}' has no description")
            continue
        first_para = desc.strip().split("\n\n")[0]
        first_sentence = first_para.split(".")[0].strip()
        if len(first_sentence) < 20:
            errors.append(
                f"'{tool_name}' first sentence too short "
                f"({len(first_sentence)} chars): {first_sentence!r}"
            )
    assert not errors, f"\n\n{chr(10).join(errors)}"


def test_core_tool_count_is_stable() -> None:
    """Verify the total tool count hasn't changed unexpectedly.

    Update this assertion when tools are intentionally added or removed.
    """
    tools = _get_tools()
    # GOAL-0002 M1+M2: 6 tools moved from the coordinator to subagents.
    # M1: search_codebase, search_knowledge_base, run_tests, propose_fix
    #     (4) -> code-researcher, code-researcher, test-runner, fix-proposer.
    # M2: internet_search, fetch_url (2) -> new research subagent.
    # See test_subagent_count_is_stable in test_subagent_descriptions.py
    # for the matching subagent count (was 9, now 10).
    assert len(tools) == 10, (
        f"Expected 10 tools (post-M1+M2), got {len(tools)}.\n"
        f"Tool names: {[t[0] for t in tools]}"
    )
