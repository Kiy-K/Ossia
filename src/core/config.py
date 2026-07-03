"""Environment and runtime configuration for Ossia."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Provider(StrEnum):
    """Supported model providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    NEBIUS = "nebius"
    OPENROUTER = "openrouter"
    FIREWORKS = "fireworks"
    BASETEN = "baseten"
    OLLAMA = "ollama"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Model provider configuration
    provider: Provider = Field(
        default=Provider.OPENROUTER,
        description="Model provider to use for the agent.",
    )
    model: str = Field(
        default="openai/gpt-4o-mini",
        description="Model identifier passed to the provider.",
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature.",
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        description="Maximum tokens per generation.",
    )

    # API keys (loaded from env)
    openai_api_key: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None)
    google_api_key: str | None = Field(default=None)
    nebius_api_key: str | None = Field(default=None)
    openrouter_api_key: str | None = Field(default=None)
    fireworks_api_key: str | None = Field(default=None)
    baseten_api_key: str | None = Field(default=None)
    tavily_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TAVILY_API_KEY", "OSSIA_TAVILY_API_KEY"),
        description=(
            "Tavily API key for the internet_search / fetch_url / qna_search "
            "tools. Read from TAVILY_API_KEY (preferred) or OSSIA_TAVILY_API_KEY. "
            "When unset the web-search tools degrade to the DuckDuckGo fallback "
            "or fail loudly for URL fetches."
        ),
    )
    browser_use_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BROWSER_USE_API_KEY", "OSSIA_BROWSER_USE_API_KEY"),
        description=(
            "browser-use API key for the web-reviewer subagent. Read from "
            "BROWSER_USE_API_KEY (preferred) or OSSIA_BROWSER_USE_API_KEY. "
            "REQUIRED only when browser_use_local=False (uses the browser-use "
            "cloud browser, which needs the key) OR browser_use_llm="
            '"browser-use" (uses their LLM gateway). NOT required for the '
            "common path: local Chromium + the main provider's LLM. The "
            "web-reviewer subagent is only wired when (browser-use installed) "
            "AND (BROWSER_USE_API_KEY set OR browser_use_local=True)."
        ),
    )
    browser_use_llm: str = Field(
        default="main",
        description=(
            'Which LLM to use for the browser-use agent. ``"main"`` (default) '
            "uses the same provider/model as the main agent (e.g. openrouter/"
            'gpt-4o-mini). ``"browser-use"`` uses ``ChatBrowserUse()`` from the '
            "browser-use SDK, which routes through their LLM gateway — requires "
            "a paid browser-use account (the free tier returns 403 from the "
            "gateway). Set via env: BROWSER_USE_LLM=main|browser-use."
        ),
    )
    browser_use_local: bool = Field(
        default=False,
        description=(
            "Run the browser-use agent against a LOCAL Chromium instead of "
            "the browser-use cloud browser. Local mode is FREE (no "
            "BROWSER_USE_API_KEY needed for the browser itself; you only pay "
            "for the LLM calls) and works against sites the cloud browser's "
            "free tier can't reach due to anti-bot. One-time setup: "
            "``uvx browser-use install`` downloads Chromium (~200MB). "
            "Set via env: BROWSER_USE_LOCAL=true."
        ),
    )
    browser_use_chromium_sandbox: bool = Field(
        default=True,
        description=(
            "Enable Chromium's process sandbox for the local browser. Set to "
            "False when running inside Docker or as root, where the kernel "
            "sandbox can't be set up. Set via env: "
            "BROWSER_USE_CHROMIUM_SANDBOX=true|false."
        ),
    )
    browser_use_user_data_dir: str | None = Field(
        default=None,
        description=(
            "Persistent profile directory for the local Chromium. Cookies, "
            "localStorage, and session data survive across runs so the agent "
            "stays logged in. Default: a per-process temp dir (no "
            "persistence). Set via env: BROWSER_USE_USER_DATA_DIR=/path/to/dir."
        ),
    )

    # LangSmith tracing
    langsmith_tracing: bool = Field(default=False)
    langsmith_endpoint: str | None = Field(default=None)
    langsmith_api_key: str | None = Field(default=None)
    langsmith_project: str = Field(default="ossia")

    # Postgres persistence
    postgres_url: str | None = Field(
        default=None,
        description="Postgres connection string for checkpointing. Required in production.",
    )

    # Redis (optional). All Redis-backed features degrade gracefully when unset.
    redis_url: str | None = Field(
        default=None,
        description=(
            "Redis connection URL (e.g. redis://localhost:6379/0). "
            "When unset, tool-result caching and the seed_memory write lock "
            "are no-ops. Set to enable surface #1 (cache) and surface #4 (lock)."
        ),
    )

    # Async subagents
    enable_async_subagents: bool = Field(
        default=True,
        description="Enable background async subagents for long-running tasks.",
    )

    # Plugin system
    ossia_plugins_dir: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OSSIA_PLUGINS_DIR", "OSSIA_OSSIA_PLUGINS_DIR"),
        description=(
            "Directory for user-installed plugins. Each ``.py`` file and "
            "each subpackage with ``__init__.py`` is treated as a plugin "
            "if it defines a top-level ``register(api)`` function. The "
            "bundled ``plugins/`` dir at the repo root is always "
            "scanned; this setting adds an additional location. Defaults "
            "to ``<repo>/plugins_local/`` (created on first load). Set "
            "via env: OSSIA_PLUGINS_DIR=/path/to/dir."
        ),
    )

    # Agent behavior
    max_revision_loops: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Hard cap on response revision loops before forcing finalization.",
    )
    tool_call_limit: int = Field(
        default=25,
        ge=1,
        le=200,
        description=(
            "Maximum total tool calls per agent run before forcing "
            "finalization. Prevents runaway agents that spin on external I/O."
        ),
    )
    enable_human_review: bool = Field(
        default=True,
        description="Pause for human approval before sending responses.",
    )

    # Retry tool middleware
    retry_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Maximum number of retry attempts for external tool calls. "
            "Tools in _EXTERNAL_TOOLS are retried this many times with "
            "exponential backoff before giving up."
        ),
    )
    retry_initial_interval: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description=(
            "Base delay in seconds between retry attempts. Multiplied by "
            "backoff_factor after each failure."
        ),
    )
    retry_backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description=(
            "Multiplier applied to the retry delay after each consecutive "
            "failure. 2.0 = double the wait each time."
        ),
    )

    # Code interpreter (QuickJS sandbox)
    code_interpreter_timeout: float = Field(
        default=5.0,
        ge=0.5,
        le=60.0,
        description=(
            "Maximum seconds allowed for a single Code Interpreter (QuickJS) "
            "execution. Longer scripts are terminated."
        ),
    )
    code_interpreter_max_ptc_calls: int = Field(
        default=32,
        ge=1,
        le=200,
        description=(
            "Maximum number of PTC (permit-to-call) tool invocations a Code "
            "Interpreter script may make. Prevents runaway script loops."
        ),
    )

    # Model retry middleware
    model_retry_max_attempts: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Maximum number of retry attempts for transient LLM provider "
            "failures (rate limits, timeouts, server errors). "
            "ModelRetryMiddleware retries this many times before giving up."
        ),
    )
    model_retry_initial_interval: float = Field(
        default=0.5,
        ge=0.1,
        le=30.0,
        description=(
            "Base delay in seconds between model retry attempts. "
            "Multiplied by backoff_factor after each failure."
        ),
    )
    model_retry_backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description=(
            "Multiplier applied to the model retry delay after each "
            "consecutive failure. 2.0 = double the wait each time."
        ),
    )

    # Model fallback middleware
    fallback_provider: Provider | None = Field(
        default=None,
        description=(
            "Provider for the fallback model when the primary model call "
            "fails with a transient error. Set alongside fallback_model "
            "to enable ModelFallbackMiddleware. If unset the fallback is "
            "not wired."
        ),
    )
    fallback_model: str | None = Field(
        default=None,
        description=(
            "Model identifier for the fallback LLM. When set (alongside "
            "fallback_provider), ModelFallbackMiddleware is wired to "
            "switch to this model on transient provider failures."
        ),
    )

    # Circuit breaker
    circuit_breaker_failure_threshold: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Consecutive failures before the circuit breaker opens. "
            "After this many failures on an external tool, subsequent "
            "calls fail fast instead of attempting the call."
        ),
    )
    circuit_breaker_recovery_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description=(
            "Seconds to wait before the circuit breaker attempts a probe. "
            "After a circuit opens, the first call after this timeout is "
            "allowed as a probe to test whether the service has recovered."
        ),
    )

    # MCP servers
    mcp_config_path: str = Field(
        default=".mcp.json",
        description="Path to MCP server configuration file.",
    )
    mcp_connect_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=60.0,
        description=(
            "Seconds to wait for each MCP server to connect and list its "
            "tools before skipping it. Servers that exceed this are treated "
            "as unreachable and degraded gracefully. Bounded above so an env "
            "misconfiguration (MCP_CONNECT_TIMEOUT) cannot block startup "
            "indefinitely."
        ),
    )

    # Ollama
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Base URL for the Ollama server (ollama provider only).",
    )

    # Memory scope: "user" isolates per API key (safe default),
    # "agent" shares one memory across all callers (matches the
    # DeepAgents "agent-scoped memory" pattern; use for shared
    # knowledge/identity the agent should learn across all users).
    memory_scope: Literal["user", "agent"] = Field(
        default="user",
        description=(
            "Memory namespace scope. 'user' = per-caller (default, isolated). "
            "'agent' = shared across all callers (assistant_id-scoped, like the DeepAgents docs)."
        ),
    )

    # Knowledge base: comma-separated list of source URLs. Each URL
    # is fetched at startup and stored in Redis (when REDIS_URL is
    # set) as one document. Title = first H1 in body (or URL stem
    # if none); source = URL; content = body. Empty = empty KB =
    # tool falls back to web search. The fetcher is plain HTTP GET
    # via httpx; bodies are treated as markdown.
    kb_source_urls: str = Field(
        default="",
        description=(
            "Comma-separated list of URLs whose markdown bodies are "
            "fetched at startup and stored in Redis as the knowledge "
            "base. Each URL = one document. Empty = no KB."
        ),
    )

    # Vector index (RAG): when REDIS_URL is set, the store is
    # configured with a RediSearch vector index that uses the local
    # Ollama server to embed stored items. Set the model to match
    # what you have pulled (``ollama list``). Default matches
    # Google's embeddinggemma (768-dim, gemma3 family).
    embedding_model: str = Field(
        default="embeddinggemma",
        description=(
            "Ollama embedding model name. Must match a model pulled "
            "locally (use ``ollama pull <name>``). Default is Google's "
            "embeddinggemma, 768-dim."
        ),
    )
    embedding_dim: int = Field(
        default=768,
        description=(
            "Embedding vector dimensions. Must match the model's "
            "output dim. Default 768 matches embeddinggemma."
        ),
    )
    enable_vector_index: bool = Field(
        default=True,
        description=(
            "When REDIS_URL is set, auto-create a RediSearch vector "
            "index on the store using the configured embedding model. "
            "Disable for key-value-only memory (no RAG)."
        ),
    )

    # Tool result cache: langgraph-redis ``ToolResultCacheMiddleware``
    # caches exact-match tool results in Redis. Only takes effect
    # when ``REDIS_URL`` is set; otherwise the helper is skipped.
    # Side-effect tools are excluded by the library's default
    # prefix list (``send_``, ``delete_``, ``create_``, ``update_``,
    # ``remove_``, ``write_``, ``post_``, ``put_``, ``patch_``,
    # plus ``edit_`` which we add).
    enable_tool_cache: bool = Field(
        default=True,
        description=(
            "Cache tool results in Redis (when REDIS_URL is set). "
            "Disable for ephemeral runs or to force fresh tool calls."
        ),
    )
    tool_cache_ttl_seconds: int = Field(
        default=3600,
        description=(
            "TTL for cached tool results, in seconds. Default 1h; "
            "tighten to 60s for tools whose outputs change often "
            "(tests, search)."
        ),
    )

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: Any) -> str:
        """Normalize provider string to lower case."""
        if value is None:
            return Provider.OPENROUTER.value
        return str(value).lower().strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
