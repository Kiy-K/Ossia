"""Core Ossia dev-concierge agent built on LangChain Deep Agents."""

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
    create_pr,
    fetch_issue,
    grade_response,
    propose_fix,
    run_tests,
    search_codebase,
    search_knowledge_base,
    send_response,
)

logger = logging.getLogger(__name__)

_DEV_CONCIERGE_SUBAGENTS = (
    (
        "code-researcher",
        "Use for code reading, symbol search, and repo structure questions.",
        "You are a code researcher. Use search_codebase and search_knowledge_base. Return the file paths and snippets needed.",
    ),
    (
        "bug-diagnostician",
        "Use when the user reports a bug, failing test, or runtime error.",
        "You are a bug diagnostician. Use search_codebase and run_tests to gather symptoms. Produce a concise likely cause and reproduction steps.",
    ),
    (
        "fix-proposer",
        "Use for proposing code changes, patches, or implementation strategies.",
        "You are a fix proposer. Use propose_fix and search_codebase. Produce a minimal concrete patch summary.",
    ),
    (
        "test-runner",
        "Use to run tests, check coverage, or validate a patch.",
        "You are a test runner. Use run_tests and search_codebase. Report pass/fail and the failing cases.",
    ),
)


def load_system_prompt(path: str | Path = "src/ossia/prompts/system.md") -> str:
    """Load the system prompt from disk."""
    return Path(path).read_text(encoding="utf-8")


def create_core_tools() -> list[BaseTool]:
    return [
        search_codebase,
        search_knowledge_base,
        run_tests,
        propose_fix,
        fetch_issue,
        create_pr,
        grade_response,
        send_response,
    ]


def create_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Create a model-agnostic chat model from environment settings."""
    settings = settings or get_settings()
    provider = settings.provider
    if provider == Provider.NEBIUS:
        return create_nebius_chat_model(settings)
    openai_like_providers = {
        Provider.OPENROUTER: ("https://openrouter.ai/api/v1", settings.openrouter_api_key),
        Provider.FIREWORKS: ("https://api.fireworks.ai/inference/v1", settings.fireworks_api_key),
        Provider.BASETEN: ("https://inference.baseten.co/v1", settings.baseten_api_key),
        Provider.OPENAI: (None, settings.openai_api_key),
    }
    if provider in openai_like_providers:
        base_url, api_key = openai_like_providers[provider]
        if not api_key:
            raise ValueError(f"API key for provider '{provider}' is not configured.")
        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": settings.model,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "api_key": api_key,
            "streaming": True,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)
    if provider == Provider.ANTHROPIC:
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
    if provider == Provider.GOOGLE:
        from langchain_google_genai import ChatGoogleGenerativeAI
        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required for the google provider.")
        return ChatGoogleGenerativeAI(
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            api_key=settings.google_api_key,
        )
    if provider == Provider.OLLAMA:
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=settings.model,
            temperature=settings.temperature,
            base_url=settings.ollama_base_url,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _build_middlewares(settings: Settings) -> list[Any]:
    return [
        RetryToolMiddleware(max_attempts=3, initial_interval=1.0, backoff_factor=2.0, jitter=True),
        RevisionLoopCapMiddleware(max_loops=settings.max_revision_loops),
    ]


def _interrupt_config(settings: Settings, checkpointer: Any | None) -> dict[str, bool] | None:
    if not settings.enable_human_review or checkpointer is None:
        return None
    return {"send_response": True}


def _build_subagents(model: BaseChatModel) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "system_prompt": prompt,
            "tools": [search_codebase, search_knowledge_base],
            "model": model,
        }
        for name, description, prompt in _DEV_CONCIERGE_SUBAGENTS
    ]


def _make_backend() -> CompositeBackend:
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
    settings = settings or get_settings()
    model = create_chat_model(settings)
    tools = create_core_tools()
    system_prompt = load_system_prompt()
    subagents = _build_subagents(model)
    toolkit: MCPToolkit | None = None
    if include_mcp_tools:
        try:
            toolkit = await MCPToolkit(settings).__aenter__()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP toolkit initialization failed (%s); falling back to core tools.", exc)
            toolkit = None
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
    config = {"configurable": {"thread_id": thread_id}}
    return graph.astream_events(
        {"messages": [input_message]},
        config,
        version="v2",
    )
