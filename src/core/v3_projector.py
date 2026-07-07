"""Thin SSE projector for Deep Agents v3 ``stream_events``.

Replaces the 782-line ``EventNormalizer`` with a minimalist projection:
iterate each v3 projection concurrently, serialize each item to its own SSE
channel. No custom event types, no type taxonomy, no message boundary
tracking — just the raw v3 projection items streamed onto SSE channels.

Architecture::

    stream.messages  ──→ sse_bytes("messages", item)  ╸
    stream.tool_calls ──→ sse_bytes("tool_calls", item)│
    stream.subagents  ──→ sse_bytes("subagents", item) │ → asyncio.Queue
    stream.values     ──→ sse_bytes("values", item)   ╸
    stream.interrupts / stream.output                   → control events

The frontend adds one subscriber per SSE channel — no switch statement,
no sideChannelStore, no full-message-array reconciliation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)

_SENTINEL = None


async def project_v3_stream(
    stream: Any,
    *,
    thread_id: str = "default",
) -> AsyncGenerator[dict[str, Any], None]:
    """Project a v3 stream directly to SSE-channel-keyed dicts.

    One channel per v3 projection. Each yielded dict has ``channel``
    and ``data`` keys; the API layer serializes to SSE::

        event: <channel>\\ndata: <JSON>\\n\\n

    ================ ======================= =============================
    Channel           Source                  What the subscriber gets
    ================ ======================= =============================
    ``messages``      ``stream.messages``     Raw message items (dicts)
    ``tool_calls``    ``stream.tool_calls``   Tool lifecycle items
    ``subagents``     ``stream.subagents``    Subagent lifecycle items
    ``values``        ``stream.values``       State snapshots
    ``control``       Synthetic               ``interrupt`` / ``complete``
    ================ ======================= =============================

    Yields:
        Dicts with ``channel`` and ``data`` keys, ready for SSE serialization
        or direct consumption.
    """
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(
        maxsize=256
    )

    tasks = [
        asyncio.create_task(
            _relay_messages(stream, queue), name="relay-messages"
        ),
        asyncio.create_task(
            _relay_tool_calls(stream, queue), name="relay-tool-calls"
        ),
        asyncio.create_task(
            _relay_subagents(stream, queue), name="relay-subagents"
        ),
        asyncio.create_task(
            _relay_values(stream, queue), name="relay-values"
        ),
    ]

    num_producers = len(tasks)

    completed = 0
    while completed < num_producers:
        item = await queue.get()
        if item is None:
            completed += 1
            continue
        channel, data = item
        yield {"channel": channel, "data": data}

    await asyncio.gather(*tasks, return_exceptions=True)

    # Terminal control events.
    interrupted = False
    try:
        int_val = stream.interrupted()
        if asyncio.iscoroutine(int_val):
            int_val = await int_val
        interrupted = bool(int_val)
    except Exception:
        pass

    if interrupted:
        interrupts_list: list[dict[str, Any]] = []
        try:
            raw = stream.interrupts()
            if asyncio.iscoroutine(raw):
                raw = await raw
            for it in raw or ():
                value = it.value if hasattr(it, "value") else it
                interrupts_list.append(_safe_dict(value))
        except Exception:
            pass
        if interrupts_list:
            yield {"channel": "control", "data": {"event": "interrupt", "interrupts": interrupts_list}}

    yield {
        "channel": "control",
        "data": {"event": "complete", "interrupted": interrupted, "thread_id": thread_id},
    }


# ── Per-projection relays ────────────────────────────────────────────────────


async def _relay_messages(
    stream: Any, queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]
) -> None:
    """Relay raw ``stream.messages`` items to the ``messages`` SSE channel."""
    try:
        async for item in stream.messages:
            data = _message_item_to_dict(item)
            if data:
                await queue.put(("messages", data))
    except Exception:
        logger.debug("messages relay failed", exc_info=True)
    finally:
        await queue.put(_SENTINEL)


async def _relay_tool_calls(
    stream: Any, queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]
) -> None:
    """Relay raw ``stream.tool_calls`` items to the ``tool_calls`` SSE channel."""
    try:
        async for item in stream.tool_calls:
            data = _tool_call_item_to_dict(item)
            if data:
                await queue.put(("tool_calls", data))
    except Exception:
        logger.debug("tool_calls relay failed", exc_info=True)
    finally:
        await queue.put(_SENTINEL)


async def _relay_subagents(
    stream: Any, queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]
) -> None:
    """Relay raw ``stream.subagents`` items to the ``subagents`` SSE channel."""
    try:
        async for item in stream.subagents:
            data = _subagent_item_to_dict(item)
            if data:
                await queue.put(("subagents", data))
    except Exception:
        logger.debug("subagents relay failed", exc_info=True)
    finally:
        await queue.put(_SENTINEL)


async def _relay_values(
    stream: Any, queue: asyncio.Queue[tuple[str, dict[str, Any]] | None]
) -> None:
    """Relay raw ``stream.values`` snapshots to the ``values`` SSE channel."""
    try:
        async for item in stream.values:
            data = _safe_dict(item)
            if data:
                await queue.put(("values", data))
    except Exception:
        logger.debug("values relay failed", exc_info=True)
    finally:
        await queue.put(_SENTINEL)


# ── Serialization ────────────────────────────────────────────────────────────


def _sse_message(channel: str, data: dict[str, Any]) -> str:
    """Build a complete SSE message string.

    Format::

        event: <channel>\\n
        data: <JSON>\\n
        \\n
    """
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {channel}\ndata: {payload}\n\n"


# ── Item → dict converters (thin, no type taxonomy) ──────────────────────────


def _safe_dict(obj: Any) -> Any:
    """Best-effort conversion to JSON-friendly value."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _safe_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_dict(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return _safe_dict(obj.model_dump())
        except Exception:
            pass
    return str(obj)


