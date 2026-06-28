"""Environment and runtime configuration for Ossia."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any

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

    # Async subagents
    enable_async_subagents: bool = Field(
        default=True,
        description="Enable background async subagents for long-running tasks.",
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
