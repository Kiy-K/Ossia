"""Tools for knowledge base search, web fallback, grading, and final actions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from duckduckgo_search import DDGS
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
    """Search the support knowledge base and fall back to web search if empty.

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


class GradeInput(BaseModel):
    query: str = Field(description="Original user query.")
    response: str = Field(description="Draft response to evaluate.")
    context: str = Field(default="", description="Retrieved context used for the response.")


class GradeOutput(BaseModel):
    passes: bool = Field(description="Whether the response meets quality standards.")
    score: float = Field(ge=0.0, le=1.0, description="Quality score.")
    feedback: str = Field(description="Concise feedback for revision or approval.")


@tool(args_schema=GradeInput)
def grade_response(query: str, response: str, context: str = "") -> GradeOutput:
    """Grade a draft response before human review or finalization.

    Args:
        query: Original user query.
        response: Draft response to grade.
        context: Retrieved context used to produce the response.

    Returns:
        Grade output with pass/fail decision and feedback.
    """
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
    """Deliver the final approved response to the user.

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
    """Fetch a GitHub issue or pull request by repo and number.

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
    """Search the local codebase for a code token, symbol, or error string.

    TODO (CONTEXT.md §8 item 2): implement with ripgrep or embedding search.

    Args:
        query: Search term.
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
    """Run tests in a sandboxed environment and report the result.

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
    """Convert a diagnosis into a concrete fix proposal.

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
    """Open a GitHub pull request with the proposed changes.

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
