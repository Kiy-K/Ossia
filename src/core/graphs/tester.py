"""Tester subagent graph: test suite execution and validation.

Registered in langgraph.json as a standalone subagent that can be
launched asynchronously via the supervisor's ``start_async_task`` tool.
"""

from __future__ import annotations

import os
from typing import Any

from core.agent import build_agent
from core.config import Provider, Settings, get_settings


def _build_graph() -> Any:
    try:
        return build_agent(settings=get_settings())
    except (ValueError, RuntimeError):
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
