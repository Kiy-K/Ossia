"""Shared helpers for LangGraph platform graph modules.

Each graph module registered in ``langgraph.json`` needs a module-level
``graph`` variable. The ``_build_graph()`` function here is the common
implementation shared by all four graph modules (supervisor, researcher,
tester, auditor) to avoid duplicating the same stub/fallback logic.
"""

from __future__ import annotations

import os
from typing import Any

from core.agent import build_agent
from core.config import Provider, Settings, get_settings


def build_graph() -> Any:
    """Build a LangGraph ``CompiledStateGraph`` from environment settings.

    Falls back to a test-safe stub when API keys are unavailable
    (e.g. CI environments without ``.env``).

    Returns:
        A ``CompiledStateGraph`` (or a stub agent for CLI detection).
    """
    try:
        return build_agent(settings=get_settings())
    except (ValueError, RuntimeError):
        # If API keys are missing, build a stub for the CLI to detect.
        # The CLI needs a module-level ``graph`` variable to start.
        # Without API keys, the real agent can't run, but the CLI
        # at least sees the structure.
        os.environ.setdefault("OSSIA_API_KEY", "dev")
        os.environ.setdefault("ENABLE_HUMAN_REVIEW", "false")
        stub_settings = Settings(
            provider=Provider.OPENROUTER,
            model="openai/gpt-4o-mini",
            openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "sk-stub"),
            enable_human_review=False,
        )
        return build_agent(settings=stub_settings)
