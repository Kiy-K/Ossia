"""Core Ossia dev-concierge agent built on LangChain Deep Agents."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware.async_subagents import AsyncSubAgent, AsyncSubAgentMiddleware
from deepagents.middleware.filesystem import FilesystemPermission
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.graph.state import CompiledStateGraph
from langgraph.middleware.redis import (
    DEFAULT_SIDE_EFFECT_PREFIXES,
    ToolCacheConfig,
    ToolResultCacheMiddleware,
)
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from core.config import Provider, Settings, get_settings
from core.context import OssiaContext
from core.episodic import (
    make_episodic_recall_tool,
    make_postgres_search_fn,
    make_search_threads_tool,
    make_semantic_recall_tool,
)
from core.mcp_tools import MCPToolkit
from core.memory import (
    AGENT_NAMESPACE,
    AGENTS_MEMORY_KEY,
    POLICY_NAMESPACE,
    get_redis_store,
    get_store,
    seed_memory,
)
from core.middleware import (
    CircuitBreakerMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    PIIRedactionMiddleware,
    RetryToolMiddleware,
    RevisionLoopCapMiddleware,
    ToolCallLimitMiddleware,
    make_caller_context_middleware,
)
from core.orchestrators.tools import (
    run_audit_pipeline,
    run_bugfix_pipeline,
    run_refactor_pipeline,
)
from core.request_context import caller_var
from core.tools import (
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
        "Read code, find symbols, and map repo structure. Use this when "
        "the main agent needs a file path, snippet, or architectural map "
        "without filling the coordinator's context.",
        (
            "You are a code researcher for the Ossia project.\n"
            "\n"
            "Use search_codebase and search_knowledge_base to answer the "
            "question. Prefer file paths and short snippets over explanatory "
            "prose.\n"
            "\n"
            "IMPORTANT: Return only the essential summary. Do NOT include raw "
            "search-tool transcripts, full file contents, or unprocessed "
            "outputs. The main agent receives only this report, so keep the "
            "context footprint small.\n"
            "\n"
            "Output format:\n"
            "  - List of relevant file paths (one per line).\n"
            "  - For each, a 1-3 line snippet of the relevant code.\n"
            "  - A one-sentence synthesis tying the snippets together.\n"
            "\n"
            "Keep the response under 200 words."
        ),
    ),
    (
        "bug-diagnostician",
        "Investigate a reported bug, failing test, or runtime error and "
        "produce a likely root cause and minimal reproduction. Use this "
        "when the main agent needs structured diagnostic output (not a fix).",
        (
            "You are a bug diagnostician for the Ossia project.\n"
            "\n"
            "Use the provided tools to gather symptoms. The expected workflow:\n"
            "  1. Read the failing test or error trace.\n"
            "  2. Find the relevant source code with search_codebase.\n"
            "  3. Form a hypothesis and the smallest possible reproduction.\n"
            "\n"
            "IMPORTANT: Return only the distilled diagnosis. Do NOT include "
            "raw search output or tool transcripts. The main agent relies on "
            "concise reports to keep its context clean.\n"
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
        "Propose a code change or implementation strategy. Use this "
        "after a diagnosis is in hand; produces a minimal concrete patch "
        "summary the main agent can review.",
        (
            "You are a fix proposer for the Ossia project.\n"
            "\n"
            "Use the provided tools to draft a minimal change that resolves the "
            "diagnosed problem.\n"
            "\n"
            "IMPORTANT: Return only the patch summary — not full file contents, "
            "not raw tool output. The main agent synthesizes the final patch; "
            "your job is a concise, actionable design.\n"
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
        "Run tests, check coverage, or validate a proposed patch. Use this "
        "when the main agent needs empirical evidence the change is safe.",
        (
            "You are a test runner for the Ossia project.\n"
            "\n"
            "Use the provided tools to run the relevant test suite and report "
            "results.\n"
            "\n"
            "IMPORTANT: Return only the pass/fail summary and key failure "
            "details. Do NOT include raw CLI output, full tracebacks, or "
            "unprocessed tool results.\n"
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
    (
        "ui-debugger",
        "Analyze UI screenshots, browser errors, and stacktrace images. "
        "Use this when the user uploads a screenshot of a bug, error, "
        "or unexpected UI state that needs visual inspection.",
        (
            "You are a UI debugger for the Ossia platform.\n"
            "\n"
            "Inspect the provided image(s) and extract visible issues:\n"
            "  1. Identify error messages, stack traces, or unexpected UI "
            "states visible in the screenshot.\n"
            "  2. Cross-reference visible text, component names, and error "
            "codes with the codebase using search_codebase.\n"
            "  3. Produce a structured diagnosis with evidence.\n"
            "\n"
            "IMPORTANT: Return only the essential findings. Do NOT include "
            "raw search output or verbose tool transcripts. Keep the context "
            "footprint small — the main agent receives only this report.\n"
            "\n"
            "Output format:\n"
            "  - What is visible (1 sentence).\n"
            "  - Issue identification (bullet points).\n"
            "  - Code locations (file paths + snippets).\n"
            "  - Suggested next steps.\n"
            "\n"
            "Keep the response under 250 words. Use read_file to inspect "
            "supporting files (e.g. config, logs) when the screenshot "
            "references them."
        ),
    ),
    (
        "diagram-analyzer",
        "Parse architecture diagrams, flowcharts, and system dependency "
        "graphs from uploaded images. Use this when the user needs "
        "structural understanding of a visual system diagram.",
        (
            "You are a diagram analyst for the Ossia platform.\n"
            "\n"
            "Analyze the provided architecture diagram, flowchart, or system "
            "graph:\n"
            "  1. Identify components, their responsibilities, and "
            "relationships visible in the diagram.\n"
            "  2. Map identified components to actual code locations in the "
            "codebase using search_codebase.\n"
            "  3. Trace data and control flow paths between components.\n"
            "\n"
            "IMPORTANT: Return only the distilled structural analysis. Do NOT "
            "include raw search output or verbose tool transcripts.\n"
            "\n"
            "Output format:\n"
            "  - Overall architecture (2-3 sentences).\n"
            "  - Component list (name / responsibility / code location).\n"
            "  - Data and control flow between components.\n"
            "  - Gaps, ambiguities, or missing detail in the diagram.\n"
            "\n"
            "Keep the response under 250 words."
        ),
    ),
    (
        "visual-regression-reviewer",
        "Compare before and after UI screenshots to identify visual "
        "regressions, layout shifts, or unintended changes. Use this when "
        "the user provides a pair of images for visual diff analysis.",
        (
            "You are a visual regression reviewer for the Ossia platform.\n"
            "\n"
            "Compare the provided before and after UI screenshots:\n"
            "  1. Identify layout changes, new or different elements, "
            "color shifts, and any visible errors.\n"
            "  2. Distinguish intentional changes from likely regressions.\n"
            "  3. Produce a structured diff report.\n"
            "\n"
            "IMPORTANT: Return only the distilled regression report. Do NOT "
            "include raw search output or verbose tool transcripts.\n"
            "\n"
            "Output format:\n"
            "  - Regression summary (1-2 sentences).\n"
            "  - Changed regions (bullet points with approximate location).\n"
            "  - Severity per change: critical / high / medium / low / info.\n"
            "  - Code areas likely affected, if identifiable.\n"
            "\n"
            "Keep the response under 250 words."
        ),
    ),
)


def load_system_prompt() -> str:
    """Load the system prompt from disk, relative to this file.

    Uses an ``__file__``-relative path for production robustness
    (consistent with the skills path in ``_compile_agent``).
    """
    path = Path(__file__).resolve().parent / "prompts" / "system.md"
    return path.read_text(encoding="utf-8")


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
        run_bugfix_pipeline,
        run_audit_pipeline,
        run_refactor_pipeline,
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
        return ChatAnthropic(  # type: ignore[call-arg]
            model=settings.model,  # pyright: ignore[reportCallIssue]
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,  # pyright: ignore[reportCallIssue]
            api_key=settings.anthropic_api_key,  # type: ignore[arg-type]
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
        return ChatOllama(  # type: ignore[no-any-return]
            model=settings.model,
            temperature=settings.temperature,
            base_url=settings.ollama_base_url,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def _build_async_subagents(settings: Settings) -> list[AsyncSubAgent]:
    """Build async subagent specs for long-running background tasks.

    These subagents run asynchronously via ``AsyncSubAgentMiddleware``.
    The supervisor launches them, checks progress, and retrieves results
    without blocking its own execution.

    Each spec maps to a ``graph_id`` registered in a LangGraph deployment.
    For local development without LangGraph Cloud, the middleware still
    exposes the lifecycle tools; actual execution requires a server.

    ``AsyncSubAgent`` is a ``TypedDict``, so return values use dict-style
    access (``spec["name"]``) rather than attribute access.
    """
    return [
        AsyncSubAgent(
            name="researcher",
            description=(
                "Conducts in-depth codebase research and repo-wide analysis. "
                "Use this for broad searches, architectural mapping, and dependency "
                "tracing that would take many turns inline."
            ),
            graph_id="researcher",
        ),
        AsyncSubAgent(
            name="tester",
            description=(
                "Runs test suites and validation pipelines. "
                "Use this for long test runs, coverage analysis, and flaky test "
                "detection that should not block the conversation."
            ),
            graph_id="tester",
        ),
        AsyncSubAgent(
            name="auditor",
            description=(
                "Performs repository audits and indexing tasks. "
                "Use this for comprehensive codebase audits, lint sweeps, "
                "and batch analysis jobs."
            ),
            graph_id="auditor",
        ),
    ]


def _build_middlewares(settings: Settings) -> list[Any]:
    middlewares: list[Any] = [
        # PII redaction runs first so sensitive data is stripped before
        # any tool executes and before retries, circuit breakers, or
        # revision caps evaluate.
        PIIRedactionMiddleware(),
        # Model retry handles transient LLM provider failures (rate limits,
        # timeouts) before the call reaches any tool. Placed early so
        # retries don't exhaust tool-call budgets.
        ModelRetryMiddleware(
            max_attempts=settings.model_retry_max_attempts,
            initial_interval=settings.model_retry_initial_interval,
            backoff_factor=settings.model_retry_backoff_factor,
        ),
        # Model fallback switches to a secondary model when the primary
        # provider is degraded. Only wired when a fallback model is configured.
        ModelFallbackMiddleware(
            fallback_model=create_chat_model(Settings(
                provider=settings.fallback_provider,  # type: ignore[arg-type]
                model=settings.fallback_model,
                openrouter_api_key=settings.openrouter_api_key,
                openai_api_key=settings.openai_api_key,
                anthropic_api_key=settings.anthropic_api_key,
                google_api_key=settings.google_api_key,
            ))
        ) if settings.fallback_model else None,
        # Circuit breaker opens when an external service repeatedly fails,
        # preventing retries from hammering a downed service. Placed before
        # RetryToolMiddleware so the breaker fails fast instead of exhausting
        # retries on a definitely-down backend.
        CircuitBreakerMiddleware(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            recovery_timeout=settings.circuit_breaker_recovery_timeout,
        ),
        RetryToolMiddleware(
            max_attempts=settings.retry_max_attempts,
            initial_interval=settings.retry_initial_interval,
            backoff_factor=settings.retry_backoff_factor,
            jitter=True,
        ),
        RevisionLoopCapMiddleware(max_loops=settings.max_revision_loops),
        ToolCallLimitMiddleware(max_calls=settings.tool_call_limit),
        CodeInterpreterMiddleware(
            ptc=[
                "search_codebase",
                "read_file",
                "recall_thread_turns",
            ],
            timeout=settings.code_interpreter_timeout,
            max_ptc_calls=settings.code_interpreter_max_ptc_calls,
            mode="thread",
        ),
    ]
    # Filter out None entries so an unconfigured fallback doesn't break the list.
    middlewares = [mw for mw in middlewares if mw is not None]
    # Tool result cache: when REDIS_URL is set, the langgraph-redis
    # library caches exact-match tool results in Redis. Placed after
    # PII redaction (so cached values are post-redaction) and before
    # the circuit breaker / retry (so a cache hit short-circuits
    # both). Side-effect tools are excluded by ``side_effect_prefixes``
    # (default plus ``edit_`` to cover the agent's memory writes).
    if settings.redis_url and settings.enable_tool_cache:
        try:
            middlewares.append(
                ToolResultCacheMiddleware(
                    ToolCacheConfig(
                        redis_url=settings.redis_url,
                        ttl_seconds=settings.tool_cache_ttl_seconds,
                        # ``edit_file`` writes to memory — must not
                        # be cached. The library's default prefix
                        # list covers ``create_``, ``send_``, etc.
                        # but not ``edit_``.
                        side_effect_prefixes=(
                            *DEFAULT_SIDE_EFFECT_PREFIXES,
                            "edit_",
                        ),
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001
            # Don't let a misconfigured cache break the agent build.
            # The tool cache is an optimization, not a correctness
            # dependency.
            logger.warning(
                "Tool result cache middleware failed to init: %s; "
                "agent will run without tool caching.",
                exc,
            )
    if settings.enable_async_subagents:
        try:
            async_subagents = _build_async_subagents(settings)
            middlewares.append(
                AsyncSubAgentMiddleware(async_subagents=async_subagents)
            )
            logger.info(
                "Async subagent middleware wired with %d subagents: %s",
                len(async_subagents),
                [a["name"] for a in async_subagents],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to wire async subagent middleware: %s", exc)
    return middlewares


def _interrupt_config(settings: Settings, checkpointer: Any | None) -> dict[str, bool] | None:
    if not settings.enable_human_review or checkpointer is None:
        return None
    return {"send_response": True}


# Tool groups for subagent permission scoping.
# Read-only tools that inspect but never mutate state.
_READ_ONLY_TOOLS: list[BaseTool] = [search_codebase, search_knowledge_base]
# Tools that test-runner subagents may also use.
_TEST_TOOLS: list[BaseTool] = [*_READ_ONLY_TOOLS, run_tests]

# Subagent permission tiers: maps subagent name -> allowed tools.
_SUBAGENT_TOOL_MAP: dict[str, list[BaseTool]] = {
    "code-researcher": _READ_ONLY_TOOLS,
    "bug-diagnostician": _READ_ONLY_TOOLS,
    "fix-proposer": _READ_ONLY_TOOLS,
    "test-runner": _TEST_TOOLS,
    "ui-debugger": _READ_ONLY_TOOLS,
    "diagram-analyzer": _READ_ONLY_TOOLS,
    "visual-regression-reviewer": _READ_ONLY_TOOLS,
}


def _build_subagents(model: BaseChatModel) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": description,
            "system_prompt": prompt,
            "tools": _SUBAGENT_TOOL_MAP.get(name, _READ_ONLY_TOOLS),
            "model": model,
        }
        for name, description, prompt in _DEV_CONCIERGE_SUBAGENTS
    ]


def _make_memory_namespace(base: tuple[str, ...] = AGENT_NAMESPACE) -> tuple[str, ...]:
    """Build a memory namespace from the current caller context.

    Reads the authenticated ``caller`` hash from the context var (set by
    ``verify_api_key`` in ``api.py``) and prepends it to the base namespace.
    This ensures memory files are scoped per authenticated caller and never
    bleed between users.

    When ``Settings.memory_scope == "agent"``, the caller's hash is
    ignored and the base namespace is returned unchanged — matching
    the DeepAgents "agent-scoped memory" pattern where every user
    contributes to and reads from the same memory.

    When the caller hash is unavailable (tests, one-off scripts), falls back
    to the base namespace (``("ossia", "default")``).

    Returns:
        A namespace tuple like ``("ossia", "abc123def456")`` (user scope),
        ``("ossia",)`` (agent scope), or ``("ossia", "default")`` when no
        caller is available.
    """
    if get_settings().memory_scope == "agent":
        return base
    caller = caller_var.get()
    if caller:
        return (base[0], caller)
    return base


# Write-deny permission for the read-only /policies/ route. The agent
# can read compliance/policy files but cannot rewrite them — only app
# code (via seed_policy) populates them. Ponytail: single hard-coded
# rule; add a path list if more read-only routes appear.
_POLICY_DENY_WRITE: list[FilesystemPermission] = [
    FilesystemPermission(operations=["write"], paths=["/policies/"], mode="deny"),
]


def _make_backend(
    store: BaseStore,
    namespace: tuple[str, ...] = AGENT_NAMESPACE,
) -> CompositeBackend:
    """Build the filesystem backend with per-user memory isolation
    and a shared read-only /policies/ route.

    ``/memories/`` is backed by the LangGraph store namespaced via
    :func:`_make_memory_namespace` (per-caller by default; shared when
    ``Settings.memory_scope == "agent"``). ``/policies/`` is backed by
    the same store on the fixed :data:`POLICY_NAMESPACE` and protected
    by :data:`_POLICY_DENY_WRITE` at the agent level. All other paths
    use the in-process StateBackend.

    The store is injected directly into ``StoreBackend`` so writes do
    not depend on runtime context resolution.
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(
                store=store,
                namespace=lambda rt, _ns=namespace: _make_memory_namespace(_ns),  # type: ignore[misc]
            ),
            "/policies/": StoreBackend(
                store=store,
                namespace=lambda rt: POLICY_NAMESPACE,
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
    semantic_tool: BaseTool | None = None,
    context_schema: type | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    backend = _make_backend(store) if store is not None else None
    all_tools: list[BaseTool] = list(tools)
    if episodic_tool is not None:
        all_tools.append(episodic_tool)
    if semantic_tool is not None:
        all_tools.append(semantic_tool)
    search_tool = make_search_threads_tool(make_postgres_search_fn(settings))
    if search_tool is not None:
        all_tools.append(search_tool)
    middlewares = _build_middlewares(settings)
    # Wire the @dynamic_prompt middleware to inject runtime caller context.
    # This is appended after all other middlewares so it runs closest to the
    # model call, ensuring the caller identity is visible in every LLM turn.
    middlewares.append(make_caller_context_middleware(system_prompt))
    return create_deep_agent(
        name="ossia",
        model=model,
        tools=all_tools,
        system_prompt=system_prompt,
        skills=[str(Path(__file__).resolve().parent.parent.parent / "docs" / "skills")],
        middleware=middlewares,
        checkpointer=checkpointer,
        interrupt_on=_interrupt_config(settings, checkpointer),  # type: ignore[arg-type]
        subagents=subagents,  # type: ignore[arg-type]
        store=store,
        backend=backend,
        memory=[AGENTS_MEMORY_KEY] if store is not None else None,
        permissions=_POLICY_DENY_WRITE,
        context_schema=context_schema,
    )


def build_agent(
    settings: Settings | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
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
) -> AsyncGenerator[CompiledStateGraph[Any, Any, Any, Any], None]:
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
    if settings.redis_url:
        # Redis store from langgraph-checkpoint-redis. Replaces the
        # Postgres store when REDIS_URL is set; key-value by default
        # — pass an IndexConfig to enable vector RAG (see Settings).
        store_cm = get_redis_store(settings)
        store = await store_cm.__aenter__()
    elif settings.postgres_url:
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
    # Semantic recall over the store's vector index. The factory
    # returns None for non-Redis stores or when vector indexing is
    # disabled (Settings.enable_vector_index=False). The agent just
    # doesn't have this tool in that mode.
    semantic_tool = make_semantic_recall_tool(store, settings)
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
            semantic_tool=semantic_tool,
            context_schema=OssiaContext,
        )
    finally:
        if store_cm is not None:
            await store_cm.__aexit__(None, None, None)
        if toolkit is not None:
            await toolkit.__aexit__(None, None, None)


def stream_agent_events(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    thread_id: str,
    input_message: dict[str, Any],
) -> Any:
    config = {"configurable": {"thread_id": thread_id}}
    return graph.astream_events(  # type: ignore[call-overload]
        {"messages": [input_message]},
        config,  # pyright: ignore[reportArgumentType]
        version="v2",
    )
