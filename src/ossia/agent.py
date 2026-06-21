"""Core Ossia support agent built on LangChain Deep Agents."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from ossia.adapters.nebius import create_nebius_chat_model
from ossia.config import Provider, Settings, get_settings
from ossia.mcp_tools import MCPToolkit
from ossia.memory import get_store
from ossia.middleware import RetryToolMiddleware, RevisionLoopCapMiddleware
from ossia.tools import (
    grade_response,
    search_knowledge_base,
    send_response,
)

logger = logging.getLogger(__name__)


def load_system_prompt(path: str | Path = "src/ossia/prompts/system.md") -> str:
    """Load the system prompt from disk.

    Args:
        path: Path to the system prompt markdown file.

    Returns:
        System prompt string.
    """
    return Path(path).read_text(encoding="utf-8")


def create_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Create a model-agnostic chat model from environment settings.

    Args:
        settings: Optional settings instance.

    Returns:
        Configured LangChain chat model.

    Raises:
        ValueError: If the provider has no API key configured or is unsupported.
    """
    settings = settings or get_settings()

    if settings.provider == Provider.NEBIUS:
        return create_nebius_chat_model(settings)

    if settings.provider == Provider.ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the anthropic provider.")
        return ChatAnthropic(
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            api_key=settings.anthropic_api_key,
            streaming=True,
        )

    if settings.provider == Provider.GOOGLE:
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required for the google provider.")
        return ChatGoogleGenerativeAI(
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            api_key=settings.google_api_key,
        )

    if settings.provider == Provider.OLLAMA:
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise ImportError(
                "The 'ollama' provider requires 'langchain-ollama'. "
                "Install it with: pip install 'ossia[ollama]'"
            ) from exc

        return ChatOllama(
            model=settings.model,
            temperature=settings.temperature,
            base_url=settings.ollama_base_url,
        )

    # OpenAI-compatible providers: openai, openrouter, fireworks, baseten.
    from langchain_openai import ChatOpenAI

    base_url_map = {
        Provider.OPENROUTER: "https://openrouter.ai/api/v1",
        Provider.FIREWORKS: "https://api.fireworks.ai/inference/v1",
        Provider.BASETEN: "https://inference.baseten.co/v1",
    }
    api_key_map = {
        Provider.OPENROUTER: settings.openrouter_api_key,
        Provider.FIREWORKS: settings.fireworks_api_key,
        Provider.BASETEN: settings.baseten_api_key,
        Provider.OPENAI: settings.openai_api_key,
    }

    api_key = api_key_map.get(settings.provider)
    if not api_key:
        raise ValueError(f"API key for provider '{settings.provider}' is not configured.")
    base_url = base_url_map.get(settings.provider)

    return ChatOpenAI(
        model=settings.model,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        api_key=api_key,
        base_url=base_url,
        streaming=True,
    )


def create_core_tools() -> list[BaseTool]:
    """Create the core tool set for the agent.

    Returns:
        List of LangChain tools.
    """
    return [
        search_knowledge_base,
        grade_response,
        send_response,
    ]


def _build_middlewares(settings: Settings) -> list[Any]:
    """Build the middleware stack for the agent.

    Args:
        settings: Application settings.

    Returns:
        List of Deep Agents middleware instances.
    """
    return [
        RetryToolMiddleware(
            max_attempts=3,
            initial_interval=1.0,
            backoff_factor=2.0,
            jitter=True,
        ),
        RevisionLoopCapMiddleware(max_loops=settings.max_revision_loops),
    ]


def _interrupt_config(settings: Settings, checkpointer: Any | None) -> dict[str, bool] | None:
    """Return the interrupt configuration, gated on checkpointer availability.

    LangGraph requires a checkpointer to persist interrupt state, so human-in-the-loop
    is only enabled when a checkpointer is present.

    Args:
        settings: Application settings.
        checkpointer: Checkpoint saver instance or None.

    Returns:
        Interrupt configuration dict, or None to disable interrupts.
    """
    if not settings.enable_human_review or checkpointer is None:
        return None
    return {"send_response": True}


