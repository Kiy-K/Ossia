"""Episodic memory tool.

Episodic memory is "records of past experiences: what happened, in what
order, and what the outcome was. Unlike semantic memory (facts and
preferences stored in files like ``AGENTS.md``), episodic memory
preserves the full conversational context so the agent can recall
*how* a problem was solved, not just *what* was learned from it."
(Deep Agents memory docs.)

The supported primitive on a bare ``BaseCheckpointSaver`` is
**per-thread turn recall** (``checkpointer.list(config_with_thread_id)``).
Cross-thread enumeration requires the LangGraph SDK's
``client.threads.search(metadata=...)`` (see
``https://docs.langchain.com/langsmith/use-threads``) or a custom
metadata-aware index.

This module implements the per-thread primitive: a tool that, given a
``thread_id``, returns the most recent turns of that thread. The
caller resolves which thread to recall; the agent typically passes
its own ``thread_id`` (from the runtime config) when the conversation
suggests a recall.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver

_DEFAULT_LIMIT = 5
_SNIPPET_CHARS = 240


def _messages_from_checkpoint(checkpoint: Any) -> list[Any]:
    """Pull the messages channel from a checkpoint."""
    if not isinstance(checkpoint, dict):
        return []
    values = checkpoint.get("channel_values", {}) or {}
    return list(values.get("messages", []) or [])


def _summarize_turns(turns: list[Any]) -> list[dict[str, Any]]:
    """Convert raw messages to a compact ``{role, content}`` shape.

    Roles are normalized to the closed set used by the public API's
    ``ChatMessage`` schema: ``user``, ``assistant``, ``tool``, ``system``.
    LangChain's ``HumanMessage.type == "human"`` and ``AIMessage.type
    == "ai"`` are mapped to the API's vocabulary.
    """
    out: list[dict[str, Any]] = []
    for m in turns:
        role = getattr(m, "type", None) or "unknown"
        if role in {"human", "user"}:
            role = "user"
        elif role in {"ai", "assistant"}:
            role = "assistant"
        elif role in {"tool", "function"}:
            role = "tool"
        elif role in {"system"}:
            role = "system"
        content = str(getattr(m, "content", ""))[:_SNIPPET_CHARS]
        out.append({"role": role, "content": content})
    return out


def make_episodic_recall_tool(
    checkpointer: BaseCheckpointSaver[Any] | None,
) -> BaseTool | None:
    """Build a ``recall_thread_turns`` tool, or ``None`` if no
    checkpointer is configured.

    The tool is a no-op (returns ``None``) when the agent runs without
    persistence — e.g. one-off scripts, ephemeral test setups. With a
    checkpointer it returns a LangChain tool that the agent can call
    to recall recent turns of a specific thread.

    Args:
        checkpointer: The agent's checkpointer. ``None`` (no
            persistence configured) yields ``None`` so the caller
            skips wiring the tool.

    Returns:
        A LangChain tool or ``None``.
    """
    if checkpointer is None:
        return None

    @tool
    async def recall_thread_turns(
        thread_id: str,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Return the most recent turns of a thread.

        Args:
            thread_id: The thread to recall. Pass the current
                thread_id to recall this conversation; pass a
                previously-used thread_id to recall across sessions.
            limit: Maximum number of past checkpoints to surface
                (most recent first).

        Returns:
            A dict ``{"thread_id": str, "turns": [{role, content}, ...]}``.
            Empty ``turns`` when the thread has no recorded
            checkpoints.
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        try:
            raw = list(checkpointer.list(config, limit=limit))
        except Exception as exc:  # noqa: BLE001
            return {
                "thread_id": thread_id,
                "turns": [],
                "error": f"recall failed: {exc!r}",
            }
        # ``list`` returns newest-first; reverse to chronological for
        # human/LLM readability.
        raw.reverse()
        turns: list[dict[str, Any]] = []
        for tup in raw[-limit:]:
            turns.extend(_summarize_turns(_messages_from_checkpoint(tup.checkpoint)))
        return {"thread_id": thread_id, "turns": turns}

    return recall_thread_turns
