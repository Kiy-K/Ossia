"""Tests for the Tavily-backed web tools.

The Tavily client is mocked at the import boundary (``ossia.tools``) so the
tests run offline and deterministic. The mock returns canned responses
matching Tavily's documented return shape so the tool wrappers can be
exercised end-to-end (search results, URL extraction, Q&A).

Per the Deep Agents deep-research doc, when TAVILY_API_KEY is unavailable
the fallback path is:

- ``fetch_url``: a direct ``httpx`` fetch + BeautifulSoup text extraction
  (``backend="duckduckgo"``); with a ``question`` set, a DDG search
  picks the top hit and fetches it.
- ``qna_search``: a DDG web search whose top snippets are synthesized
  into an answer (``backend="duckduckgo"``).

Tests cover:

1. ``internet_search`` with TAVILY_API_KEY set returns structured results
   from Tavily and a synthesized answer.
2. ``internet_search`` falls back to DuckDuckGo when the key is missing
   and returns the ``backend="duckduckgo"`` marker.
3. ``fetch_url`` returns extracted content for a single URL, with the
   Tavily ``answer`` field when a question is asked.
4. ``fetch_url`` falls back to ``httpx`` + ``bs4`` when the key is missing.
5. ``fetch_url`` falls back to a DDG search + top-hit fetch when the
   key is missing and a ``question`` is set.
6. ``qna_search`` returns a one-shot answer string from Tavily.
7. ``qna_search`` falls back to a DDG-synthesized answer when the key
   is missing.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.tools import fetch_url, internet_search, qna_search

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _FakeTavilyClient:
    """Stand-in for ``TavilyClient`` with canned responses."""

    def __init__(self, *, search_payload=None, extract_payload=None, qna_answer="") -> None:
        self._search_payload = search_payload or {
            "results": [
                {
                    "title": "Sample result",
                    "url": "https://example.com/article",
                    "content": "This is a snippet.",
                    "score": 0.92,
                }
            ],
            "answer": "Tavily's synthesized answer.",
        }
        self._extract_payload = extract_payload or {
            "results": [
                {
                    "url": "https://example.com/article",
                    "title": "Example Article",
                    "raw_content": "# Heading\n\nSome markdown content.",
                }
            ],
            "answer": "Grounded answer from the page.",
        }
        self._qna_answer = qna_answer or "A one-shot answer."

    def search(self, **kwargs: Any) -> dict:
        return self._search_payload

    def extract(self, **kwargs: Any) -> dict:
        return self._extract_payload

    def qna_search(self, **kwargs: Any) -> str:
        return self._qna_answer


@contextmanager
def _patched_tavily(client: _FakeTavilyClient | None, *, key: str = "tvly-test"):
    """Patch ``ossia.tools._get_tavily_client`` to return ``client``.

    When ``client`` is ``None``, the helper also blanks the env-derived
    settings so the production code path falls through to DuckDuckGo or
    raises the loud-fail error.
    """
    if client is None:
        with (
            patch("core.tools._get_tavily_client", return_value=None),        patch("core.config.get_settings") as gs,
            ):
            gs.return_value.tavily_api_key = None
            yield
    else:
        with (
        patch("core.tools._get_tavily_client", return_value=client),
        patch("core.config.get_settings") as gs,
        ):
            gs.return_value.tavily_api_key = key
            yield


# ---------------------------------------------------------------------------
# internet_search
# ---------------------------------------------------------------------------


def test_internet_search_returns_tavily_results() -> None:
    """With TAVILY_API_KEY set, internet_search returns Tavily's payload."""
    client = _FakeTavilyClient()
    with _patched_tavily(client):
        result = internet_search.invoke(
            {"query": "what is X?", "max_results": 5, "topic": "general"}
        )
    assert result.backend == "tavily"
    assert result.query == "what is X?"
    assert len(result.results) == 1
    assert result.results[0].title == "Sample result"
    assert result.results[0].url == "https://example.com/article"
    assert result.results[0].score == 0.92
    assert "synthesized" in result.answer


def test_internet_search_clamps_max_results() -> None:
    """The Pydantic input schema rejects out-of-range ``max_results``."""
    with pytest.raises(ValidationError):
        internet_search.invoke(
            {"query": "x", "max_results": 0, "topic": "general"}
        )


