"""Tools for knowledge base search, web fallback, grading, and final actions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ddgs import DDGS
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """Result from a knowledge base or web search."""

    title: str = Field(description="Document title.")
    source: str = Field(description="Document source URL or KB id.")
    content: str = Field(description="Relevant snippet or summary.")
    score: float = Field(default=0.0, description="Relevance score if available.")


class KBSearchInput(BaseModel):
    """Input schema for knowledge base search."""

    query: str = Field(description="User query to search against the knowledge base.")
    top_k: int = Field(default=3, ge=1, le=10, description="Number of results to return.")


class KBSearchOutput(BaseModel):
    """Output schema for knowledge base search."""

    results: list[SearchResult]
    fallback_used: bool = Field(default=False, description="True if KB was empty and web fallback was used.")
    reasoning: str = Field(default="", description="Short explanation of how results were obtained.")


@dataclass
class KnowledgeBase:
    """In-memory knowledge base for local development and tests."""

    documents: list[dict[str, Any]]

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Return documents whose content contains any query token."""
        tokens = query.lower().split()
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in self.documents:
            text = doc.get("content", "").lower()
            matches = sum(1 for token in tokens if token in text)
            if matches:
                scored.append((matches, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchResult(
                title=doc.get("title", "Untitled"),
                source=doc.get("source", "kb"),
                content=doc.get("content", "")[:800],
                score=float(score),
            )
            for score, doc in scored[:top_k]
        ]


def create_kb(documents: list[dict[str, Any]] | None = None) -> KnowledgeBase:
    """Create a knowledge base with optional seed documents."""
    if documents is not None:
        return KnowledgeBase(documents=documents)
    return _default_kb()


@lru_cache(maxsize=1)
def _default_kb() -> KnowledgeBase:
    """Build and cache the default seed knowledge base."""
    default_docs: list[dict[str, Any]] = [
        {
            "title": "Nebius Serverless Endpoints",
            "source": "https://docs.nebius.com/serverless/endpoints",
            "content": (
                "Nebius Serverless Endpoints provide on-demand GPU inference for "
                "LLMs. You pay only for compute time. Cold starts are handled by "
                "keeping a configurable minimum number of containers warm. Use "
                "vLLM for OpenAI-compatible serving."
            ),
        },
        {
            "title": "Nebius Serverless Jobs",
            "source": "https://docs.nebius.com/serverless/jobs",
            "content": (
                "Nebius Serverless Jobs run batch or training workloads. Jobs are "
                "ideal for fine-tuning, evaluation pipelines, and offline inference. "
                "They start from a container image and can mount object storage."
            ),
        },
        {
            "title": "Resetting endpoint credentials",
            "source": "https://docs.nebius.com/serverless/troubleshooting",
            "content": (
                "If you lose endpoint credentials, regenerate the API key from the "
                "Nebius console under the endpoint's 'Access' tab. Update your "
                "application's environment variable and restart the deployment."
            ),
        },
    ]
    return KnowledgeBase(documents=default_docs)


@tool(args_schema=KBSearchInput)
async def search_knowledge_base(query: str, top_k: int = 3) -> KBSearchOutput:
    """Search the local knowledge base for internal documentation, known issues, and troubleshooting guides.

    Use this when the user's question is about project-specific topics:
    deployment guides, known bugs, setup instructions, or internal conventions.
    Falls back to DuckDuckGo web search if the KB returns no matches.
    For broad external research, prefer ``internet_search`` instead.

    Args:
        query: User query to search.
        top_k: Number of results to return.

    Returns:
        Knowledge base results or web fallback results.
    """
    kb = create_kb()
    results = kb.search(query, top_k=top_k)
    if results:
        return KBSearchOutput(results=results, fallback_used=False, reasoning="Matched documents in the local knowledge base.")
    try:
        web_results = await asyncio.to_thread(_ddgs_text, query, top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web search fallback failed for query %r: %s", query, exc)
        return KBSearchOutput(results=[], fallback_used=True, reasoning="No KB match and web search failed; will use model internal knowledge.")
    if not web_results:
        return KBSearchOutput(results=[], fallback_used=True, reasoning="No KB match and no web results; will use model internal knowledge.")
    return KBSearchOutput(
        results=[
            SearchResult(
                title=res.get("title", "Web result"),
                source=res.get("href", "https://duckduckgo.com"),
                content=res.get("body", "")[:800],
                score=0.5,
            )
            for res in web_results
        ],
        fallback_used=True,
        reasoning="KB returned no matches; used DuckDuckGo web search fallback.",
    )


def _ddgs_text(query: str, max_results: int) -> list[dict[str, Any]]:
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def _ddgs_news(query: str, max_results: int) -> list[dict[str, Any]]:
    with DDGS() as ddgs:
        return list(ddgs.news(query, max_results=max_results))


def _fetch_url_text(url: str, *, timeout: float = 10.0, max_chars: int = 4000) -> str:
    """Fetch a URL and return plain text extracted via BeautifulSoup.

    Mirrors the canonical pattern from the Deep Agents deep-research
    doc (``fetch_webpage_content``), but uses ``bs4`` rather than
    ``markdownify`` to avoid adding a new dependency. Plain text is
    fine for the model's purposes; markdown is a refinement we
    can layer on later.

    Args:
        url: URL to fetch.
        timeout: HTTP timeout in seconds.
        max_chars: Maximum length of the returned text.

    Returns:
        Extracted text truncated to ``max_chars``. On any error,
        a short ``"Error fetching ..."`` message.
    """
    import httpx
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        return f"Error fetching {url}: {exc!s}"


def _ddg_search_for_answer(query: str, max_results: int = 3) -> str:
    """Run a DDG search and return a synthesized answer from the top hits.

    Used as the fallback path for ``qna_search`` when Tavily is
    unavailable. DDG has no native Q&A primitive, so we approximate
    one with the top search snippets. Output is clearly tagged
    ``backend="duckduckgo"`` so the caller can see the source.

    Args:
        query: Question to search.
        max_results: Number of results to include in the synthesis.

    Returns:
        A plain-text answer assembled from the top DDG snippets.
        Empty string when DDG returns no results.
    """
    try:
        results = _ddgs_text(query, max_results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DDG search failed: %s", exc)
        return ""
    if not results:
        return ""
    parts: list[str] = []
    for res in results:
        title = res.get("title", "")
        body = res.get("body", "")[:400]
        if title or body:
            parts.append(f"{title}\n{body}".strip())
    return "\n\n---\n\n".join(parts)[:2000]


def _ddg_fetch_url_via_search(
    url_or_query: str, *, is_query: bool = False, max_chars: int = 4000
) -> str:
    """Fallback for ``fetch_url`` when Tavily is unavailable.

    Two modes:
      - Direct: ``url_or_query`` is a URL; we fetch it via ``httpx``.
      - Search-then-fetch: ``url_or_query`` is a question; we run
        a DDG search and fetch the top hit.

    Args:
        url_or_query: URL or search query depending on ``is_query``.
        is_query: When True, treat input as a search query and fetch
            the top DDG result. When False, treat as a direct URL.
        max_chars: Maximum length of the returned text.

    Returns:
        Plain text (or the relevant snippet), truncated to
        ``max_chars``. Empty string on failure.
    """
    url = url_or_query
    if is_query:
        try:
            results = _ddgs_text(url_or_query, 3)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DDG search for fetch_url fallback failed: %s", exc)
            return ""
        if not results:
            return ""
        url = results[0].get("href", "")
        if not url:
            return ""
    return _fetch_url_text(url, max_chars=max_chars)


class GradeInput(BaseModel):
    query: str = Field(description="Original user query.")
    response: str = Field(description="Draft response to evaluate.")
    context: str = Field(default="", description="Retrieved context used for the response.")


class GradeOutput(BaseModel):
    passes: bool = Field(description="Whether the response meets quality standards.")
    score: float = Field(ge=0.0, le=1.0, description="Quality score.")
    feedback: str = Field(description="Concise feedback for revision or approval.")


@tool(args_schema=GradeInput)
def grade_response(
    query: str,
    response: str,
    context: str = "",
    runtime: Any = None,
) -> GradeOutput:
    """Self-check the quality of a draft response before submitting it for human review.

    Use this after drafting an answer to verify it meets quality standards:
    sufficient length, relevance to the original query, and grounding in
    retrieved context. If the grade is low, revise the response and re-grade.
    The revision loop is capped at 3 attempts by middleware.

    Args:
        query: Original user query.
        response: Draft response to grade.
        context: Retrieved context used to produce the response.
        runtime: Injected by deepagents; not part of the model's
            argument schema. Carries ``runtime.context`` (the
            ``OssiaContext`` for this call) and ``runtime.store``.

    Returns:
        Grade output with pass/fail decision and feedback.
    """
    caller = None
    if runtime is not None:
        ctx = getattr(runtime, "context", None)
        caller = getattr(ctx, "caller", None) if ctx is not None else None
    if caller:
        logger.debug("grade_response for caller=%s", caller)
    checks = 0
    total = 3
    if len(response) >= 40:
        checks += 1
    if any(token in response.lower() for token in query.lower().split() if len(token) > 3):
        checks += 1
    if context and not context.strip().startswith("No"):
        checks += 1
    score = checks / total
    return GradeOutput(
        passes=score >= 0.67,
        score=score,
        feedback=("Response looks acceptable." if score >= 0.67 else "Response needs more detail."),
    )


class SendResponseInput(BaseModel):
    response: str = Field(description="Final response text to deliver.")
    channel: str = Field(default="chat", description="Delivery channel.")


class SendResponseOutput(BaseModel):
    sent: bool = Field(description="Whether the response was delivered.")
    message: str = Field(description="Confirmation message.")


@tool(args_schema=SendResponseInput)
def send_response(response: str, channel: str = "chat") -> SendResponseOutput:
    """Deliver the final approved response to the user after grading and human review.

    Use this after the response has passed ``grade_response`` and,
    when human review is enabled, received explicit approval from the user.
    Calling this tool triggers an interrupt for approval when
    ``interrupt_on={"send_response": True}`` is configured.

    Args:
        response: Final response text.
        channel: Delivery channel identifier.

    Returns:
        Confirmation of delivery.
    """
    return SendResponseOutput(sent=True, message=f"Response delivered via {channel}: {response[:120]}...")


# ── Dev-concierge stubs ──────────────────────────────────────────────────────
# Per CONTEXT.md §2, the dev-concierge path is the active runtime mode.
# These stubs provide typing and a clear handoff for real implementations.


class FetchIssueInput(BaseModel):
    """Input schema for fetching a GitHub issue / PR."""

    repo: str = Field(description="Owner/repo, e.g. 'octocat/Hello-World'.")
    issue_number: int = Field(description="Issue or PR number.")


class FetchIssueOutput(BaseModel):
    """Output schema for fetching a GitHub issue / PR."""

    number: int = Field(description="Issue / PR number.")
    title: str = Field(description="Title.")
    body: str = Field(description="Issue or PR body text.")
    state: str = Field(description="open / closed.")
    url: str = Field(description="HTML URL on GitHub.")


@tool(args_schema=FetchIssueInput)
def fetch_issue(repo: str, issue_number: int) -> FetchIssueOutput:
    """Fetch a GitHub issue or pull request by repository and issue number.

    Use this when the user references a specific GitHub issue or PR
    (e.g. "#42" or "octocat/Hello-World#5") and you need the title,
    body, and current state to understand the context.

    TODO (CONTEXT.md §8 item 2): wire to GitHub API with authentication.

    Args:
        repo: ``owner/name`` repository string.
        issue_number: Numeric issue or PR identifier.

    Returns:
        Fetch result with metadata and body text.
    """
    logger.info("fetch_issue stub: repo=%r number=%d", repo, issue_number)
    return FetchIssueOutput(
        number=issue_number,
        title="[STUB] Issue title",
        body="[STUB] Issue body — replace with GitHub REST call.",
        state="open",
        url=f"https://github.com/{repo}/issues/{issue_number}",
    )


class SearchCodebaseInput(BaseModel):
    """Input schema for local code-base search."""

    query: str = Field(description="Search query, e.g. a function name or error string.")
    path: str = Field(default=".", description="Root directory to search.")


class SearchCodebaseOutput(BaseModel):
    """Output schema for local code-base search."""

    matches: list[str] = Field(description="Matching file paths with snippets.")


@tool(args_schema=SearchCodebaseInput)
def search_codebase(query: str, path: str = ".") -> SearchCodebaseOutput:
    """Search the local project codebase for code tokens, symbols, error strings, or file patterns.

    Use this when you need to find where a function is defined, locate
    usages of a variable, find error-handling code matching an error
    message, or discover file paths related to a feature. Prefer this
    over ``internet_search`` for anything inside the project.

    TODO (CONTEXT.md §8 item 2): implement with ripgrep or embedding search.

    Args:
        query: Search term — function name, error string, or symbol.
        path: Root directory to search within.

    Returns:
        List of matched file paths and surrounding context.
    """
    logger.info("search_codebase stub: query=%r path=%r", query, path)
    return SearchCodebaseOutput(
        matches=[f"[STUB] No real search yet — query={query}, path={path}"]
    )


class RunTestsInput(BaseModel):
    """Input schema for sandbox test execution."""

    path: str = Field(default="tests/", description="Test path or file.")
    command: str = Field(default="pytest", description="Test runner command.")


class RunTestsOutput(BaseModel):
    """Output schema for sandbox test execution."""

    passed: bool = Field(description="Whether all tests passed.")
    output: str = Field(description="Captured test runner output.")


@tool(args_schema=RunTestsInput)
def run_tests(path: str = "tests/", command: str = "pytest") -> RunTestsOutput:
    """Run tests to verify code changes don't break existing functionality.

    Use this after proposing or applying a code change to get empirical
    evidence the change is safe. Also use it to investigate a user-reported
    failure by running the specific test path they mention.

    TODO (CONTEXT.md §8 item 2): wire to a sandbox (Daytona / Docker / modal).

    Args:
        path: Root test directory or single file.
        command: Test runner executable and args.

    Returns:
        Pass/fail flag with stdout/stderr capture.
    """
    logger.info("run_tests stub: path=%r command=%r", path, command)
    return RunTestsOutput(
        passed=True,
        output=f"[STUB] No real sandbox yet — would run `{command} {path}`.",
    )


class ProposeFixInput(BaseModel):
    """Input schema for proposing a code fix."""

    issue_description: str = Field(description="Summary of the bug or issue.")
    file_path: str = Field(description="Target file to modify, if known.")


class ProposeFixOutput(BaseModel):
    """Output schema for proposing a code fix."""

    summary: str = Field(description="One-line description of the proposed fix.")
    patch: str = Field(default="", description="Unified diff patch or code snippet.")


@tool(args_schema=ProposeFixInput)
def propose_fix(issue_description: str, file_path: str = "") -> ProposeFixOutput:
    """Produce a concrete code fix suggestion from a diagnosed bug or issue description.

    Use this after ``bug-diagnostician`` or ``run_bugfix_pipeline`` has
    identified the root cause. Returns a patch summary and optional unified
    diff that the main agent can review before applying.

    TODO (CONTEXT.md §6): implement with LLM-generated patch or RAG retrieval.

    Args:
        issue_description: Plain-text bug / feature description from triage.
        file_path: Optional target file the agent should modify.

    Returns:
        Fix summary and optional patch.
    """
    logger.info("propose_fix stub: file=%r desc=%r", file_path, issue_description[:50])
    return ProposeFixOutput(
        summary=f"[STUB] Proposed fix for {'file ' + file_path if file_path else 'issue'}.",
        patch="",
    )


class CreatePROutput(BaseModel):
    """Output schema for opening a GitHub pull request."""

    url: str = Field(description="PR HTML URL.")
    number: int = Field(description="PR number.")


@tool
def create_pr(
    repo: str,
    title: str,
    body: str = "",
    head: str = "",
    base: str = "main",
) -> CreatePROutput:
    """Create a GitHub pull request to propose code changes for review.

    Use this when the fix or feature has been tested and the user asks to
    submit a pull request. Requires a source branch (``head``) with the
    changes already committed and pushed.

    TODO (CONTEXT.md §8 item 2): wire to GitHub REST API with auth + branch
    creation, push, and PR creation.

    Args:
        repo: ``owner/name`` repository string.
        title: Pull request title.
        body: PR description body.
        head: Source branch containing changes.
        base: Target branch (default ``main``).

    Returns:
        Created PR URL and number.
    """
    logger.info("create_pr stub: repo=%r head=%s->%s", repo, head, base)
    return CreatePROutput(
        url=f"https://github.com/{repo}/pull/0",
        number=0,
    )


# ── Web search and URL extraction (Tavily-backed) ────────────────────────────
# Per the Deep Agents tools doc: pass any callable directly to ``tools=``;
# Deep Agents infers the schema from the function signature and docstring.
# The three tools below wrap ``tavily.TavilyClient`` (the canonical pattern
# from the docs) and are wired in ``create_core_tools`` below.
#
# Tavily's client is created lazily because the import is heavy and the
# API key may be absent (we degrade to DuckDuckGo for ``search_knowledge_base``,
# and fail loudly for URL fetches that need a real backend).


def _get_tavily_client():
    """Return a ``TavilyClient`` for the current TAVILY_API_KEY, or ``None``."""
    from tavily import TavilyClient

    from core.config import get_settings

    key = get_settings().tavily_api_key
    if not key:
        return None
    return TavilyClient(api_key=key)


# ─── internet_search ────────────────────────────────────────────────────────
# Mirrors the Deep Agents tools doc example: a plain function with typed
# args, a docstring, and Tavily's ``search()`` returning structured results.


class InternetSearchInput(BaseModel):
    """Input schema for ``internet_search``."""

    query: str = Field(description="Natural-language search query.")
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Number of search results to return.",
    )
    topic: str = Field(
        default="general",
        description="Tavily topic: 'general' | 'news' | 'finance'.",
    )


class InternetSearchResult(BaseModel):
    """One result from ``internet_search``."""

    title: str = Field(description="Page title.")
    url: str = Field(description="Page URL.")
    content: str = Field(description="Relevant snippet from the page.")
    score: float = Field(default=0.0, description="Tavily relevance score.")


class InternetSearchOutput(BaseModel):
    """Output schema for ``internet_search``."""

    query: str
    results: list[InternetSearchResult]
    answer: str = Field(
        default="",
        description="Tavily's synthesized answer when include_answer is set.",
    )
    backend: str = Field(
        description="Which backend served the query: 'tavily' or 'duckduckgo'.",
    )


@tool(args_schema=InternetSearchInput)
def internet_search(
    query: str,
    max_results: int = 5,
    topic: str = "general",
) -> InternetSearchOutput:
    """Run a web search via Tavily and return structured results.

    Use this when the model needs information that is not in the
    knowledge base: external API docs, recent releases, vendor
    announcements, etc. Returns up to ``max_results`` (default 5)
    results plus a synthesized answer when Tavily can produce one.

    When TAVILY_API_KEY is not set, falls back to DuckDuckGo. The
    fallback is best-effort and does not return an ``answer`` field.

    Args:
        query: Natural-language search query.
        max_results: Number of results to return (1-10).
        topic: Search topic — 'general', 'news', or 'finance'.

    Returns:
        ``InternetSearchOutput`` with results, an optional synthesized
        answer, and the backend that served the query.
    """
    client = _get_tavily_client()
    if client is None:
        try:
            ddg = _ddgs_text(query, max_results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily unavailable and DuckDuckGo failed: %s", exc)
            return InternetSearchOutput(query=query, results=[], backend="duckduckgo")
        return InternetSearchOutput(
            query=query,
            results=[
                InternetSearchResult(
                    title=res.get("title", "Web result"),
                    url=res.get("href", ""),
                    content=res.get("body", "")[:800],
                    score=0.5,
                )
                for res in ddg
            ],
            backend="duckduckgo",
        )
    try:
        raw = client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_answer="basic",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily search failed: %s", exc)
        return InternetSearchOutput(query=query, results=[], backend="tavily")
    return InternetSearchOutput(
        query=query,
        results=[
            InternetSearchResult(
                title=res.get("title", ""),
                url=res.get("url", ""),
                content=res.get("content", "")[:800],
                score=float(res.get("score", 0.0) or 0.0),
            )
            for res in raw.get("results", [])
        ],
        answer=raw.get("answer", "") or "",
        backend="tavily",
    )


# ─── fetch_url ──────────────────────────────────────────────────────────────
# Wraps Tavily's ``extract`` endpoint. The optional ``question`` parameter
# turns extraction into a focused Q&A: Tavily returns a one-shot answer
# to the question grounded in the page content.


class FetchUrlInput(BaseModel):
    """Input schema for ``fetch_url``."""

    url: str = Field(description="Page URL to extract content from.")
    question: str | None = Field(
        default=None,
        description=(
            "Optional focused question. When set, the tool returns "
            "Tavily's answer to the question grounded in the page."
        ),
    )


class FetchUrlOutput(BaseModel):
    """Output schema for ``fetch_url``."""

    url: str
    title: str = ""
    content: str = Field(
        default="",
        description="Extracted page content (markdown), truncated to 4000 chars.",
    )
    answer: str = Field(
        default="",
        description="Tavily's answer to ``question`` when set; empty otherwise.",
    )
    backend: str = Field(description="'tavily' (only supported backend).")


@tool(args_schema=FetchUrlInput)
def fetch_url(url: str, question: str | None = None) -> FetchUrlOutput:
    """Fetch a URL and return its content (or answer a question about it).

    Use this when the model has a specific page in mind: an issue
    tracker URL, a docs page, a blog post. With no ``question`` the
    tool returns the page content as markdown. With ``question`` it
    returns Tavily's grounded answer (and the content for context).

    Backed by Tavily's ``extract`` endpoint. When ``TAVILY_API_KEY``
    is unset, falls back to a direct ``httpx`` fetch (``backend=
    "duckduckgo"``) — the content quality is lower (plain text via
    BeautifulSoup, no Q&A) but the tool still works. When ``question``
    is set without Tavily, the fallback runs a DDG search and fetches
    the top hit, then returns the page content with no synthesized
    answer (DDG has no Q&A primitive).

    Args:
        url: URL to extract content from. When ``question`` is also
            set and Tavily is unavailable, ``url`` is treated as a
            search query and the top DDG hit is fetched instead.
        question: Optional focused question.

    Returns:
        ``FetchUrlOutput`` with content, optional answer, and backend
        name (``"tavily"`` or ``"duckduckgo"``).
    """
    client = _get_tavily_client()
    if client is None:
        # No Tavily: use httpx + bs4 directly. With a question, search
        # DDG first and fetch the top hit.
        is_query = bool(question)
        content = _ddg_fetch_url_via_search(url, is_query=is_query)
        return FetchUrlOutput(
            url=url,
            title="",
            content=content,
            answer="",
            backend="duckduckgo",
        )
    try:
        raw = client.extract(
            urls=[url],
            format="markdown",
            query=question or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily extract failed for %s: %s", url, exc)
        return FetchUrlOutput(url=url, content="", answer="", backend="tavily")
    # Tavily returns a list of results keyed by the input URL; when
    # ``query`` is set there is also a top-level ``answer`` field.
    results = raw.get("results", []) if isinstance(raw, dict) else []
    page = results[0] if results else {}
    full = page.get("raw_content", "") or page.get("content", "")
    content = (full or "")[:4000]
    answer = raw.get("answer", "") if question else ""
    return FetchUrlOutput(
        url=url,
        title=page.get("title", ""),
        content=content,
        answer=answer or "",
        backend="tavily",
    )


# ─── qna_search ─────────────────────────────────────────────────────────────
# One-shot Q&A: the model asks a question, Tavily returns a string answer.
# Useful for "what is X?" patterns where the model just wants a one-line
# answer, not a list of citations to wade through.


class QnaSearchInput(BaseModel):
    """Input schema for ``qna_search``."""

    query: str = Field(description="Natural-language question.")
    topic: str = Field(
        default="general",
        description="Tavily topic: 'general' | 'news' | 'finance'.",
    )


class QnaSearchOutput(BaseModel):
    """Output schema for ``qna_search``."""

    query: str
    answer: str
    backend: str = Field(description="'tavily' (only supported backend).")


@tool(args_schema=QnaSearchInput)
def qna_search(query: str, topic: str = "general") -> QnaSearchOutput:
    """Get a one-shot answer to a natural-language question.

    Use this for "what is X?" style questions where a list of search
    results would be overkill — the answer is a single string, no
    citations to traverse. Backed by Tavily's ``qna_search`` endpoint.

    When ``TAVILY_API_KEY`` is unset, falls back to a DDG web search
    and synthesizes an answer from the top snippets. The fallback is
    clearly tagged ``backend="duckduckgo"`` so the caller knows the
    answer quality may be lower than the Tavily path.

    Args:
        query: Natural-language question.
        topic: 'general' | 'news' | 'finance'.

    Returns:
        ``QnaSearchOutput`` with the answer string and backend name
        (``"tavily"`` or ``"duckduckgo"``).
    """
    client = _get_tavily_client()
    if client is None:
        answer = _ddg_search_for_answer(query, max_results=3)
        return QnaSearchOutput(
            query=query,
            answer=answer,
            backend="duckduckgo",
        )
    try:
        answer = client.qna_search(query=query, topic=topic)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily qna_search failed: %s", exc)
        return QnaSearchOutput(query=query, answer="", backend="tavily")
    return QnaSearchOutput(query=query, answer=str(answer or ""), backend="tavily")
