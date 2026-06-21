"""Nebius-specific adapter for model endpoints and job runners.

This module isolates Nebius SDK / REST usage from portable agent logic so the
core agent can run on any provider.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_nebius import ChatNebius

from ossia.config import Provider, Settings, get_settings

NEBIUS_BASE_URL = "https://api.studio.nebius.ai/v1/"


def create_nebius_chat_model(
    settings: Settings | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Create a LangChain chat model backed by a Nebius Serverless Endpoint.

    Args:
        settings: Optional settings instance.
        **kwargs: Extra arguments forwarded to ChatNebius.

    Returns:
        Configured Nebius chat model.
    """
    settings = settings or get_settings()
    if settings.provider != Provider.NEBIUS:
        raise ValueError(f"Provider must be '{Provider.NEBIUS}' to use Nebius adapter")

    api_key = settings.nebius_api_key or os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise ValueError("NEBIUS_API_KEY is required for Nebius provider")

    return ChatNebius(
        model=settings.model,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        api_key=api_key,
        base_url=NEBIUS_BASE_URL,
        **kwargs,
    )


def get_nebius_job_manifest(image: str, command: list[str]) -> dict[str, Any]:
    """Return a minimal Nebius Job manifest for batch evaluation.

    Args:
        image: Container image URI.
        command: Entrypoint command list.

    Returns:
        Job manifest dictionary.
    """
    return {
        "apiVersion": "batch.nebius.ai/v1",
        "kind": "Job",
        "metadata": {"name": "ossia-eval-job", "labels": {"app": "ossia"}},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "eval",
                            "image": image,
                            "command": command,
                            "envFrom": [{"secretRef": {"name": "ossia-env"}}],
                        }
                    ],
                    "restartPolicy": "Never",
                }
            },
            "backoffLimit": 2,
        },
    }
