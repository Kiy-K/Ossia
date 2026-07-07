"""Tools for knowledge base search, web fallback, grading, and final actions."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from ddgs import DDGS
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from core.kb_loader import read_kb_from_redis, search_redis_kb
from core.redis_client import get_async_redis

logger = logging.getLogger(__name__)

# Ponytail: shared caps for the dev-concierge tools so a runaway
# search / test run cannot take down the agent. The values are
# deliberately conservative — a real dev session fits comfortably.
_RG_TIMEOUT_S = 30
_RG_MAX_MATCHES = 50
_TEST_TIMEOUT_S = 300
_TEST_MAX_OUTPUT_BYTES = 50_000
_GITHUB_TIMEOUT_S = 15
_GITHUB_API_BASE = "https://api.github.com"


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
    fallback_used: bool = Field(
        default=False, description="True if KB was empty and web fallback was used."
    )
    reasoning: str = Field(
        default="", description="Short explanation of how results were obtained."
    )


# Ponytail: simple proportion-based ranking with length penalty.
# BM25-lite without IDF — good for KBs of <1000 docs. When the corpus
# grows, switch to BM25 with proper IDF or embed-and-ANN search.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


@dataclass
class KnowledgeBase:
    """In-memory knowledge base snapshot.

    Constructed from a list of document dicts (``title``, ``source``,
    ``content``). For the live tool, instances are built once per
    process from the Redis snapshot and cached.
    """

    documents: list[dict[str, Any]] = field(default_factory=list)
    avg_doc_length: float = 1.0

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Return documents ranked by term-overlap proportion with a
        mild length penalty.

        Ranking: ``score = overlap / (1 + log(doc_len / avg_doc_len))``
        where ``overlap`` is the number of distinct query tokens that
        appear at least once in the document. Documents with zero
        overlap are dropped.
        """
        if not self.documents:
            return []
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        query_set = set(query_tokens)
        scored: list[tuple[float, dict[str, Any]]] = []
        for doc in self.documents:
            doc_tokens = _tokenize(doc.get("content", ""))
            if not doc_tokens:
                continue
            doc_set = set(doc_tokens)
            overlap = len(query_set & doc_set)
            if overlap == 0:
                continue
            ratio = overlap / len(query_set)
            len_penalty = 1.0 + math.log(max(1.0, len(doc_tokens) / self.avg_doc_length))
            scored.append((ratio / len_penalty, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchResult(
                title=doc.get("title", "Untitled"),
                source=doc.get("source", "kb"),
                content=doc.get("content", "")[:800],
                score=round(score, 4),
            )
            for score, doc in scored[:top_k]
        ]


def _build_kb(docs: list[dict[str, Any]]) -> KnowledgeBase:
    """Construct a KnowledgeBase from a list of doc dicts."""
    avg_len = sum(len(_tokenize(d.get("content", ""))) for d in docs) / max(1, len(docs))
    return KnowledgeBase(documents=list(docs), avg_doc_length=max(1.0, avg_len))


# Process-local KB cache. Rebuilt on first call after the lifespan
# loader populates Redis, and on explicit ``reset_kb_cache()``.
_kb_cache: KnowledgeBase | None = None


async def create_kb(
    documents: list[dict[str, Any]] | None = None,
) -> KnowledgeBase:
    """Return the live KB, building it from Redis on first call.

    When ``documents`` is provided, build a one-off KB from those
    (used by tests that don't want to mock Redis). Otherwise, read
    the snapshot from Redis and cache it for the rest of the
    process. Returns an empty KB when Redis is unset.
    """
    global _kb_cache
    if documents is not None:
        return _build_kb(documents)
    if _kb_cache is None:
        docs = await read_kb_from_redis(get_async_redis())
        _kb_cache = _build_kb(docs)
    return _kb_cache


@tool(args_schema=KBSearchInput)
async def search_knowledge_base(query: str, top_k: int = 3) -> KBSearchOutput:
    """Search the local knowledge base for internal documentation.

    Use this when the user's question is about project-specific
    topics: deployment guides, known bugs, setup instructions, or
    internal conventions configured via ``KB_SOURCE_URLS``. Falls
    back to DuckDuckGo web search when the KB is empty or returns
    no matches. For broad external research, prefer
    ``internet_search`` instead.

    The KB is loaded at startup from the URLs in
    ``Settings.kb_source_urls``; each URL (or each entry in a JSON
    manifest) becomes one document. The agent reads from the
    process-local snapshot — Redis is the source of truth, the
    snapshot is the read cache.

    When the RediSearch index is available, the read path uses
    ``FT.SEARCH`` for sub-ms server-side ranking. Falls back to
    the in-process proportion search when the index is missing,
    the RediSearch module is not loaded, or Redis is unset.

    Args:
        query: User query to search.
        top_k: Number of results to return.

    Returns:
        Knowledge base results or web fallback results.
    """
    # Fast path: RediSearch server-side ranking. Returns None on
    # any failure (no index, no module, no client) → fall through
    # to the in-process search.
    redis_client = get_async_redis()
    redis_hits = await search_redis_kb(redis_client, query, top_k)
    if redis_hits:
        return KBSearchOutput(
            results=[
                SearchResult(
                    title=hit["title"],
                    source=hit["source"],
                    content=hit["content"],
                    # RediSearch without WITHSCORES does not
                    # return a score; leave 0.0 to signal the
                    # caller. Add WITHSCORES + a parser if a
                    # numeric score becomes useful.
                    score=0.0,
                )
                for hit in redis_hits
            ],
            fallback_used=False,
            reasoning=(f"Matched {len(redis_hits)} document(s) via RediSearch."),
        )
    # Slow path: in-process ranking. Same shape, slower for large
    # KBs because every doc is loaded and tokenized on the agent
    # host.
    kb = await create_kb()
    results = kb.search(query, top_k=top_k)
    if results:
        return KBSearchOutput(
            results=results,
            fallback_used=False,
            reasoning=f"Matched {len(results)} document(s) in the local knowledge base.",
        )
    try:
        web_results = await asyncio.to_thread(_ddgs_text, query, top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web search fallback failed for query %r: %s", query, exc)
        return KBSearchOutput(
            results=[],
            fallback_used=True,
            reasoning="No KB match and web search failed; will use model internal knowledge.",
        )
    if not web_results:
        return KBSearchOutput(
            results=[],
            fallback_used=True,
            reasoning="No KB match and no web results; will use model internal knowledge.",
        )
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
        return str(text)[:max_chars]
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
    return SendResponseOutput(
        sent=True, message=f"Response delivered via {channel}: {response[:120]}..."
    )


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


def _github_headers() -> dict[str, str]:
    """Headers for GitHub REST calls. Adds the token if set.

    Honors ``$GITHUB_TOKEN`` (and ``$GH_TOKEN`` for users who copy
    that from the ``gh`` CLI). Authenticated calls get a 5000 req/h
    budget; unauthenticated get only 60.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ossia-dev-concierge",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _validate_github_repo(repo: str) -> tuple[str, str] | None:
    """Return ``(owner, name)`` for a valid ``owner/name`` string, else None."""
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


@tool(args_schema=FetchIssueInput)
def fetch_issue(repo: str, issue_number: int) -> FetchIssueOutput:
    """Fetch a GitHub issue or pull request by repository and issue number.

    Use this when the user references a specific GitHub issue or PR
    (e.g. "#42" or "octocat/Hello-World#5") and you need the title,
    body, and current state to understand the context.

    Authenticated when ``$GITHUB_TOKEN`` (or ``$GH_TOKEN``) is set —
    the agent can read private repos the token has access to.

    Args:
        repo: ``owner/name`` repository string.
        issue_number: Numeric issue or PR identifier.

    Returns:
        Fetch result with metadata and body text. On HTTP error,
        returns a placeholder object with the URL and an error note
        in the body — the agent can decide whether to retry or
        surface the error to the user.
    """
    parsed = _validate_github_repo(repo)
    if parsed is None:
        return FetchIssueOutput(
            number=issue_number,
            title="[invalid repo]",
            body=f"fetch_issue: {repo!r} is not in 'owner/name' form",
            state="open",
            url=f"https://github.com/{repo}/issues/{issue_number}",
        )

    url = f"{_GITHUB_API_BASE}/repos/{parsed[0]}/{parsed[1]}/issues/{issue_number}"
    try:
        resp = httpx.get(url, headers=_github_headers(), timeout=_GITHUB_TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.warning("fetch_issue network error: %s", exc)
        return FetchIssueOutput(
            number=issue_number,
            title="[network error]",
            body=str(exc),
            state="open",
            url=f"https://github.com/{repo}/issues/{issue_number}",
        )
    if resp.status_code == 404:
        return FetchIssueOutput(
            number=issue_number,
            title="[not found]",
            body=f"No issue/PR #{issue_number} on {repo}",
            state="open",
            url=f"https://github.com/{repo}/issues/{issue_number}",
        )
    if resp.status_code >= 400:
        logger.warning("fetch_issue GitHub %d: %s", resp.status_code, resp.text[:200])
        return FetchIssueOutput(
            number=issue_number,
            title=f"[http {resp.status_code}]",
            body=resp.text[:2000],
            state="open",
            url=f"https://github.com/{repo}/issues/{issue_number}",
        )
    data = resp.json()
    # PRs share the issues endpoint; ``pull_request`` is set when it is.
    state = data.get("state", "open")
    if data.get("state_reason") == "not_planned":
        state = "closed"
    return FetchIssueOutput(
        number=data.get("number", issue_number),
        title=data.get("title", ""),
        body=data.get("body") or "",
        state=state,
        url=data.get("html_url", f"https://github.com/{repo}/issues/{issue_number}"),
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

    Backed by ripgrep (``rg --json``). If ripgrep is not installed,
    the tool returns an empty match list with a clear log message —
    the agent can fall back to ``read_file`` + ``list_directory``.

    Args:
        query: Search term — function name, error string, or symbol.
        path: Root directory to search within (default: cwd).

    Returns:
        Up to 50 matches, each formatted ``path:line: column  text``.
        Output is truncated to the first match per file when the
        corpus is large; raise ``limit`` by passing a more specific
        ``path``.
    """
    rg = shutil.which("rg")
    if rg is None:
        logger.warning("search_codebase: ripgrep (rg) not found on PATH")
        return SearchCodebaseOutput(matches=[])

    cmd = [
        rg,
        "--json",
        "--no-heading",
        "--no-messages",
        "--max-count",
        str(_RG_MAX_MATCHES),
        "--",
        query,
        path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_RG_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "search_codebase timed out after %ds: query=%r path=%r",
            _RG_TIMEOUT_S,
            query,
            path,
        )
        return SearchCodebaseOutput(matches=[])

    if proc.returncode not in (0, 1):  # 1 = no matches, which is fine
        logger.warning(
            "search_codebase rg failed (rc=%d): %s",
            proc.returncode,
            proc.stderr.strip()[:200],
        )
        return SearchCodebaseOutput(matches=[])

    matches: list[str] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        rel_path = data["path"]["text"]
        line_number = data["line_number"]
        for sub in data["submatches"]:
            col = sub["start"] + 1
            text = sub["match"]["text"].rstrip()
            matches.append(f"{rel_path}:{line_number}:{col}: {text}")
            if len(matches) >= _RG_MAX_MATCHES:
                break
        if len(matches) >= _RG_MAX_MATCHES:
            break

    return SearchCodebaseOutput(matches=matches)


class RunTestsInput(BaseModel):
    """Input schema for sandbox test execution."""

    path: str = Field(default="tests/", description="Test path or file.")
    command: str = Field(default="pytest", description="Test runner command.")


class RunTestsOutput(BaseModel):
    """Output schema for sandbox test execution."""

    passed: bool = Field(description="Whether all tests passed.")
    output: str = Field(description="Captured test runner output.")


def _tokenize_args(command: str) -> list[str]:
    """Split a shell-style command string into argv tokens.

    Ponytail: shlex.split without enabling shell features. Keeps
    quotes and backslash-escapes intact. Never invokes a shell —
    we always pass the resulting list straight to ``subprocess.run``
    with ``shell=False`` (the default).
    """
    import shlex

    return shlex.split(command)


@tool(args_schema=RunTestsInput)
def run_tests(path: str = "tests/", command: str = "pytest") -> RunTestsOutput:
    """Run tests to verify code changes don't break existing functionality.

    Use this after proposing or applying a code change to get empirical
    evidence the change is safe. Also use it to investigate a user-reported
    failure by running the specific test path they mention.

    For v0.1 the ``sandbox`` is the agent's working directory — tests
    run locally with a 300s timeout and a 50 KB output cap. When a
    proper isolated sandbox is wired (Daytona / Docker / modal),
    swap the subprocess block for a sandbox call; the rest of the
    tool is stable.

    Args:
        path: Root test directory or single file.
        command: Test runner executable and args (e.g. ``"pytest -x -q"``).

    Returns:
        Pass/fail flag plus captured stdout+stderr. The output is
        truncated to ~50 KB from the tail (where the failure trace
        lives); pass a more specific path to keep it small.
    """
    args = _tokenize_args(command) + [path]
    logger.info("run_tests: %s", " ".join(args))
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_TEST_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RunTestsOutput(
            passed=False,
            output=f"run_tests timed out after {_TEST_TIMEOUT_S}s",
        )
    except FileNotFoundError as exc:
        return RunTestsOutput(
            passed=False,
            output=f"run_tests: command not found — {exc}",
        )
    output = (proc.stdout or "") + (proc.stderr or "")
    if len(output) > _TEST_MAX_OUTPUT_BYTES:
        output = "...[truncated]...\n" + output[-_TEST_MAX_OUTPUT_BYTES:]
    return RunTestsOutput(passed=proc.returncode == 0, output=output)


class ProposeFixInput(BaseModel):
    """Input schema for proposing a code fix."""

    issue_description: str = Field(description="Summary of the bug or issue.")
    file_path: str = Field(description="Target file to modify, if known.")


class ProposeFixOutput(BaseModel):
    """Output schema for proposing a code fix."""

    summary: str = Field(description="One-line description of the proposed fix.")
    patch: str = Field(default="", description="Unified diff patch or code snippet.")
    context_file: str = Field(
        default="",
        description="Contents of the target file, when readable. Truncated to 200 KB.",
    )


def _read_file_safe(path: str) -> str | None:
    """Read a text file, returning None if it doesn't exist or isn't text.

    Used by ``propose_fix`` to gather context for the agent. Bounded
    to 200 KB so a runaway read doesn't blow the context window.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None
    if len(text) > 200_000:
        return text[:200_000] + "\n... [truncated]"
    return text


@tool(args_schema=ProposeFixInput)
def propose_fix(issue_description: str, file_path: str = "") -> ProposeFixOutput:
    """Produce a concrete code fix suggestion from a diagnosed bug or issue description.

    Use this after ``bug-diagnostician`` or ``run_bugfix_pipeline`` has
    identified the root cause. The tool itself is intentionally a
    thin context-gatherer — it reads the target file (if given) and
    hands the bundle back to the calling agent. The actual patch is
    generated by the LLM that is already running the agent, so we
    don't pay a second model call.

    For v0.1 the tool returns:
      - ``summary``: one-line description of the bug surface.
      - ``patch``: empty (the LLM proposes the patch inline).
      - ``context_file``: the file's content if it was readable.

    When a dedicated code-edit subagent or ``refactor_pipeline`` is
    wired, swap the body for an LLM call there. Ponytail: the
    context-gathering IS the value — the patch stays in the agent.
    """
    file_content = _read_file_safe(file_path) if file_path else None
    target = f"file {file_path}" if file_path else "the issue"
    summary = f"Fix proposed for {target}: {issue_description[:80]}"
    return ProposeFixOutput(
        summary=summary,
        patch="",
        context_file=file_content or "",
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
    changes already committed and pushed — the agent handles commit +
    push with its own bash/subprocess tool; this tool only opens the PR.

    Requires ``$GITHUB_TOKEN`` (or ``$GH_TOKEN``) with ``repo`` scope
    and write access to the target repo.

    Args:
        repo: ``owner/name`` repository string.
        title: Pull request title.
        body: PR description body.
        head: Source branch containing changes.
        base: Target branch (default ``main``).

    Returns:
        Created PR URL and number. On HTTP error, returns
        ``number=0`` and an error-coded URL so the agent can branch
        on failure.
    """
    parsed = _validate_github_repo(repo)
    if parsed is None:
        return CreatePROutput(
            url=f"https://github.com/{repo}/pull/0",
            number=0,
        )
    if not head:
        return CreatePROutput(
            url=f"https://github.com/{repo}/pull/0",
            number=0,
        )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return CreatePROutput(
            url=f"https://github.com/{repo}/pull/0",
            number=0,
        )
    url = f"{_GITHUB_API_BASE}/repos/{parsed[0]}/{parsed[1]}/pulls"
    try:
        resp = httpx.post(
            url,
            headers=_github_headers(),
            json={"title": title, "body": body, "head": head, "base": base},
            timeout=_GITHUB_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        logger.warning("create_pr network error: %s", exc)
        return CreatePROutput(
            url=f"https://github.com/{repo}/pull/0",
            number=0,
        )
    if resp.status_code >= 400:
        logger.warning("create_pr GitHub %d: %s", resp.status_code, resp.text[:300])
        return CreatePROutput(
            url=f"https://github.com/{repo}/pull/0",
            number=0,
        )
    data = resp.json()
    return CreatePROutput(
        url=data.get("html_url", f"https://github.com/{repo}/pull/0"),
        number=data.get("number", 0),
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


def _get_tavily_client() -> Any:
    """Return a ``TavilyClient`` for the current TAVILY_API_KEY, or ``None``."""
    from tavily import TavilyClient

    from core.config import get_settings

    key = get_settings().tavily_api_key
    if not key:
        return None
    return TavilyClient(api_key=key)


def _tavily_first(
    *,
    op: str,
    tavily_fn: Any,
    ddg_fn: Any,
) -> tuple[Any, str]:
    """Run *tavily_fn* first; on any error, fall back to *ddg_fn*.

    Returns ``(result, backend)`` where ``backend`` is ``"tavily"`` on
    the happy path and ``"duckduckgo"`` when the fallback fires (or
    when no Tavily key is configured). Ponytail: this is the
    contract the three Tavily-backed tools share — call Tavily,
    swallow the exception, fall back. Errors are logged at WARNING;
    the agent never sees an exception from the search surface.
    """
    client = _get_tavily_client()
    if client is None:
        try:
            return ddg_fn(), "duckduckgo"
        except Exception as exc:  # noqa: BLE001
            logger.warning("DDG fallback for %s failed: %s", op, exc)
            return None, "duckduckgo"
    try:
        return tavily_fn(client), "tavily"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily %s failed, falling back to DDG: %s", op, exc)
        try:
            return ddg_fn(), "duckduckgo"
        except Exception as inner:  # noqa: BLE001
            logger.warning("DDG fallback for %s also failed: %s", op, inner)
            return None, "duckduckgo"


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

    def _tavily(client: Any) -> dict[str, Any]:
        result: dict[str, Any] = client.search(
            query=query,
            max_results=max_results,
            topic=topic,
            include_answer="basic",
        )
        return result

    def _ddg() -> list[dict[str, Any]]:
        return _ddgs_text(query, max_results)

    raw, backend = _tavily_first(op="search", tavily_fn=_tavily, ddg_fn=_ddg)
    if backend == "tavily" and raw:
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
    # DDG path (no key, or Tavily failed and DDG succeeded).
    results = raw or []
    return InternetSearchOutput(
        query=query,
        results=[
            InternetSearchResult(
                title=res.get("title", "Web result"),
                url=res.get("href", ""),
                content=res.get("body", "")[:800],
                score=0.5,
            )
            for res in results
        ],
        backend="duckduckgo",
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

    def _tavily(client: Any) -> dict[str, Any]:
        return client.extract(  # type: ignore[no-any-return]
            urls=[url],
            format="markdown",
            query=question or None,
        )

    def _ddg() -> str:
        # No Tavily: use httpx + bs4 directly. With a question, search
        # DDG first and fetch the top hit.
        is_query = bool(question)
        return _ddg_fetch_url_via_search(url, is_query=is_query)

    raw, backend = _tavily_first(op="extract", tavily_fn=_tavily, ddg_fn=_ddg)
    if backend == "tavily" and isinstance(raw, dict):
        # Tavily returns a list of results keyed by the input URL; when
        # ``query`` is set there is also a top-level ``answer`` field.
        results = raw.get("results", [])
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
    # DDG path — raw is the fetched content string (or None on full failure).
    return FetchUrlOutput(
        url=url,
        title="",
        content=raw or "",
        answer="",
        backend="duckduckgo",
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

    def _tavily(client: Any) -> str:
        return str(client.qna_search(query=query, topic=topic) or "")

    def _ddg() -> str:
        return _ddg_search_for_answer(query, max_results=3)

    answer, backend = _tavily_first(op="qna_search", tavily_fn=_tavily, ddg_fn=_ddg)
    return QnaSearchOutput(query=query, answer=answer or "", backend=backend)
