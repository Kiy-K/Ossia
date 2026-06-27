"""Serialize ``OssiaEvent`` objects to Server-Sent Events (SSE) format.

This module converts the normalized ``OssiaEvent`` objects from the
normalizer into raw SSE byte/string payloads suitable for a FastAPI
``StreamingResponse``.

The SSE format follows the ``text/event-stream`` content type::

    event: <event_type>
    data: <json-encoded event>
    \n

Clients can subscribe to specific event types via
``EventSource.addEventListener(<type>, handler)``.

For backward compatibility with existing TUI consumers, the serializer
also provides a ``to_legacy_stream_event`` function that converts an
``OssiaEvent`` to the existing ``StreamEvent`` schema (with ``kind``
and ``data`` fields).
"""

from __future__ import annotations

from core.events.types import OssiaEvent
from core.schemas import StreamEvent


def serialize_sse(event: OssiaEvent, *, include_seq: bool = True) -> str:
    """Serialize an ``OssiaEvent`` to an SSE string.

    The result is a complete SSE message with double newline terminator::

        event: message_delta
        data: {"id":"abc123","seq":1,"type":"message_delta",...}
        \n
    The trailing ``\n\n`` is the SSE event delimiter. Multiple events
    can be concatenated directly.

    Args:
        event: The normalized event to serialize.
        include_seq: When True, include ``seq`` in the SSE ``id:`` field
            so clients can track the last seen sequence for replay.

    Returns:
        An SSE message string ending with ``\n\n``.
    """
    lines: list[str] = []
    lines.append(f"event: {event.type}")
    if include_seq:
        lines.append(f"id: {event.seq}")
    lines.append(f"data: {event.model_dump_json()}")
    # SSE delimiter: blank line between events
    lines.append("")
    return "\n".join(lines) + "\n"


def serialize_json(event: OssiaEvent) -> str:
    """Serialize an ``OssiaEvent`` to a standalone JSON string.

    Useful for non-SSE transports (WebSocket, Server-Sent Events with
    JSON framing, batch logging).

    Args:
        event: The normalized event to serialize.

    Returns:
        A JSON string.
    """
    return event.model_dump_json()


def to_ossia_kind(ossia_type: str) -> str:
    """Map an ``OssiaEvent`` type to the closest ``StreamEvent.kind``.

    This mapping is used for backward compatibility with the existing
    ``StreamEvent`` wire contract. New event types map to ``"protocol"``
    as a fallback.

    Args:
        ossia_type: The ``event.type`` value (e.g. ``"message_delta"``).

    Returns:
        The ``StreamEvent.kind`` value (e.g. ``"message"``).
    """
    if ossia_type.startswith("message_"):
        return "message"
    if ossia_type.startswith("tool_"):
        return "tool_call"
    if ossia_type.startswith("subagent_"):
        return "subagent"
    if ossia_type.startswith("async_task_"):
        return "async_task"
    if ossia_type.startswith("pipeline_"):
        return "pipeline"
    if ossia_type.startswith("artifact_"):
        return "artifact"
    if ossia_type == "interrupt":
        return "interrupt"
    if ossia_type == "complete":
        return "complete"
    if ossia_type == "error":
        return "protocol"
    return "protocol"


def to_legacy_stream_event(event: OssiaEvent) -> StreamEvent:
    """Convert an ``OssiaEvent`` to the existing ``StreamEvent`` wire schema.

    This provides backward compatibility for clients that consume the
    old ``kind``-based SSE format. New clients should consume the
    normalized ``OssiaEvent`` format directly for richer event types.

    Args:
        event: The normalized event to convert.

    Returns:
        A ``StreamEvent`` with ``kind`` derived from ``event.type``
        and ``data`` from ``event.data``.
    """
    return StreamEvent(
        kind=to_ossia_kind(event.type),  # type: ignore[arg-type]
        seq=event.seq,
        data=event.data,
    )
