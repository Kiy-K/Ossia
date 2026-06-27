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

from core.graphs._shared import build_graph

graph = build_graph()
