"""Supervisor graph: the main Ossia concierge agent.

Registered in langgraph.json as the entry point for async subagent
deployments. Builds the full agent with sync subagents, middleware
stack (interpreter, retry, revision cap), and async subagent tools.

The ``graph`` variable is a module-level ``CompiledStateGraph`` built
eagerly. The LangGraph CLI reads ``module:graph`` from langgraph.json
by inspecting ``module.__dict__``, so ``__getattr__``-based lazy
construction is not visible to it.
"""

from __future__ import annotations

import os
from typing import Any

from core.agent import build_agent
from core.config import Provider, Settings, get_settings


def _build_graph() -> Any:
    """Build the supervisor graph from environment settings.

    Falls back to a test-safe stub when API keys are unavailable
    (e.g. CI environments without .env).
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


graph = _build_graph()
