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

from ossia.config import Provider, Settings, get_settings
from ossia.context import OssiaContext
from ossia.episodic import make_episodic_recall_tool
from ossia.mcp_tools import MCPToolkit
from ossia.memory import (
    AGENT_NAMESPACE,
    AGENTS_MEMORY_KEY,
    get_store,
    seed_memory,
)
from ossia.middleware import RetryToolMiddleware, RevisionLoopCapMiddleware
from ossia.tools import (
    create_pr,
    fetch_issue,
    fetch_url,
    grade_response,
    internet_search,
    propose_fix,
    qna_search,
    run_tests,
    search_codebase,
    search_knowledge_base,
    send_response,
)

logger = logging.getLogger(__name__)

_DEV_CONCIERGE_SUBAGENTS = (
    (
        "code-researcher",
        "Read code, find symbols, and map repo structure. Delegates here when "
        "the main agent needs a file path, snippet, or architectural map "
        "without filling the coordinator's context.",
        (
            "You are a code researcher for the Nebius / Ossia monorepo.\n"
            "\n"
            "Use the provided tools (search_codebase, search_knowledge_base) to "
            "answer the question. Prefer file paths and short snippets over "
            "explanatory prose.\n"
            "\n"
            "Output format:\n"
            "  - List of relevant file paths (one per line).\n"
            "  - For each, a 1-3 line snippet of the relevant code.\n"
            "  - A one-sentence synthesis tying the snippets together.\n"
            "\n"
            "Keep the response under 200 words. Do not include raw search-tool "
            "transcripts or unprocessed outputs."
        ),
    ),
    (
        "bug-diagnostician",
        "Investigate a reported bug, failing test, or runtime error and "
        "produce a likely root cause and minimal reproduction. Delegates here "
        "when the main agent needs structured diagnostic output (not a fix).",
        (
            "You are a bug diagnostician for the Nebius / Ossia monorepo.\n"
            "\n"
            "Use the provided tools to gather symptoms. The expected workflow:\n"
            "  1. Read the failing test or error trace.\n"
            "  2. Find the relevant source code with search_codebase.\n"
            "  3. Form a hypothesis and the smallest possible reproduction.\n"
            "\n"
            "Output format:\n"
            "  - Likely cause (1-2 sentences).\n"
            "  - Reproduction steps (numbered list, 3-5 items max).\n"
            "  - Supporting evidence (file paths + short snippets).\n"
            "\n"
            "Do NOT propose a fix. Delegate that to fix-proposer. Keep the "
            "response under 250 words."
        ),
    ),
    (
        "fix-proposer",
        "Propose a code change or implementation strategy. Delegates here "
        "after a diagnosis is in hand; produces a minimal concrete patch "
        "summary the main agent can review.",
        (
            "You are a fix proposer for the Nebius / Ossia monorepo.\n"
            "\n"
            "Use the provided tools to draft a minimal change that resolves the "
            "diagnosed problem.\n"
            "\n"
            "Output format:\n"
            "  - Patch summary (1-2 sentences describing the change).\n"
            "  - Diff or pseudo-diff (file path + before/after for each change).\n"
            "  - Risk notes (anything the reviewer should double-check).\n"
            "\n"
            "Do not actually apply the change. The main agent decides. Keep the "
            "response under 250 words."
        ),
    ),
    (
        "test-runner",
        "Run tests, check coverage, or validate a proposed patch. Delegates "
        "here when the main agent needs empirical evidence the change is safe.",
        (
            "You are a test runner for the Nebius / Ossia monorepo.\n"
            "\n"
            "Use the provided tools to run the relevant test suite and report "
            "results.\n"
            "\n"
            "Output format:\n"
            "  - Pass/fail summary (X/Y passed).\n"
            "  - Failing test names + first 1-2 lines of each failure.\n"
            "  - Coverage delta if available (e.g. +1.2%).\n"
            "\n"
            "If a test hangs or times out, say so explicitly and stop; do not "
            "retry without instruction. Keep the response under 200 words."
        ),
    ),
)


def load_system_prompt(path: str | Path = "src/ossia/prompts/system.md") -> str:
    """Load the system prompt from disk."""
    return Path(path).read_text(encoding="utf-8")


def create_core_tools() -> list[BaseTool]:
    return [
        search_codebase,
        search_knowledge_base,
        internet_search,
        fetch_url,
        qna_search,
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
        raise NotImplementedError(
            "Provider.NEBIUS was removed; the adapter was deleted. "
            "Use Provider.OPENROUTER (or another OpenAI-compatible "
            "provider) with a Nebius-routed model id."
        )
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


def _make_backend(
    store: BaseStore,
    namespace: tuple[str, ...] = AGENT_NAMESPACE,
) -> CompositeBackend:
    """Build the filesystem backend with agent-scoped memory wired in.

    Filesystem under ``/memories/`` is backed by the LangGraph store
    namespaced to ``namespace`` (default: agent-scoped identity
    ``("ossia",)``). All other paths use the in-process StateBackend.
    The store is injected directly into ``StoreBackend`` so writes do
    not depend on runtime context resolution.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(
                store=store,
                namespace=lambda rt, _ns=namespace: _ns,
            ),
        },
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
    episodic_tool: BaseTool | None = None,
    context_schema: type | None = None,
) -> CompiledStateGraph:
    backend = _make_backend(store) if store is not None else None
    all_tools: list[BaseTool] = list(tools)
    if episodic_tool is not None:
        all_tools.append(episodic_tool)
    return create_deep_agent(
        name="ossia",
        model=model,
        tools=all_tools,
        system_prompt=system_prompt,
        middleware=_build_middlewares(settings),
        checkpointer=checkpointer,
        interrupt_on=_interrupt_config(settings, checkpointer),
        subagents=subagents,
        store=store,
        backend=backend,
        memory=[AGENTS_MEMORY_KEY] if store is not None else None,
        context_schema=context_schema,
    )


def build_agent(
    settings: Settings | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    settings = settings or get_settings()
    model = create_chat_model(settings)
    tools = create_core_tools()
    system_prompt = load_system_prompt()
    # Sync build path is for tests and one-off scripts. No event loop here,
    # so we cannot seed the in-process store; tests seed explicitly via
    # ``seed_memory`` after the agent is built. Production uses the async
    # path which seeds at startup.
    return _compile_agent(
        settings,
        model,
        tools,
        system_prompt,
        checkpointer,
        subagents=_build_subagents(model),
        context_schema=OssiaContext,
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
    # Seed agent-scoped memory once per store. Idempotent: re-runs leave
    # any agent-written updates alone.
    try:
        await seed_memory(store)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory seed failed (%s); continuing without seed", exc)
    # Episodic memory wraps the checkpointer; only available when one is
    # configured. The factory returns None for ephemeral setups (in-process
    # ``build_agent`` path or test mode).
    episodic_tool = make_episodic_recall_tool(checkpointer)
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
            episodic_tool=episodic_tool,
            context_schema=OssiaContext,
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