# Intent-specialist subagents. The main agent classifies the user's intent and
# delegates research + drafting to the matching specialist via the built-in
# ``task`` tool, then grades / human-reviews / sends the result in the main
# thread (where the checkpointer-backed interrupt gate lives). This realizes
# the PRD's ``classify_intent`` step as real routing while using the Deep
# Agents subagent harness for context isolation.
_INTENT_SUBAGENTS: tuple[tuple[str, str, str], ...] = (
    (
        "billing-specialist",
        "Delegate here when the user asks about billing, invoices, pricing, "
        "usage costs, or payment for Nebius services.",
        "You are a Nebius billing specialist. Search the knowledge base for "
        "pricing, invoicing, and cost-optimization facts. Draft a concise, "
        "accurate answer with citations. If the KB has nothing relevant, say so "
        "and give your best general guidance while flagging the uncertainty.",
    ),
    (
        "technical-support",
        "Delegate here for technical questions about Serverless Endpoints, "
        "Jobs, vLLM serving, deployments, configuration, or troubleshooting.",
        "You are a Nebius technical support specialist. Search the knowledge "
        "base for endpoint, job, GPU, and deployment documentation. Draft a "
        "concise, accurate, step-by-step answer with citations. Flag any "
        "uncertainty rather than guessing.",
    ),
    (
        "account-access",
        "Delegate here for account, login, credential, access, or permission "
        "issues (e.g. resetting endpoint credentials).",
        "You are a Nebius account-access specialist. Search the knowledge base "
        "for credential reset, login, and access-control procedures. Draft a "
        "concise, accurate answer with citations. Never ask for or echo "
        "secrets; guide the user to regenerate via the console.",
    ),
    (
        "general-information",
        "Delegate here for general product, feature, or documentation "
        "questions that do not fit the other specialists.",
        "You are a Nebius general-information specialist. Search the knowledge "
        "base for product overviews, features, and documentation. Draft a "
        "concise, accurate answer with citations. If unsure, say so and offer "
        "to escalate.",
    ),
)


def _build_subagents(model: BaseChatModel) -> list[dict[str, Any]]:
    """Build the intent-specialist subagents exposed via the ``task`` tool.

    Each subagent shares the main agent's (portable) model and the
    ``search_knowledge_base`` research tool, but has a focused system prompt
    scoped to one intent class. Subagents only research and draft; grading,
    human review, and sending stay in the main agent.

    Args:
        model: Chat model instance shared with the main agent (portable, no
            vendor hardcoding).

    Returns:
        List of Deep Agents ``SubAgent`` dicts for ``create_deep_agent``.
    """
    return [
        {
            "name": name,
            "description": description,
            "system_prompt": prompt,
            "tools": [search_knowledge_base],
            "model": model,
        }
        for name, description, prompt in _INTENT_SUBAGENTS
    ]


