"""Normalized event types for the Ossia event streaming protocol.

This module defines ``OssiaEvent`` — the single normalized event type
that all event sources (DeepAgent v3 stream, pipeline orchestrators,
async tasks, multimodal artifacts) are converted into before reaching
the SSE serialization layer.

Design rules
------------
- Every event has a globally unique ``id``, a monotonic ``seq``, and an
  ISO-8601 ``timestamp`` so clients can order, deduplicate, and replay.
- ``source`` encodes parent-child relationships as a dot-separated path:
  ``"coordinator"`` for the top-level agent, ``"coordinator.researcher"``
  for a subagent, ``"coordinator.researcher.security-reviewer"`` for a
  nested subagent.
- ``type`` follows a ``<category>_<event>`` convention so clients can
  filter by category prefix or subscribe to specific event types.
- ``data`` is a typed dict whose shape is determined by ``type``.
  The per-type schemas are enumerated below.

Event categories
----------------
- ``message_*``: Coordinator text generation (token-level streaming)
- ``subagent_*``: Subagent lifecycle (spawn, message, complete, fail)
- ``tool_*``: Tool call lifecycle (start, progress, complete, fail)
- ``pipeline_*``: Programmatic pipeline lifecycle (start, step, complete, fail)
- ``async_task_*``: Background async subagent lifecycle
- ``artifact_*``: Multimodal artifact lifecycle
- ``interrupt``: Human-in-the-loop pause
- ``error``: Runtime error
- ``complete``: Run finished
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Event type literals ──────────────────────────────────────────────────────

MessageEventType = Literal[
    "message_started",
    "message_delta",
    "message_completed",
]

SubagentEventType = Literal[
    "subagent_spawned",
    "subagent_message_delta",
    "subagent_completed",
    "subagent_failed",
    "subagent_interrupted",
]

ToolEventType = Literal[
    "tool_started",
    "tool_progress",
    "tool_completed",
    "tool_failed",
]

PipelineEventType = Literal[
    "pipeline_started",
    "pipeline_step_started",
    "pipeline_step_completed",
    "pipeline_step_failed",
    "pipeline_completed",
    "pipeline_failed",
]

AsyncTaskEventType = Literal[
    "async_task_started",
    "async_task_updated",
    "async_task_completed",
    "async_task_failed",
    "async_task_cancelled",
]

ArtifactEventType = Literal[
    "artifact_received",
    "artifact_processed",
    "image_analysis_started",
    "image_analysis_completed",
]

SystemEventType = Literal[
    "interrupt",
    "error",
    "complete",
]

EventType = (
    MessageEventType
    | SubagentEventType
    | ToolEventType
    | PipelineEventType
    | AsyncTaskEventType
    | ArtifactEventType
    | SystemEventType
)

# ── Core event envelope ──────────────────────────────────────────────────────


class OssiaEvent(BaseModel):
    """A single normalized event in the Ossia event stream.

    Every event in the system — whether produced by the DeepAgent runtime,
    the orchestrator pipelines, async subagents, or the multimodal layer —
    is normalized into this envelope before reaching the SSE serialization
    layer or the TUI reducer.

    Attributes:
        id: Globally unique event identifier (UUID hex). Stable across
            retries and replay; clients can use this for deduplication.
        seq: Monotonically increasing sequence number within the run.
            Guarantees ordering for clients that cannot trust wall-clock
            timestamps.
        timestamp: ISO-8601 UTC timestamp of when the event was emitted.
        type: Event type discriminator (``message_delta``, ``tool_started``,
            ``subagent_spawned``, etc.). Clients branch on this field.
        source: Dot-separated path identifying the emitter. ``"coordinator"``
            for the top-level agent. Subagents append their name:
            ``"coordinator.researcher"``. Nested subagents extend the path:
            ``"coordinator.researcher.security-reviewer"``.
        thread_id: The LangGraph thread id this event belongs to.
        data: Type-specific payload. The shape is determined by ``type``
            — see the per-type data schemas below.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Globally unique event identifier (UUID hex).",
    )
    seq: int = Field(description="Monotonically increasing sequence number.")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO-8601 UTC timestamp.",
    )
    type: str = Field(description="Event type discriminator.")
    source: str = Field(default="coordinator", description="Dot-separated emitter path.")
    thread_id: str = Field(default="default", description="Thread id for this event.")
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific payload.",
    )


# ── Per-type data shape helpers ──────────────────────────────────────────────
# These are not Pydantic models (to keep the envelope lightweight) but
# documented dict shapes that each ``type`` maps to. The normalizer in
# ``normalizer.py`` constructs these dicts.


def message_started_data(
    role: str, text: str = "", message_id: str | None = None
) -> dict[str, Any]:
    """Payload for ``message_started``: first token of a coordinator message."""
    return {"role": role, "text": text, "id": message_id}


def message_delta_data(role: str, text: str, message_id: str | None = None) -> dict[str, Any]:
    """Payload for ``message_delta``: subsequent tokens."""
    return {"role": role, "text": text, "id": message_id}


