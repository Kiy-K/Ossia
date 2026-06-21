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
    fallback_used: bool = Field(
        default=False,
        description="True if KB was empty and web fallback was used.",
    )
    reasoning: str = Field(
        default="",
        description="Short explanation of how results were obtained.",
    )


@dataclass
class KnowledgeBase:
    """In-memory knowledge base for local development and tests.

    In production this is backed by a vector store (Postgres/pgvector, etc.).
    """

    documents: list[dict[str, Any]]

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """Return documents whose content contains any query token.

        Args:
            query: Search query.
            top_k: Maximum number of results.

        Returns:
            List of matching search results.
        """
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
    """Create a knowledge base with optional seed documents.

    When ``documents`` is None, a cached default knowledge base is returned so the
    default document set is built only once per process.

    Args:
        documents: Optional list of seed documents.

    Returns:
        Configured knowledge base instance.
    """
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
        return KBSearchOutput(
            results=results,
            fallback_used=False,
            reasoning="Matched documents in the local knowledge base.",
        )

    # Web fallback when KB is empty. Run the synchronous DDGS client off the
    # event loop and degrade gracefully on any search/network failure.
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
    """Run a synchronous DuckDuckGo text search.

    Args:
        query: Search query.
        max_results: Maximum number of results.

    Returns:
        List of result dicts.
    """
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


class GradeInput(BaseModel):
    """Input schema for response grading."""

    query: str = Field(description="Original user query.")
    response: str = Field(description="Draft response to evaluate.")
    context: str = Field(default="", description="Retrieved context used for the response.")


class GradeOutput(BaseModel):
    """Output schema for response grading."""

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
    # Deterministic heuristic used for unit tests; replace with LLM-as-judge in prod.
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
        feedback=(
            "Response looks acceptable." if score >= 0.67 else "Response needs more detail."
        ),
    )


class SendResponseInput(BaseModel):
    """Input schema for the final send action."""

    response: str = Field(description="Final response text to deliver.")
    channel: str = Field(default="chat", description="Delivery channel.")


class SendResponseOutput(BaseModel):
    """Output schema for the final send action."""

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
    return SendResponseOutput(
        sent=True,
        message=f"Response delivered via {channel}: {response[:120]}...",
    )
