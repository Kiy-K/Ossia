"""Environment and runtime configuration for Ossia."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
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

    # Agent behavior
    max_revision_loops: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Hard cap on response revision loops before forcing finalization.",
    )
    enable_human_review: bool = Field(
        default=True,
        description="Pause for human approval before sending responses.",
    )

    # MCP servers
    mcp_config_path: str = Field(
        default=".mcp.json",
        description="Path to MCP server configuration file.",
    )
    mcp_connect_timeout: float = Field(
        default=10.0,
        gt=0,
        description="Seconds to wait for each MCP server to connect before skipping.",
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
