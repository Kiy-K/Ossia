"""State reducers for the normalized event stream.

Reducers accumulate ``OssiaEvent`` objects into a structured state tree
that a TUI (or any consumer) can render without complex parsing. The
state tree mirrors the hierarchical agent/subagent/tool structure:

.. code-block::

    {
      "thread_id": "...",
      "state": "running" | "completed" | "interrupted" | "error",
      "coordinator": {
        "messages": [...],
        "tools": [...],
        "subagents": {
          "researcher": {
            "state": "completed",
            "messages": [...],
            "tools": [...],
          }
        }
      }
    }

Each reducer is a pure function: ``(state, event) -> new_state``.
The initial state is produced by ``initial_state()``.
"""

from __future__ import annotations

from typing import Any

from core.events.types import OssiaEvent

# ── Type aliases ─────────────────────────────────────────────────────────────

AgentState = dict[str, Any]
SubagentTree = dict[str, "SubagentNode"]
SubagentNode = dict[str, Any]


def initial_state(thread_id: str = "default") -> AgentState:
    """Return the initial (empty) state tree for a run.

    Args:
        thread_id: The thread id to scope state to.

    Returns:
        A dict representing the initial renderable state.
    """
    return {
        "thread_id": thread_id,
        "state": "running",
        "error": None,
        "interrupted": False,
        "coordinator": _empty_agent_node(),
        "subagents": {},
    }


def _empty_agent_node() -> AgentState:
    return {
        "messages": [],
        "tools": [],
        "subagents": {},
        "state": "idle",
    }


# ── Subagent path parsing ────────────────────────────────────────────────────


def _parse_source(source: str) -> list[str]:
    """Split a dot-separated source path into components.

    ``"coordinator"`` -> ``[]``
    ``"coordinator.researcher"`` -> ``["researcher"]``
    ``"coordinator.researcher.security-reviewer"`` -> ``["researcher", "security-reviewer"]``
    """
    parts = source.split(".")
    if parts[0] == "coordinator":
        parts = parts[1:]
    return parts


def _navigate_to_target(state: AgentState, source: str) -> AgentState:
    """Navigate into the state tree to the node at ``source``.

    Traverses or creates the subagent tree along the dot-separated path.
    Returns the leaf node dict. Mutates the tree in place — the caller
    should deep-copy first if isolation is needed.

    Example: ``"coordinator.researcher"`` navigates to
    ``state["subagents"]["researcher"]``, creating it if absent.
    ``"coordinator.researcher.security-reviewer"`` navigates to
    ``state["subagents"]["researcher"]["subagents"]["security-reviewer"]``.
    ``"coordinator"`` (empty path) returns the coordinator node.
    """
    parts = _parse_source(source)
    if not parts:
        return state["coordinator"]

    # Walk the subagent tree, creating nodes as needed
    current: AgentState = state
    for i, part in enumerate(parts):
        subagents = current.setdefault("subagents", {})
        if part not in subagents:
            subagents[part] = _empty_agent_node()
            subagents[part]["name"] = part
        if i == len(parts) - 1:
            return subagents[part]
        current = subagents[part]

    return state["coordinator"]  # fallback (should not reach here)


# ── Main reducer ─────────────────────────────────────────────────────────────


def reduce_event(state: AgentState, event: OssiaEvent) -> AgentState:
    """Accumulate a single ``OssiaEvent`` into the state tree.

    This is a pure-ish function: it creates a shallow copy of the relevant
    parts of the state tree. For production use with high-volume streams,
    consider using ``apply_events`` which batches multiple events.

    Args:
        state: The current state tree.
        event: The event to reduce into the tree.

    Returns:
        A new state tree with the event applied.
    """
    # Copy state to avoid mutating the input
    state = {**state, "coordinator": {**state.get("coordinator", _empty_agent_node())}}
    state["subagents"] = {**state.get("subagents", {})}

    etype = event.type
    data = event.data
    source = event.source

    if etype == "message_delta":
        node = _navigate_to_target(state, source)
        if not node.get("pending_message"):
            node["pending_message"] = data.get("text", "")
        else:
            node["pending_message"] += data.get("text", "")

    elif etype == "message_completed":
        node = _navigate_to_target(state, source)
        text = data.get("text", "")
        node.setdefault("messages", []).append({
            "role": data.get("role", "ai"),
            "content": text,
        })
        node["pending_message"] = None

    elif etype == "subagent_spawned":
        name = data.get("name", "unknown")
        path = data.get("path", [])
        subagents = state["subagents"]
        # Navigate/buid the subagent tree
        target = subagents
        for i, p in enumerate(path):
            if p not in target:
                target[p] = _empty_agent_node()
                target[p]["name"] = p
            if i == len(path) - 1:
                target[p]["state"] = "running"
            target = target[p]
            if "subagents" not in target:
                target["subagents"] = {}

    elif etype in ("subagent_completed", "subagent_success"):
        name = data.get("name", "unknown")
        # Find and update the subagent node
        for _sname, sdata in state["subagents"].items():
            if sdata.get("name") == name:
                sdata["state"] = "completed"
                break

    elif etype == "subagent_failed":
        name = data.get("name", "unknown")
        for _sname, sdata in state["subagents"].items():
            if sdata.get("name") == name:
                sdata["state"] = "error"
                sdata["error"] = data.get("error", "unknown error")
                break

    elif etype == "tool_started":
        node = _navigate_to_target(state, source)
        node.setdefault("tools", []).append({
            "name": data.get("name", "unknown"),
            "input": data.get("input", {}),
            "state": "running",
        })

    elif etype == "tool_completed":
        node = _navigate_to_target(state, source)
        tool_name = data.get("name", "")
        for t in node.get("tools", []):
            if t["name"] == tool_name and t.get("state") == "running":
                t["state"] = "completed"
                t["output"] = data.get("output")
                break

    elif etype == "tool_failed":
        node = _navigate_to_target(state, source)
        tool_name = data.get("name", "")
        for t in node.get("tools", []):
            if t["name"] == tool_name and t.get("state") == "running":
                t["state"] = "failed"
                t["error"] = data.get("error", "unknown error")
                break

    elif etype == "async_task_started":
        state.setdefault("async_tasks", []).append({
            "task_id": data.get("task_id", ""),
            "agent_name": data.get("agent_name", ""),
            "status": "running",
        })

    elif etype == "async_task_completed":
        for t in state.get("async_tasks", []):
            if t.get("task_id") == data.get("task_id"):
                t["status"] = "completed"

    elif etype == "async_task_failed":
        for t in state.get("async_tasks", []):
            if t.get("task_id") == data.get("task_id"):
                t["status"] = "failed"
                t["error"] = data.get("error", "unknown error")

    elif etype == "async_task_cancelled":
        for t in state.get("async_tasks", []):
            if t.get("task_id") == data.get("task_id"):
                t["status"] = "cancelled"

    elif etype == "interrupt":
        state["interrupted"] = True
        state["state"] = "interrupted"
        state["interrupts"] = data.get("interrupts", [])

    elif etype == "error":
        state["state"] = "error"
        state["error"] = data.get("error", "unknown error")

    elif etype == "complete":
        state["state"] = "interrupted" if data.get("interrupted") else "completed"
        state["interrupted"] = data.get("interrupted", False)

    return state


def apply_events(state: AgentState, events: list[OssiaEvent]) -> AgentState:
    """Apply multiple events to the state tree in order.

    Args:
        state: The initial state tree.
        events: Iterable of ``OssiaEvent`` objects to apply, in order.

    Returns:
        The final state tree after all events have been reduced.
    """
    for event in events:
        state = reduce_event(state, event)
    return state