def _make_backend() -> CompositeBackend:
    """Build a composite filesystem backend routing ``/memories/`` to the store.

    Short-term working files live in thread-scoped state; anything under
    ``/memories/`` is persisted to the LangGraph ``BaseStore`` so it survives
    across threads/sessions (the Deep Agents long-term-memory pattern). The
    sub-backends resolve the runtime store/state via config at runtime, so no
    runtime argument is passed at construction.

    Returns:
        A ``CompositeBackend`` with ``/memories/`` routed to ``StoreBackend``.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={"/memories/": StoreBackend()},
    )


def _compile_agent(
    settings: Settings,
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    checkpointer: Any | None,
    *,
    store: BaseStore | None = None,
    subagents: list[dict[str, Any]] | None = None,
) -> CompiledStateGraph:
    """Compile the Deep Agent graph from shared configuration.

    Args:
        settings: Application settings.
        model: Chat model instance.
        tools: Tool list.
        system_prompt: System prompt string.
        checkpointer: Checkpoint saver or None.
        store: Optional LangGraph BaseStore for cross-session long-term memory.
            When provided, ``/memories/`` files persist across threads.
        subagents: Optional intent-specialist subagents exposed via the
            ``task`` tool. When provided, the main agent can delegate
            research/drafting per intent class.

    Returns:
        Compiled LangGraph agent.
    """
    return create_deep_agent(
        name="ossia",
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=_build_middlewares(settings),
        checkpointer=checkpointer,
        interrupt_on=_interrupt_config(settings, checkpointer),
        subagents=subagents,
        store=store,
        backend=_make_backend() if store is not None else None,
    )


def build_agent(
    settings: Settings | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Build a compiled Ossia Deep Agent graph without MCP tools (sync).

    Use :func:`build_agent_async` when MCP tools or a long-term-memory store
    are required. The sync builder still wires intent subagents (no store
    lifecycle needed), so ``classify_intent`` routing is available in both
    paths.

    Args:
        settings: Optional settings instance.
        checkpointer: Optional checkpoint saver for persistence.

    Returns:
        Compiled LangGraph agent.
    """
    settings = settings or get_settings()
    model = create_chat_model(settings)
    tools = create_core_tools()
    system_prompt = load_system_prompt()

    return _compile_agent(
        settings,
        model,
        tools,
        system_prompt,
        checkpointer,
        subagents=_build_subagents(model),
    )


@asynccontextmanager
async def build_agent_async(
    settings: Settings | None = None,
    checkpointer: Any | None = None,
    include_mcp_tools: bool = True,
) -> AsyncGenerator[CompiledStateGraph, None]:
    """Build a compiled Ossia Deep Agent graph, owning MCP session lifetimes.

    Implemented as an async context manager so the MCP client sessions stay
    alive for the agent's lifetime and are cleaned up on exit. MCP loading
    degrades gracefully: if a server is unreachable, the agent starts with the
    remaining/core tools instead of aborting.

    Args:
        settings: Optional settings instance.
        checkpointer: Optional checkpoint saver for persistence.
        include_mcp_tools: Whether to load tools from configured MCP servers.

    Yields:
        Compiled LangGraph agent.
    """
    settings = settings or get_settings()
    model = create_chat_model(settings)
    tools = create_core_tools()
    system_prompt = load_system_prompt()
    subagents = _build_subagents(model)

    # Initialize MCP tools if requested. Only the initialization is wrapped so
    # that a caller exception raised during the yielded context is not mistaken
    # for an MCP failure; cleanup is handled by the finally below.
    toolkit: MCPToolkit | None = None
    if include_mcp_tools:
        try:
            toolkit = await MCPToolkit(settings).__aenter__()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP toolkit initialization failed (%s); falling back to core tools.",
                exc,
            )
            toolkit = None

    # Long-term memory store: Postgres-backed in production, in-process
    # InMemoryStore for local/dev (so cross-session memory still works within a
    # process). Owned for the agent's lifetime so /memories/ files persist.
    store: BaseStore
    store_cm: Any | None = None
    if settings.postgres_url:
        store_cm = get_store(settings)
        store = await store_cm.__aenter__()
    else:
        store = InMemoryStore()

    try:
        if toolkit is not None:
            tools = [*tools, *toolkit.get_tools()]
        yield _compile_agent(
            settings,
            model,
            tools,
            system_prompt,
            checkpointer,
            store=store,
            subagents=subagents,
        )
    finally:
        if store_cm is not None:
            await store_cm.__aexit__(None, None, None)
        if toolkit is not None:
            await toolkit.__aexit__(None, None, None)


def stream_agent_events(
    graph: CompiledStateGraph,
    thread_id: str,
    input_message: dict[str, Any],
) -> Any:
    """Stream agent events for real-time UI feedback.

    Args:
        graph: Compiled agent graph.
        thread_id: Conversation thread identifier.
        input_message: Initial user message.

    Returns:
        Async iterator of LangGraph events.
    """
    config = {"configurable": {"thread_id": thread_id}}
    return graph.astream_events(
        {"messages": [input_message]},
        config,
        version="v2",
    )
