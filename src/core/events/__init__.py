"""Event buffer for replay and late-joining sessions.

The v3 projector in ``core.v3_projector`` has replaced the custom
``EventNormalizer`` + ``OssiaEvent`` pipeline (deleted). Only the
thread-level event buffer remains — it stores raw channel-keyed
dicts from the projector for replay via ``GET /v1/threads/{id}/events``.
"""

from core.events.buffer import ThreadEventBuffer, get_thread_event_buffer

__all__ = [
    "ThreadEventBuffer",
    "get_thread_event_buffer",
]
