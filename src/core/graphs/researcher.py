"""Researcher subagent graph: codebase research and repo-wide analysis.

Registered in langgraph.json as a standalone subagent that can be
launched asynchronously via the supervisor's ``start_async_task`` tool.
"""

from __future__ import annotations

from core.graphs._shared import build_graph

graph = build_graph()