def message_completed_data(role: str, text: str, message_id: str | None = None) -> dict[str, Any]:
    """Payload for ``message_completed``: final token with full accumulated text."""
    return {"role": role, "text": text, "id": message_id}


def subagent_spawned_data(name: str, path: list[str]) -> dict[str, Any]:
    """Payload for ``subagent_spawned``: a subagent has started."""
    return {"name": name, "path": path}


def subagent_message_delta_data(name: str, text: str, path: list[str]) -> dict[str, Any]:
    """Payload for ``subagent_message_delta``: a token from a subagent."""
    return {"name": name, "text": text, "path": path}


def subagent_completed_data(
    name: str, result: str | None = None, path: list[str] | None = None
) -> dict[str, Any]:
    """Payload for ``subagent_completed``: a subagent finished successfully."""
    return {"name": name, "result": result, "path": path or []}


def subagent_failed_data(name: str, error: str, path: list[str] | None = None) -> dict[str, Any]:
    """Payload for ``subagent_failed``: a subagent encountered an error."""
    return {"name": name, "error": error, "path": path or []}


def subagent_interrupted_data(name: str, path: list[str] | None = None) -> dict[str, Any]:
    """Payload for ``subagent_interrupted``: a subagent paused for HITL."""
    return {"name": name, "path": path or []}


def tool_started_data(name: str, input_: dict[str, Any]) -> dict[str, Any]:
    """Payload for ``tool_started``: a tool call has been initiated."""
    return {"name": name, "input": input_, "source": "coordinator"}


def tool_progress_data(name: str, output_delta: str | None = None) -> dict[str, Any]:
    """Payload for ``tool_progress``: streaming tool output delta."""
    return {"name": name, "output_delta": output_delta, "source": "coordinator"}


def tool_completed_data(
    name: str, output: Any = None, source: str = "coordinator"
) -> dict[str, Any]:
    """Payload for ``tool_completed``: a tool call returned successfully."""
    return {"name": name, "output": output, "source": source}


def tool_failed_data(name: str, error: str, source: str = "coordinator") -> dict[str, Any]:
    """Payload for ``tool_failed``: a tool call raised an error."""
    return {"name": name, "error": error, "source": source}


def pipeline_started_data(pipeline_type: str, total_steps: int, pipeline_id: str) -> dict[str, Any]:
    """Payload for ``pipeline_started``: a programmatic pipeline began."""
    return {"pipeline_type": pipeline_type, "total_steps": total_steps, "pipeline_id": pipeline_id}


def pipeline_step_started_data(
    pipeline_id: str, step_name: str, step_index: int, total_steps: int
) -> dict[str, Any]:
    """Payload for ``pipeline_step_started``: a pipeline stage began."""
    return {
        "pipeline_id": pipeline_id,
        "step_name": step_name,
        "step_index": step_index,
        "total_steps": total_steps,
    }


def pipeline_step_completed_data(
    pipeline_id: str, step_name: str, step_index: int, result: str | None = None
) -> dict[str, Any]:
    """Payload for ``pipeline_step_completed``: a pipeline stage finished."""
    return {
        "pipeline_id": pipeline_id,
        "step_name": step_name,
        "step_index": step_index,
        "result": result,
    }


def pipeline_step_failed_data(
    pipeline_id: str, step_name: str, step_index: int, error: str
) -> dict[str, Any]:
    """Payload for ``pipeline_step_failed``: a pipeline stage errored."""
    return {
        "pipeline_id": pipeline_id,
        "step_name": step_name,
        "step_index": step_index,
        "error": error,
    }


def pipeline_completed_data(pipeline_id: str, result: str | None = None) -> dict[str, Any]:
    """Payload for ``pipeline_completed``: the pipeline finished."""
    return {"pipeline_id": pipeline_id, "result": result}


def pipeline_failed_data(pipeline_id: str, error: str) -> dict[str, Any]:
    """Payload for ``pipeline_failed``: the pipeline errored."""
    return {"pipeline_id": pipeline_id, "error": error}


def async_task_data(
    event: str,
    task_id: str,
    agent_name: str,
    status: str,
    tasks: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Payload for async task lifecycle events."""
    return {
        "event": event,
        "task_id": task_id,
        "agent_name": agent_name,
        "status": status,
        "tasks": tasks or [],
        "error": error,
    }


def artifact_data(
    artifact_id: str,
    art_type: str,
    filename: str,
    event: str,
    analysis_state: str,
    summary: str | None = None,
) -> dict[str, Any]:
    """Payload for artifact lifecycle events."""
    return {
        "artifact_id": artifact_id,
        "type": art_type,
        "filename": filename,
        "event": event,
        "analysis_state": analysis_state,
        "summary": summary,
    }


def interrupt_data(interrupts: list[dict[str, Any]]) -> dict[str, Any]:
    """Payload for ``interrupt``: run paused for human review."""
    return {"interrupts": interrupts}


def error_data(error: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Payload for ``error``: a runtime error occurred."""
    return {"error": error, "details": details or {}}


def complete_data(output: dict[str, Any], interrupted: bool = False) -> dict[str, Any]:
    """Payload for ``complete``: the run finished."""
    return {"output": output, "interrupted": interrupted}
