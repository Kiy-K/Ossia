"""Ponytail — the laziest-solution reviewer plugin.

The first plugin shipped with the agent. Exposes a single
``ponytail_review`` tool the agent (or a reviewer subagent) can call
to check a diff, snippet, or design proposal against the Ponytail
ladder:

  1. Does this need to exist at all? (YAGNI)
  2. Already in this codebase? (reuse it)
  3. Stdlib does it? (use it)
  4. Native platform feature covers it? (use it)
  5. Already-installed dependency solves it? (use it)
  6. Can it be one line? (one line)
  7. Only then: the minimum code that works.

The tool is a static, deterministic heuristic — no LLM call. It
flag obvious over-engineering patterns and returns a structured
review. Plugins and code marked with ``# ponytail:`` comments are
deliberate simplifications and are NOT flagged (those are a
ceiling marker, not laziness).

Ponytail: no LLM, no async, no external deps. The tool's verdict is
reproducible — same input, same output. Add an LLM-based review
later by composing this with the existing ``grade_response`` tool.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

PLUGIN_NAME = "ponytail"
__version__ = "0.1.0"


class PonytailFinding(BaseModel):
    """A single over-engineering finding."""

    location: str = Field(
        description="File path or 'global'. The string 'global' means the whole diff."
    )
    pattern: str = Field(description="Which lazy-ladder rung the finding came from.")
    snippet: str = Field(description="Short excerpt of the offending code (≤120 chars).")
    fix: str = Field(description="Concrete simplification suggestion.")


class PonytailReviewInput(BaseModel):
    """Input schema for the ``ponytail_review`` tool."""

    diff: str = Field(
        description=(
            "The code, diff, or design proposal to review. Plain text; "
            "the tool does not need a unified diff format — a code block "
            "or design paragraph works the same."
        )
    )
    context: str = Field(
        default="",
        description=(
            "Optional one-line context (e.g. 'this is the new pipeline "
            "orchestrator'). The review only uses it to disambiguate "
            "intentional vs accidental patterns."
        ),
    )


class PonytailReview(BaseModel):
    """Output schema for the ``ponytail_review`` tool."""

    verdict: str = Field(
        description=(
            "'ship' = nothing flagged, ship as-is. "
            "'simplify' = at least one finding, but small. "
            "'over_engineered' = multiple findings or a major one."
        )
    )
    findings: list[PonytailFinding] = Field(default_factory=list)
    lazy_alternative: str = Field(
        default="",
        description=(
            "If the entire diff is over-engineered, a one-paragraph "
            "description of the lazier alternative. Empty when the "
            "diff is fine."
        ),
    )
    # The Ponytail ladder — surfaced in the result so the caller can
    # see the rubric that produced the verdict.
    ladder_used: list[str] = Field(
        default_factory=lambda: [
            "1. Does this need to exist at all? (YAGNI)",
            "2. Already in this codebase? Reuse it.",
            "3. Stdlib does it? Use it.",
            "4. Native platform feature covers it? Use it.",
            "5. Already-installed dependency solves it? Use it.",
            "6. Can it be one line? One line.",
            "7. Only then: the minimum code that works.",
        ]
    )


# ---------------------------------------------------------------------------
# Heuristics. Each is a (pattern_id, regex, fix_suggestion) tuple. Patterns
# are deliberately simple; the LLM (or human) decides whether a hit is
# real. False positives are cheap; false negatives are not.
# ---------------------------------------------------------------------------

_HEURISTICS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "abstract-factory",
        re.compile(r"class\s+\w+(Factory|FactoryImpl|FactoryBase)\b"),
        "Drop the factory. Construct the one implementation directly. "
        "Add indirection back when the second implementation arrives.",
    ),
    (
        "single-impl-interface",
        re.compile(r"class\s+\w+\s*\(.*Protocol.*\):\s*\n\s+\.\.\.|"
                   r"class\s+\w+\s*\(.*ABC.*\):\s*\n\s+pass\s*$", re.MULTILINE),
        "One implementer = no interface. Use the concrete type. "
        "Add the ABC when the second implementer shows up.",
    ),
    (
        "config-for-one-value",
        re.compile(r"config\s*=\s*\{[^}]{0,80}\}", re.DOTALL),
        "Config dict with one entry? Hardcode the value. Promote to "
        "config when the second consumer arrives.",
    ),
    (
        "imported-only-for-type-hint",
        re.compile(r"if\s+TYPE_CHECKING\s*:\s*\n\s+from\s+\S+\s+import\s+"),
        "If the import is for typing only, keep it. If it's used at "
        "runtime, drop the guard. Don't guard actual usage.",
    ),
    (
        "future-todo",
        re.compile(r"#\s*(TODO|FIXME|XXX)\b", re.IGNORECASE),
        "If it's actually needed, do it. If not, delete the marker. "
        "TODO comments rot.",
    ),
    (
        "what-comment",
        re.compile(r"^\s*#\s*(This function|This method|This class|"
                   r"Returns|Set|Get|Helper for|Utility for)\b", re.MULTILINE | re.IGNORECASE),
        "Comment explains what the code already says. Rename the symbol "
        "or delete the comment.",
    ),
    (
        "wrapper-without-delta",
        re.compile(r"def\s+\w+\([^)]*\)\s*->\s*\w+:\s*\n\s+return\s+\w+\([^)]*\)\s*$",
                   re.MULTILINE),
        "One-line passthrough. Inline at the call site. The wrapper "
        "is allowed to add real behavior (logging, retries, "
        "validation) — those are not passthroughs.",
    ),
]


@tool(args_schema=PonytailReviewInput)
def ponytail_review(diff: str, context: str = "") -> PonytailReview:
    """Review a diff, code snippet, or design for over-engineering.

    Use this when the agent (or a reviewer subagent) wants a sanity
    check before producing a long diff. The tool returns a verdict
    (``ship`` / ``simplify`` / ``over_engineered``) plus a list of
    findings, each tied to a rung of the Ponytail ladder. The
    ``# ponytail:`` comment is the OPPOSITE of a finding — it
    marks a deliberate simplification. Ponytail comments are
    preserved, never flagged.

    Args:
        diff: Plain text of the code or design to review. No specific
            format required — a unified diff, a code block, or a
            design paragraph all work.
        context: Optional one-line context (e.g. what the code is for)
            to disambiguate intentional vs accidental patterns.

    Returns:
        A structured review with verdict, findings, an optional
        lazy alternative, and the ladder used.
    """
    findings: list[PonytailFinding] = []
    # Strip out ponytail-marked lines so a deliberate ceiling doesn't
    # count as over-engineering.
    cleaned = "\n".join(
        line for line in diff.splitlines() if "ponytail:" not in line
    )

    for pattern_id, regex, fix in _HEURISTICS:
        for match in regex.finditer(cleaned):
            line_no = cleaned[: match.start()].count("\n") + 1
            snippet = match.group(0).replace("\n", " ")[:120]
            findings.append(
                PonytailFinding(
                    location=f"line {line_no}",
                    pattern=pattern_id,
                    snippet=snippet,
                    fix=fix,
                )
            )

    if not findings:
        verdict = "ship"
        lazy_alternative = ""
    elif len(findings) == 1:
        verdict = "simplify"
        lazy_alternative = ""
    else:
        verdict = "over_engineered"
        # Build a one-paragraph lazy alternative from the worst hit
        worst = max(findings, key=lambda f: len(f.snippet))
        lazy_alternative = (
            f"Worst hit: {worst.pattern} — {worst.fix} "
            f"Apply that, then re-run ponytail_review on the smaller diff."
        )

    return PonytailReview(
        verdict=verdict,
        findings=findings,
        lazy_alternative=lazy_alternative,
    )


def register(api: Any, config: dict | None = None) -> None:
    """Ponytail plugin entry point. Called by ``core.plugin`` at startup.

    Ponytail ignores ``config`` — the heuristic review is fully
    deterministic. The kwarg exists to match the v0.2 plugin
    contract; pass it through so the loader can keep a uniform
    signature. Add a real config knob (e.g. custom pattern allowlist)
    when someone actually needs it.
    """
    cfg_keys = list(config.keys()) if config else []
    api.log(
        f"ponytail {__version__} registering ponytail_review tool"
        + (f" (config={cfg_keys})" if cfg_keys else "")
    )
    api.add_tool(ponytail_review)
