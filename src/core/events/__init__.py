"""Normalized event protocol for Ossia.

This package defines the normalized event schema and the normalizer that
converts raw DeepAgent v3 stream events into a stable, typed event format.

Architecture::

    Raw DeepAgent v3 Stream
         │
         ▼
    EventNormalizer (normalizer.py)
         │  Converts stream.messages, stream.tool_calls,
         │  stream.subagents, stream.values into OssiaEvent objects
         ▼
    OssiaEvent (types.py)
         │  Typed envelope with id, seq, timestamp, source, type, data
         ├──▶ SSESerializer (serializers.py) → SSE text/event-stream
         ├──▶ LegacyAdapter (serializers.py) → StreamEvent (backward compat)
         └──▶ StateReducer (reducers.py) → Renderable state tree

Design principles
-----------------
1. Every event has an ``id`` (UUID), ``seq`` (monotonic), and ``timestamp``
   for ordering, deduplication, and replay.
2. ``source`` encodes parent-child relationships: ``"coordinator"``,
   ``"coordinator.researcher"``, ``"coordinator.researcher.security-reviewer"``.
3. No raw DeepAgent structures leak into the normalized format.
4. The normalizer is decoupled from the serialization layer — SSE is one
   output format; WebSocket, JSON-lines, or batch logging are equally viable.
5. The reducer is a pure function: ``(state, event) -> new_state``.
"""

from core.events.buffer import ThreadEventBuffer, get_thread_event_buffer
from core.events.normalizer import EventNormalizer
from core.events.reducers import apply_events, initial_state, reduce_event
from core.events.serializers import (
    serialize_json,
    serialize_sse,
    to_legacy_stream_event,
    to_ossia_kind,
)
from core.events.types import (
    OssiaEvent,
    artifact_data,
    complete_data,
    error_data,
    interrupt_data,
    message_completed_data,
    message_delta_data,
    message_started_data,
    pipeline_completed_data,
    pipeline_failed_data,
    pipeline_started_data,
    pipeline_step_completed_data,
    pipeline_step_failed_data,
    pipeline_step_started_data,
    subagent_completed_data,
    subagent_failed_data,
    subagent_interrupted_data,
    subagent_message_delta_data,
    subagent_spawned_data,
    tool_completed_data,
    tool_failed_data,
    tool_progress_data,
    tool_started_data,
)

__all__ = [
    # Normalizer
    "EventNormalizer",
    # Buffer
    "ThreadEventBuffer",
    "get_thread_event_buffer",
    # Reducers
    "initial_state",
    "reduce_event",
    "apply_events",
    # Serializers
    "serialize_sse",
    "serialize_json",
    "to_legacy_stream_event",
    "to_ossia_kind",
    # Types
    "OssiaEvent",
    # Data helpers
    "message_started_data",
    "message_delta_data",
    "message_completed_data",
    "subagent_spawned_data",
    "subagent_message_delta_data",
    "subagent_completed_data",
    "subagent_failed_data",
    "subagent_interrupted_data",
    "tool_started_data",
    "tool_progress_data",
    "tool_completed_data",
    "tool_failed_data",
    "pipeline_started_data",
    "pipeline_step_started_data",
    "pipeline_step_completed_data",
    "pipeline_step_failed_data",
    "pipeline_completed_data",
    "pipeline_failed_data",
    "artifact_data",
    "interrupt_data",
    "error_data",
    "complete_data",
]
