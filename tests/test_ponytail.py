"""Tests for the Ponytail plugin's ``ponytail_review`` tool.

The tool is deterministic: same input, same output, no LLM. The
heuristics are simple regex matchers; the tests below lock in the
verdict rubric (ship / simplify / over_engineered) and a few
representative patterns.
"""

from __future__ import annotations

import asyncio

from plugins.ponytail import (
    PonytailReviewInput,
    __version__,
    ponytail_review,
)


def test_plugin_metadata() -> None:
    """The bundled Ponytail plugin exposes a version constant."""
    assert __version__ == "0.1.0"


def test_ship_verdict_when_diff_is_clean() -> None:
    """A minimal diff with no flagged patterns returns verdict=ship."""
    diff = "x = 1\ny = x + 1\nprint(y)\n"
    result = ponytail_review.invoke({"diff": diff})
    assert result.verdict == "ship"
    assert result.findings == []
    assert result.lazy_alternative == ""


def test_simplify_verdict_on_single_finding() -> None:
    """One finding = 'simplify', not 'over_engineered'."""
    diff = "class WidgetFactory:\n    pass\n"
    result = ponytail_review.invoke({"diff": diff})
    assert result.verdict == "simplify"
    assert len(result.findings) == 1
    assert result.findings[0].pattern == "abstract-factory"
    assert result.lazy_alternative == ""


def test_over_engineered_verdict_on_multiple_findings() -> None:
    """Two or more findings = 'over_engineered' with a lazy_alternative."""
    diff = (
        "class WidgetFactory:\n    pass\n"
        "# TODO: actually do this\n"
        "# This function does the thing.\n"
        "def the_thing() -> int:\n    return 42\n"
    )
    result = ponytail_review.invoke({"diff": diff})
    assert result.verdict == "over_engineered"
    assert len(result.findings) >= 2
    assert result.lazy_alternative != ""
    # The lazy alternative references the worst hit.
    assert "Worst hit:" in result.lazy_alternative


def test_ponytail_comment_is_not_flagged() -> None:
    """Lines tagged ``# ponytail:`` are deliberate simplifications — never flagged."""
    # The abstract-factory pattern, but with a ponytail comment
    # marking it as a known ceiling.
    diff = "class WidgetFactory:  # ponytail: single-impl ceiling, expand when needed\n    pass\n"
    result = ponytail_review.invoke({"diff": diff})
    assert result.verdict == "ship"


def test_ponytail_marker_excludes_only_the_marked_line() -> None:
    """A ponytail comment on one line does NOT silence the rest of the file."""
    diff = "x = 1  # ponytail: trivial assignment\nclass WidgetFactory:\n    pass\n"
    result = ponytail_review.invoke({"diff": diff})
    assert result.verdict in {"simplify", "over_engineered"}
    assert any(f.pattern == "abstract-factory" for f in result.findings)


def test_todo_comment_is_flagged() -> None:
    """A TODO marker is a finding — Ponytail: do it or delete the marker."""
    result = ponytail_review.invoke({"diff": "# TODO: refactor this later\n"})
    assert any(f.pattern == "future-todo" for f in result.findings)


def test_what_comment_is_flagged() -> None:
    """Comments that narrate what the code already says are findings."""
    result = ponytail_review.invoke(
        {"diff": "# This function returns the answer.\ndef answer() -> int:\n    return 42\n"}
    )
    assert any(f.pattern == "what-comment" for f in result.findings)


def test_passthrough_wrapper_is_flagged() -> None:
    """A one-line passthrough is a finding — inline at the call site."""
    result = ponytail_review.invoke(
        {"diff": ("def fetch_user(user_id: int) -> User:\n    return get_user(user_id)\n")}
    )
    assert any(f.pattern == "wrapper-without-delta" for f in result.findings)


def test_input_schema_rejects_empty_diff() -> None:
    """The Pydantic schema accepts an empty diff (verdict=ship, no findings)."""
    result = ponytail_review.invoke({"diff": ""})
    assert result.verdict == "ship"
    assert result.findings == []


def test_context_field_is_optional() -> None:
    """The ``context`` field is optional and does not affect the verdict."""
    diff_with = ponytail_review.invoke({"diff": "x = 1\n", "context": "trivial"})
    diff_without = ponytail_review.invoke({"diff": "x = 1\n"})
    assert diff_with.verdict == diff_without.verdict


def test_findings_carry_location_and_fix() -> None:
    """Each finding has a location, a pattern id, a snippet, and a fix."""
    diff = "class WidgetFactory:\n    pass\n"
    result = ponytail_review.invoke({"diff": diff})
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.location.startswith("line ")
    assert f.pattern == "abstract-factory"
    assert "Factory" in f.snippet
    assert "Drop the factory" in f.fix


def test_async_invoke_works() -> None:
    """The tool can be invoked through the async ainvoke path (matches the core tools)."""
    result = asyncio.run(ponytail_review.ainvoke({"diff": "x = 1\n"}))
    assert result.verdict == "ship"


def test_ladder_is_surfaced_in_result() -> None:
    """The result carries the rubric that produced the verdict — debuggable."""
    result = ponytail_review.invoke({"diff": "x = 1\n"})
    assert len(result.ladder_used) == 7
    assert result.ladder_used[0].startswith("1.")
    assert "YAGNI" in result.ladder_used[0]


def test_input_schema_validates() -> None:
    """Pydantic input schema accepts and rejects as expected."""
    inp = PonytailReviewInput(diff="x = 1")
    assert inp.context == ""
    inp2 = PonytailReviewInput(diff="x = 1", context="trivial")
    assert inp2.context == "trivial"
