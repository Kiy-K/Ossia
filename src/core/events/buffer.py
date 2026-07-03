"""In-memory thread event buffer for replay and late-joining TUI sessions.

Stores normalized ``OssiaEvent`` objects per thread so clients can replay a
thread's event stream via ``GET /v1/threads/{id}/events`` without needing
a full checkpoint-based re-run.

The buffer is a simple dict-backed store. Events are appended after each
stream completes (in ``chat_stream``). The buffer is bounded per-thread to
``MAX_EVENTS_PER_THREAD`` to prevent unbounded memory growth.

When ``store()`` is called from inside an event loop, the buffer
also schedules a webhook delivery task per event so subscribed
``/v1/webhooks`` get a copy. The task is fire-and-forget: the
buffer does not wait for delivery. Ponytail: this is the cheapest
way to wire the two without a background worker.
"""

from __future__ import annotations

import asyncio
import logging

from core.events.types import OssiaEvent

logger = logging.getLogger(__name__)

# Maximum number of events stored per thread to bound memory growth.
# At ~500 bytes per event this is ~5 MB per thread.
MAX_EVENTS_PER_THREAD = 10_000


class ThreadEventBuffer:
    """In-memory thread-scoped event buffer.

    ``store()`` appends events to the buffer for a given thread.
    ``get()`` returns a copy of the stored events list.
    ``clear()`` drops all events for a thread.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[OssiaEvent]] = {}

    def store(self, thread_id: str, events: list[OssiaEvent]) -> None:
        """Append *events* to the buffer for *thread_id*.

        If the thread already has buffered events, *events* are appended.
        The total count is trimmed to ``MAX_EVENTS_PER_THREAD``.

        When called from a running event loop, each event also
        triggers a webhook delivery task (fire-and-forget).
        """
        if not events:
            return
        existing = self._events.get(thread_id, [])
        existing.extend(events)
        if len(existing) > MAX_EVENTS_PER_THREAD:
            logger.debug(
                "Trimming event buffer for thread %s: %d -> %d",
                thread_id,
                len(existing),
                MAX_EVENTS_PER_THREAD,
            )
            existing = existing[-MAX_EVENTS_PER_THREAD:]
        self._events[thread_id] = existing
        self._dispatch_webhooks(events)

    def _dispatch_webhooks(self, events: list[OssiaEvent]) -> None:
        """Schedule a webhook delivery task per event when an event loop is running.

        No-op outside a running loop (sync callers like the
        ``/v1/threads/{id}/events`` replay path don't trigger
        webhooks — those events are not new). Ponytail: import
        inside the method to avoid a hard dependency on the
        webhooks module at import time.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        from core.webhooks import deliver_event

        for ev in events:
            loop.create_task(deliver_event(ev))

    def get(self, thread_id: str) -> list[OssiaEvent]:
        """Return a copy of stored events for *thread_id*, or []."""
        return list(self._events.get(thread_id, []))

    def clear(self, thread_id: str) -> None:
        """Remove all stored events for a thread."""
        self._events.pop(thread_id, None)

    def clear_all(self) -> None:
        """Remove all stored events across all threads."""
        self._events.clear()

    def thread_ids(self) -> list[str]:
        """Return list of thread ids with buffered events."""
        return list(self._events.keys())


# Module-level singleton — imported by api.py and tests.
_EVENT_BUFFER = ThreadEventBuffer()


def get_thread_event_buffer() -> ThreadEventBuffer:
    """Return the global thread event buffer singleton."""
    return _EVENT_BUFFER