def _message_item_to_dict(item: Any) -> dict[str, Any] | None:
    """Convert a v3 messages projection item to a dict.

    v3 yields ``ChatModelStream`` / ``AsyncChatModelStream`` objects
    (one per LLM call). Their ``.text`` is a projection — resolve it.
    """
    raw_text = getattr(item, "text", None)
    if raw_text is None:
        return None

    result: dict[str, Any] = {}

    # Resolve text — may be AsyncProjection or SyncTextProjection.
    if isinstance(raw_text, str):
        result["content"] = raw_text
    elif hasattr(raw_text, "__iter__") and not isinstance(raw_text, (str, bytes)):
        result["content"] = str(raw_text)
    else:
        result["content"] = str(raw_text)

    msg_id = getattr(item, "message_id", None)
    if msg_id is not None:
        result["id"] = str(msg_id)

    role = getattr(item, "role", None)
    result["role"] = str(role) if role is not None else "ai"

    tc_chunks = getattr(item, "tool_call_chunks", None) or []
    if tc_chunks:
        result["tool_call_chunks"] = _safe_dict(tc_chunks)

    return result


def _tool_call_item_to_dict(item: Any) -> dict[str, Any] | None:
    """Convert a v3 tool_calls projection item to a dict.

    v3 yields objects with ``.tool_name``, ``.input``, ``.output``,
    ``.error``, ``.output_deltas``.

    We slice the output deltas into the first and total to keep the
    item self-contained (no separate stream), then emit one item per
    state transition (start → output → end/error).
    """
    result: dict[str, Any] = {
        "tool_name": str(getattr(item, "tool_name", "")),
    }

    inp = getattr(item, "input", None)
    if inp is not None:
        result["input"] = _safe_dict(inp)

    error = getattr(item, "error", None)
    if error is not None:
        result["state"] = "error"
        result["error"] = str(error)
    else:
        output = getattr(item, "output", None)
        result["state"] = "completed" if output is not None else "running"
        if output is not None:
            result["output"] = _safe_dict(output)

    return result


def _subagent_item_to_dict(item: Any) -> dict[str, Any] | None:
    """Convert a v3 subagents projection item to a dict.

    v3 yields objects with ``.name``, ``.status``, ``.path``.
    """
    return {
        "name": str(getattr(item, "name", "")),
        "status": str(getattr(item, "status", "unknown")),
        "path": list(getattr(item, "path", []) or []),
    }
