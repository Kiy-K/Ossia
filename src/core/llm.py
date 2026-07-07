"""Provider-agnostic chat-model factory.

Factored out of ``core.agent`` so the browser-use tool (and any other
module that needs an LLM) can construct a chat model without importing
the full agent graph.

Ponytail: single function, no helper class. If we ever need to share
instances across tools, add an LRU cache around ``create_chat_model``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from core.config import Provider, Settings, get_settings


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
    # ── Nvidia NIM ──────────────────────────────────────────────────────────
    # Uses the native ``ChatNVIDIA`` from ``langchain-nvidia-ai-endpoints``.
    # Defaults to the free hosted endpoint (https://integrate.api.nvidia.com/v1);
    # override ``nim_base_url`` for local NIM containers. The API key is
    # ``NVIDIA_API_KEY`` in the environment or ``nim_api_key`` in settings —
    # local containers don't need one.
    if provider == Provider.NIM:
        from langchain_nvidia_ai_endpoints import ChatNVIDIA

        kwargs: dict[str, Any] = {
            "model": settings.model,
            "temperature": settings.temperature,
        }
        # ChatNVIDIA uses ``max_completion_tokens`` for newer endpoints.
        # Set it regardless — older NIMs silently cap at their own limit.
        if settings.max_tokens:
            kwargs["max_completion_tokens"] = settings.max_tokens
        if settings.nim_api_key:
            kwargs["nvidia_api_key"] = settings.nim_api_key
        if settings.nim_base_url:
            kwargs["base_url"] = settings.nim_base_url
        return ChatNVIDIA(**kwargs)  # type: ignore[arg-type]
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
            "streaming": True,
            "api_key": api_key,
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