def test_internet_search_falls_back_to_duckduckgo_when_key_missing() -> None:
    """No TAVILY_API_KEY -> DuckDuckGo path; backend='duckduckgo', no answer."""
    canned_ddg = [
        {"title": "DDG result", "href": "https://ddg.example.com", "body": "snippet"}
    ]
    with _patched_tavily(client=None),        patch("core.tools._ddgs_text", return_value=canned_ddg
    ):
        result = internet_search.invoke(
            {"query": "x", "max_results": 3, "topic": "general"}
        )
    assert result.backend == "duckduckgo"
    assert result.answer == ""
    assert len(result.results) == 1
    assert result.results[0].title == "DDG result"
    assert result.results[0].url == "https://ddg.example.com"


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------


def test_fetch_url_returns_extracted_content() -> None:
    client = _FakeTavilyClient()
    with _patched_tavily(client):
        result = fetch_url.invoke(
            {"url": "https://example.com/article", "question": None}
        )
    assert result.backend == "tavily"
    assert result.url == "https://example.com/article"
    assert "markdown" in result.content
    # No question -> no answer.
    assert result.answer == ""


def test_fetch_url_with_question_returns_answer() -> None:
    client = _FakeTavilyClient()
    with _patched_tavily(client):
        result = fetch_url.invoke(
            {"url": "https://example.com/article", "question": "What does it say?"}
        )
    assert result.backend == "tavily"
    assert result.answer == "Grounded answer from the page."
    # Content is also returned for context.
    assert "markdown" in result.content


def test_fetch_url_falls_back_to_httpx_when_key_missing() -> None:
    """No TAVILY_API_KEY -> ``httpx`` + ``bs4`` direct fetch
    (the canonical pattern from the Deep Agents deep-research doc).
    The ``backend`` field is set to ``"duckduckgo"`` to be honest about
    which path served the call (the actual implementation is ``httpx``
    + BeautifulSoup, but ``"duckduckgo"`` is the umbrella name we
    use for the DDG-side fallback family).
    """
    with _patched_tavily(client=None), patch(
        "core.tools._ddg_fetch_url_via_search", return_value="Hello world"
    ) as mock:
        result = fetch_url.invoke(
            {"url": "https://example.com/article", "question": None}
        )
    assert result.backend == "duckduckgo"
    assert result.content == "Hello world"
    assert result.answer == ""
    mock.assert_called_once()
    # No question => no DDG search first; fetch the URL directly.
    assert mock.call_args.kwargs.get("is_query") is False


def test_fetch_url_falls_back_to_ddg_search_when_question_and_no_tavily() -> None:
    """Without Tavily but with a ``question``, the fallback runs a DDG
    search and fetches the top hit. We assert the helper is called with
    ``is_query=True`` and the answer field stays empty (DDG has no
    Q&A primitive).
    """
    with _patched_tavily(client=None), patch(
        "core.tools._ddg_fetch_url_via_search", return_value="page text"
    ) as mock:
        result = fetch_url.invoke(
            {"url": "what is X?", "question": "what is X?"}
        )
    assert result.backend == "duckduckgo"
    assert result.content == "page text"
    assert result.answer == ""
    mock.assert_called_once()
    call_args = mock.call_args
    assert call_args.kwargs.get("is_query") is True


def test_fetch_url_truncates_content() -> None:
    """The output is capped at 4000 chars to keep the model's context lean."""
    long_content = "x" * 10000
    client = _FakeTavilyClient(
        extract_payload={
            "results": [
                {
                    "url": "https://example.com/long",
                    "title": "Long page",
                    "raw_content": long_content,
                }
            ],
            "answer": "",
        }
    )
    with _patched_tavily(client):
        result = fetch_url.invoke(
            {"url": "https://example.com/long", "question": None}
        )
    assert len(result.content) == 4000


# ---------------------------------------------------------------------------
# qna_search
# ---------------------------------------------------------------------------


def test_qna_search_returns_one_shot_answer() -> None:
    client = _FakeTavilyClient(qna_answer="Quick answer.")
    with _patched_tavily(client):
        result = qna_search.invoke({"query": "what is X?", "topic": "general"})
    assert result.backend == "tavily"
    assert result.answer == "Quick answer."
    assert result.query == "what is X?"


def test_qna_search_falls_back_to_ddg_when_key_missing() -> None:
    """No TAVILY_API_KEY -> DDG web search and synthesize an answer from
    the top snippets. The ``backend`` field is set to
    ``"duckduckgo"`` so the caller can see the lower quality path.
    """
    with _patched_tavily(client=None), patch(
        "core.tools._ddg_search_for_answer", return_value="DDG answer"
    ):
        result = qna_search.invoke({"query": "what is X?", "topic": "general"})
    assert result.backend == "duckduckgo"
    assert result.answer == "DDG answer"
