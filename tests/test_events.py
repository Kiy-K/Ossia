"""Tests for the normalized event protocol.

Covers:
- Event type helpers and OssiaEvent envelope
- EventNormalizer with mocked v3 stream
- SSE serialization
- Legacy StreamEvent conversion
- State reducers (initial_state, reduce_event, apply_events)
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from core.events import (
    EventNormalizer,
    OssiaEvent,
    apply_events,
    artifact_data,
    complete_data,
    initial_state,
    message_completed_data,
    message_delta_data,
    message_started_data,
    reduce_event,
    serialize_json,
    serialize_sse,
    subagent_completed_data,
    subagent_failed_data,
    subagent_spawned_data,
    to_legacy_stream_event,
    to_ossia_kind,
    tool_completed_data,
    tool_failed_data,
    tool_progress_data,
    tool_started_data,
)
from core.schemas import StreamEvent

# ── OssiaEvent envelope tests ────────────────────────────────────────────────


def test_ossia_event_has_required_fields() -> None:
    """OssiaEvent requires seq, type, and accepts optional fields."""
    event = OssiaEvent(seq=1, type="message_delta", data={"text": "hello"})
    assert event.seq == 1
    assert event.type == "message_delta"
    assert event.data == {"text": "hello"}
    assert event.id  # auto-generated UUID hex
    assert event.timestamp  # auto-generated ISO timestamp
    assert event.source == "coordinator"  # default
    assert event.thread_id == "default"  # default


def test_ossia_event_accepts_all_fields() -> None:
    """OssiaEvent accepts all fields explicitly."""
    event = OssiaEvent(
        id="custom-id",
        seq=5,
        timestamp="2026-01-01T00:00:00",
        type="tool_started",
        source="coordinator.researcher",
        thread_id="my-thread",
        data={"name": "search_codebase", "input": {}},
    )
    assert event.id == "custom-id"
    assert event.seq == 5
    assert event.type == "tool_started"
    assert event.source == "coordinator.researcher"
    assert event.thread_id == "my-thread"


def test_ossia_event_rejects_extra_fields() -> None:
    """OssiaEvent uses extra='forbid'."""
    with pytest.raises(ValidationError):
        OssiaEvent(seq=1, type="test", unknown_field="bad")  # type: ignore[call-arg]


# ── Event type data helpers ──────────────────────────────────────────────────


def test_message_data_helpers() -> None:
    """Message data helpers produce correctly shaped dicts."""
    d1 = message_started_data("ai", "Hello", message_id="msg-1")
    assert d1 == {"role": "ai", "text": "Hello", "id": "msg-1"}

    d2 = message_delta_data("ai", " World")
    assert d2 == {"role": "ai", "text": " World", "id": None}

    d3 = message_completed_data("ai", "Hello World")
    assert d3["role"] == "ai"
    assert d3["text"] == "Hello World"


def test_subagent_data_helpers() -> None:
    """Subagent data helpers produce correctly shaped dicts."""
    d1 = subagent_spawned_data("researcher", ["ossia", "agent", "researcher"])
    assert d1["name"] == "researcher"
    assert d1["path"] == ["ossia", "agent", "researcher"]

    d2 = subagent_completed_data("researcher", result="Done")
    assert d2["result"] == "Done"

    d3 = subagent_failed_data("researcher", error="Timeout")
    assert d3["error"] == "Timeout"


def test_tool_data_helpers() -> None:
    """Tool data helpers produce correctly shaped dicts."""
    d1 = tool_started_data("search_codebase", {"query": "test"})
    assert d1["name"] == "search_codebase"
    assert d1["input"] == {"query": "test"}

    d2 = tool_progress_data("search_codebase", output_delta="partial")
    assert d2["output_delta"] == "partial"

    d3 = tool_completed_data("search_codebase", output="result")
    assert d3["output"] == "result"

    d4 = tool_failed_data("search_codebase", error="crashed", source="coordinator.researcher")
    assert d4["error"] == "crashed"
    assert d4["source"] == "coordinator.researcher"


def test_artifact_data_helper() -> None:
    """Artifact data helper produces correctly shaped dict."""
    d = artifact_data(
        artifact_id="art-0",
        art_type="image",
        filename="screenshot.png",
        event="artifact_received",
        analysis_state="pending",
    )
    assert d["artifact_id"] == "art-0"
    assert d["type"] == "image"
    assert d["filename"] == "screenshot.png"
    assert d["event"] == "artifact_received"


def test_complete_data_helper() -> None:
    """Complete data helper produces correctly shaped dict."""
    d1 = complete_data({"thread_id": "t1"}, interrupted=False)
    assert d1["output"] == {"thread_id": "t1"}
    assert d1["interrupted"] is False

    d2 = complete_data({}, interrupted=True)
    assert d2["interrupted"] is True


# ── SSE serialization tests ──────────────────────────────────────────────────


def test_serialize_sse_format() -> None:
    """serialize_sse produces valid SSE format."""
    event = OssiaEvent(seq=1, type="message_delta", data={"text": "Hello"})
    sse = serialize_sse(event)
    assert sse.startswith("event: message_delta")
    assert "id: 1" in sse
    assert "data: " in sse
    assert sse.endswith("\n\n") or sse.endswith("\n\n\n")


def test_serialize_sse_without_seq() -> None:
    """serialize_sse omits id: when include_seq=False."""
    event = OssiaEvent(seq=1, type="message_delta")
    sse = serialize_sse(event, include_seq=False)
    assert "id:" not in sse


def test_serialize_sse_valid_json() -> None:
    """The data line in SSE output is valid JSON."""
    import json

    event = OssiaEvent(seq=1, type="tool_started", data={"name": "search"})
    sse = serialize_sse(event)
    # Extract the data line
    for line in sse.split("\n"):
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            assert payload["type"] == "tool_started"
            assert payload["data"]["name"] == "search"


def test_serialize_json() -> None:
    """serialize_json produces valid JSON string."""
    event = OssiaEvent(seq=1, type="complete", data={"interrupted": False})
    js = serialize_json(event)
    import json

    parsed = json.loads(js)
    assert parsed["type"] == "complete"
    assert parsed["data"]["interrupted"] is False


def test_to_ossia_kind_mapping() -> None:
    """to_ossia_kind maps event types to the correct StreamEvent kind."""
    assert to_ossia_kind("message_delta") == "message"
    assert to_ossia_kind("tool_started") == "tool_call"
    assert to_ossia_kind("subagent_spawned") == "subagent"
    assert to_ossia_kind("async_task_completed") == "async_task"
    assert to_ossia_kind("pipeline_started") == "pipeline"
    assert to_ossia_kind("artifact_received") == "artifact"
    assert to_ossia_kind("interrupt") == "interrupt"
    assert to_ossia_kind("complete") == "complete"
    assert to_ossia_kind("error") == "protocol"
    assert to_ossia_kind("unknown_event") == "protocol"


def test_to_legacy_stream_event() -> None:
    """to_legacy_stream_event converts to existing StreamEvent schema."""
    event = OssiaEvent(seq=42, type="message_delta", data={"text": "hi"})
    legacy = to_legacy_stream_event(event)
    assert isinstance(legacy, StreamEvent)
    assert legacy.kind == "message"
    assert legacy.seq == 42
    assert legacy.data == {"text": "hi"}


# ── State reducer tests ──────────────────────────────────────────────────────


def test_initial_state() -> None:
    """initial_state returns an empty, ready-to-render state tree."""
    state = initial_state("test-thread")
    assert state["thread_id"] == "test-thread"
    assert state["state"] == "running"
    assert state["coordinator"]["messages"] == []
    assert state["coordinator"]["tools"] == []
    assert state["subagents"] == {}
    assert state["interrupted"] is False
    assert state["error"] is None


def test_reduce_message_delta_appends_text() -> None:
    """message_delta events accumulate text into pending_message."""
    state = initial_state()
    event = OssiaEvent(seq=1, type="message_delta", data={"text": "Hello ", "role": "ai"})
    state = reduce_event(state, event)
    assert state["coordinator"]["pending_message"] == "Hello "

    event2 = OssiaEvent(seq=2, type="message_delta", data={"text": "World", "role": "ai"})
    state = reduce_event(state, event2)
    assert state["coordinator"]["pending_message"] == "Hello World"


def test_reduce_message_completed_moves_to_messages() -> None:
    """message_completed finalizes the pending message into the messages list."""
    state = initial_state()
    state["coordinator"]["pending_message"] = "Hello World"

    event = OssiaEvent(seq=2, type="message_completed", data={"role": "ai", "text": "Hello World"})
    state = reduce_event(state, event)
    assert state["coordinator"]["pending_message"] is None
    assert len(state["coordinator"]["messages"]) == 1
    assert state["coordinator"]["messages"][0]["role"] == "ai"
    assert state["coordinator"]["messages"][0]["content"] == "Hello World"


def test_reduce_subagent_spawned_creates_node() -> None:
    """subagent_spawned creates a node in the subagent tree."""
    state = initial_state()
    event = OssiaEvent(
        seq=1,
        type="subagent_spawned",
        data=subagent_spawned_data("researcher", ["researcher"]),
        source="coordinator.researcher",
    )
    state = reduce_event(state, event)
    assert "researcher" in state["subagents"]
    assert state["subagents"]["researcher"]["state"] == "running"
    assert state["subagents"]["researcher"]["name"] == "researcher"


def test_reduce_tool_started_and_completed() -> None:
    """tool_started and tool_completed track tool lifecycle."""
    state = initial_state()
    event = OssiaEvent(
        seq=1,
        type="tool_started",
        data=tool_started_data("search_codebase", {"query": "test"}),
    )
    state = reduce_event(state, event)
    assert len(state["coordinator"]["tools"]) == 1
    assert state["coordinator"]["tools"][0]["name"] == "search_codebase"
    assert state["coordinator"]["tools"][0]["state"] == "running"

    event2 = OssiaEvent(
        seq=2,
        type="tool_completed",
        data=tool_completed_data("search_codebase", output="results"),
    )
    state = reduce_event(state, event2)
    assert state["coordinator"]["tools"][0]["state"] == "completed"


def test_reduce_interrupt_and_complete() -> None:
    """interrupt and complete events set the run state correctly."""
    state = initial_state()

    # Interrupt
    event = OssiaEvent(seq=1, type="interrupt", data={"interrupts": [{"type": "approve"}]})
    state = reduce_event(state, event)
    assert state["interrupted"] is True
    assert state["state"] == "interrupted"

    # Complete (interrupted)
    event2 = OssiaEvent(seq=2, type="complete", data={"interrupted": True, "output": {}})
    state = reduce_event(state, event2)
    assert state["state"] == "interrupted"

    # Complete (not interrupted)
    state2 = initial_state()
    event3 = OssiaEvent(
        seq=1, type="complete", data={"interrupted": False, "output": {"key": "val"}}
    )
    state2 = reduce_event(state2, event3)
    assert state2["state"] == "completed"
    assert state2["interrupted"] is False


def test_apply_events_batch() -> None:
    """apply_events processes multiple events in order."""
    state = initial_state()
    events = [
        OssiaEvent(seq=1, type="message_delta", data={"text": "Hello ", "role": "ai"}),
        OssiaEvent(seq=2, type="message_delta", data={"text": "World", "role": "ai"}),
        OssiaEvent(seq=3, type="message_completed", data={"role": "ai", "text": "Hello World"}),
        OssiaEvent(seq=4, type="complete", data={"interrupted": False, "output": {}}),
    ]
    final = apply_events(state, events)
    assert len(final["coordinator"]["messages"]) == 1
    assert final["coordinator"]["messages"][0]["content"] == "Hello World"
    assert final["state"] == "completed"


# ── EventNormalizer tests ────────────────────────────────────────────────────


# ── Mock projection classes for _resolve_text tests ────────────────────────
# These mimic AsyncProjection and SyncTextProjection from
# langchain_core.language_models.chat_model_stream, which the v3
# stream's .messages projection uses instead of plain strings.


class _FakeAsyncProjection:
    """Mocks AsyncProjection: an awaitable that resolves to the full text.

    The real ``AsyncProjection`` is both async-iterable (for per-token
    deltas) and awaitable (for the final accumulated text). This mock
    implements only ``__await__`` so ``_resolve_text`` can await it.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def __await__(self):
        async def _resolve() -> str:
            return self._text

        return _resolve().__await__()


class _FakeSyncProjection:
    """Mocks SyncTextProjection: a sync iterable that supports ``str()``.

    The real ``SyncTextProjection`` is iterable (for per-token deltas)
    and ``str()`` returns the full accumulated text. This mock implements
    ``__str__`` and ``__iter__`` so ``_resolve_text`` can resolve it.
    """

    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text

    def __iter__(self):
        yield self._text


class _FakeAsyncChatModelStream:
    """Mocks an AsyncChatModelStream from the v3 stream.

    The real v3 ``stream.messages`` projection yields
    ``AsyncChatModelStream`` objects per LLM call. Their ``.text`` is
    an ``AsyncProjection`` (awaitable), not a plain string. They carry
    ``.message_id`` instead of ``.id``, and ``.role`` is always ``"ai"``.
    """

    def __init__(self, text: str, message_id: str | None = None) -> None:
        self.text = _FakeAsyncProjection(text)
        self.message_id = message_id if message_id else f"msg-{hash(text)}"


class _FakeSyncChatModelStream:
    """Mocks a ChatModelStream (sync variant) from the v3 stream.

    Same structure as ``_FakeAsyncChatModelStream`` but ``.text`` is a
    ``SyncTextProjection``, which ``str()`` resolves to the full text.
    """

    def __init__(self, text: str, message_id: str | None = None) -> None:
        self.text = _FakeSyncProjection(text)
        self.message_id = message_id if message_id else f"msg-{hash(text)}"


class _FakeV3Stream:
    """Minimal fake v3 stream for unit-testing the normalizer.

    Provides .messages, .tool_calls, .subagents, .values async generators
    plus .output and .interrupted properties.
    """

    def __init__(self) -> None:
        self._messages: list[Any] = []
        self._tool_calls: list[Any] = []
        self._subagents: list[Any] = []
        self._values: list[Any] = []
        self._output: Any = None
        self._interrupted: bool = False
        self._interrupts: list[Any] = []

    @property
    def messages(self) -> Any:
        class _AG:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._msgs:
                    raise StopAsyncIteration
                return self._msgs.pop(0)

        ag = _AG()
        ag._msgs = list(self._messages)  # type: ignore[attr-defined]
        return ag

    @property
    def tool_calls(self) -> Any:
        class _AG:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._tcs:
                    raise StopAsyncIteration
                return self._tcs.pop(0)

        ag = _AG()
        ag._tcs = list(self._tool_calls)  # type: ignore[attr-defined]
        return ag

    @property
    def subagents(self) -> Any:
        class _AG:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._sas:
                    raise StopAsyncIteration
                return self._sas.pop(0)

        ag = _AG()
        ag._sas = list(self._subagents)  # type: ignore[attr-defined]
        return ag

    @property
    def values(self) -> Any:
        class _AG:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._vals:
                    raise StopAsyncIteration
                return self._vals.pop(0)

        ag = _AG()
        ag._vals = list(self._values)  # type: ignore[attr-defined]
        return ag

    @property
    def output(self) -> Any:
        return self._output

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    @property
    def interrupts(self) -> list[Any]:
        return self._interrupts


class _FakeMsg:
    """Fake message projection item."""

    def __init__(self, text: str, role: str = "ai", id_: str | None = None):
        self.text = text
        self.role = role
        self.id = id_


class _FakeToolCall:
    """Fake tool call projection item."""

    def __init__(
        self,
        tool_name: str,
        input_: dict | None = None,
        output: Any = None,
        error: str | None = None,
        output_deltas: list[str] | None = None,
    ):
        self.tool_name = tool_name
        self.input = input_ or {}
        self.output = output
        self.error = error
        self._deltas = output_deltas or []

    @property
    def output_deltas(self) -> Any:
        class _AG:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._ds:
                    raise StopAsyncIteration
                return self._ds.pop(0)

        ag = _AG()
        ag._ds = list(self._deltas)  # type: ignore[attr-defined]
        return ag


class _FakeSubagent:
    """Fake subagent projection item."""

    def __init__(self, name: str, status: str, path: list[str] | None = None):
        self.name = name
        self.status = status
        self.path = path or []


class _FakeValue(dict):
    """Fake value projection item.

    Subclasses ``dict`` so ``_safe()`` in the normalizer can extract
    ``async_tasks`` via dict-style access (``values.get("async_tasks")``).
    """

    def __init__(self, async_tasks: list[dict] | None = None):
        super().__init__()
        self["async_tasks"] = async_tasks or []


@pytest.mark.asyncio
async def test_normalizer_empty_stream() -> None:
    """Normalizer handles an empty stream gracefully (completes immediately)."""
    stream = _FakeV3Stream()
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # Should produce exactly one complete event
    assert len(events) == 1
    assert events[0].type == "complete"
    assert events[0].data["interrupted"] is False


@pytest.mark.asyncio
async def test_normalizer_message_flow() -> None:
    """Normalizer converts message stream items to OssiaEvents."""
    stream = _FakeV3Stream()
    stream._messages = [
        _FakeMsg("Hello", role="ai", id_="msg-1"),
        _FakeMsg(" World", role="ai", id_="msg-1"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 1 message_started + 1 message_delta + 1 message_completed + 1 complete = 4
    assert len(events) == 4
    assert events[0].type == "message_started"
    assert events[0].data["text"] == "Hello"
    assert events[1].type == "message_delta"
    assert events[1].data["text"] == " World"
    assert events[2].type == "message_completed"
    assert events[2].data["text"] == "Hello World"
    assert events[3].type == "complete"


@pytest.mark.asyncio
async def test_normalizer_tool_call_flow() -> None:
    """Normalizer converts tool call stream items to OssiaEvents."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(
            tool_name="search_codebase",
            input_={"query": "test"},
            output="found 3 results",
            output_deltas=["found ", "3 ", "results"],
        ),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 1 tool_started + 3 tool_progress + 1 tool_completed + 1 complete = 6
    assert len(events) == 6
    assert events[0].type == "tool_started"
    assert events[0].data["name"] == "search_codebase"
    assert events[1].type == "tool_progress"
    assert events[2].type == "tool_progress"
    assert events[3].type == "tool_progress"
    assert events[4].type == "tool_completed"
    assert events[5].type == "complete"


@pytest.mark.asyncio
async def test_normalizer_tool_failure() -> None:
    """Normalizer emits tool_failed when a tool errors."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(tool_name="search_codebase", error="Rate limit exceeded"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    assert len(events) == 3
    assert events[0].type == "tool_started"
    assert events[1].type == "tool_failed"
    assert events[1].data["error"] == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_normalizer_subagent_flow() -> None:
    """Normalizer converts subagent lifecycle to OssiaEvents."""
    stream = _FakeV3Stream()
    stream._subagents = [
        _FakeSubagent("researcher", "started", ["ossia", "agent", "researcher"]),
        _FakeSubagent("researcher", "completed", ["ossia", "agent", "researcher"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 2 subagent events + 1 complete = 3
    assert len(events) == 3
    assert events[0].type == "subagent_spawned"
    assert events[0].data["name"] == "researcher"
    assert events[1].type == "subagent_completed"
    assert events[2].type == "complete"


# ── Nested subagent / source path tests ─────────────────────────────────────


def test_source_from_path_coordinator() -> None:
    """An empty/root-level path resolves to 'coordinator'."""
    normalizer = EventNormalizer()
    assert normalizer._source_from_path([]) == "coordinator"
    assert normalizer._source_from_path(["ossia"]) == "coordinator"
    assert normalizer._source_from_path(["ossia", "agent"]) == "coordinator"


def test_source_from_path_one_level() -> None:
    """A single subagent path resolves to 'coordinator.<name>'."""
    normalizer = EventNormalizer()
    assert normalizer._source_from_path(["ossia", "researcher"]) == "coordinator.researcher"
    assert (
        normalizer._source_from_path(["ossia", "code-researcher"]) == "coordinator.code-researcher"
    )


def test_source_from_path_two_levels() -> None:
    """A nested subagent path resolves to 'coordinator.<parent>.<child>'."""
    normalizer = EventNormalizer()
    assert normalizer._source_from_path(["ossia", "researcher", "security-reviewer"]) == (
        "coordinator.researcher.security-reviewer"
    )


def test_source_from_path_three_levels() -> None:
    """A deeply nested subagent path accumulates all levels."""
    normalizer = EventNormalizer()
    assert normalizer._source_from_path(["ossia", "researcher", "sec-review", "deep-agent"]) == (
        "coordinator.researcher.sec-review.deep-agent"
    )


def test_source_for_active_subagent_no_subagents() -> None:
    """When no subagents are active, source_for_active_subagent returns 'coordinator'."""
    normalizer = EventNormalizer()
    assert normalizer._source_for_active_subagent() == "coordinator"


def test_source_for_active_subagent_single() -> None:
    """When a single subagent is active, source_for_active_subagent returns its path."""
    normalizer = EventNormalizer()
    normalizer._active_subagents["coordinator.researcher"] = "researcher"
    assert normalizer._source_for_active_subagent() == "coordinator.researcher"


def test_source_for_active_subagent_nested() -> None:
    """When multiple subagents are active, returns the deepest one."""
    normalizer = EventNormalizer()
    normalizer._active_subagents["coordinator.researcher"] = "researcher"
    normalizer._active_subagents["coordinator.researcher.security-reviewer"] = "security-reviewer"
    # Should return the deepest (lexicographically last sorted key)
    assert normalizer._source_for_active_subagent() == "coordinator.researcher.security-reviewer"


def test_source_for_active_subagent_cleared_on_complete() -> None:
    """When a subagent completes, it's removed from active subagents."""
    normalizer = EventNormalizer()
    normalizer._active_subagents["coordinator.researcher"] = "researcher"
    assert normalizer._source_for_active_subagent() == "coordinator.researcher"
    normalizer._active_subagents.pop("coordinator.researcher", None)
    assert normalizer._source_for_active_subagent() == "coordinator"


@pytest.mark.asyncio
async def test_normalizer_nested_subagents() -> None:
    """Normalizer emits correct source paths for nested subagents.

    Verifies the hierarchy: coordinator → researcher → security-reviewer.
    Each subagent event must carry the correct dot-separated source path
    matching its depth in the tree.
    """
    stream = _FakeV3Stream()
    stream._subagents = [
        _FakeSubagent("researcher", "started", ["ossia", "researcher"]),
        _FakeSubagent("security-reviewer", "started", ["ossia", "researcher", "security-reviewer"]),
        _FakeSubagent(
            "security-reviewer", "completed", ["ossia", "researcher", "security-reviewer"]
        ),
        _FakeSubagent("researcher", "completed", ["ossia", "researcher"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # Filter subagent events
    subagent_events = [e for e in events if e.type.startswith("subagent_")]
    assert len(subagent_events) == 4

    # Researcher spawned: source should be coordinator.researcher
    assert subagent_events[0].type == "subagent_spawned"
    assert subagent_events[0].data["name"] == "researcher"
    assert subagent_events[0].source == "coordinator.researcher"

    # Security-reviewer spawned under researcher: source should be coordinator.researcher.security-reviewer
    assert subagent_events[1].type == "subagent_spawned"
    assert subagent_events[1].data["name"] == "security-reviewer"
    assert subagent_events[1].source == "coordinator.researcher.security-reviewer"

    # Security-reviewer completed
    assert subagent_events[2].type == "subagent_completed"
    assert subagent_events[2].data["name"] == "security-reviewer"
    assert subagent_events[2].source == "coordinator.researcher.security-reviewer"

    # Researcher completed
    assert subagent_events[3].type == "subagent_completed"
    assert subagent_events[3].data["name"] == "researcher"
    assert subagent_events[3].source == "coordinator.researcher"


@pytest.mark.asyncio
async def test_normalizer_nested_subagents_three_levels() -> None:
    """Normalizer handles three levels of nesting:
    coordinator → researcher → sec-review → deep-agent."""
    stream = _FakeV3Stream()
    stream._subagents = [
        _FakeSubagent("researcher", "started", ["ossia", "researcher"]),
        _FakeSubagent("sec-review", "started", ["ossia", "researcher", "sec-review"]),
        _FakeSubagent("deep-agent", "started", ["ossia", "researcher", "sec-review", "deep-agent"]),
        _FakeSubagent(
            "deep-agent", "completed", ["ossia", "researcher", "sec-review", "deep-agent"]
        ),
        _FakeSubagent("sec-review", "completed", ["ossia", "researcher", "sec-review"]),
        _FakeSubagent("researcher", "completed", ["ossia", "researcher"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    subagent_events = [e for e in events if e.type.startswith("subagent_")]
    assert len(subagent_events) == 6

    # Verify sources at each depth
    assert subagent_events[0].source == "coordinator.researcher"
    assert subagent_events[1].source == "coordinator.researcher.sec-review"
    assert subagent_events[2].source == "coordinator.researcher.sec-review.deep-agent"
    assert subagent_events[3].source == "coordinator.researcher.sec-review.deep-agent"
    assert subagent_events[4].source == "coordinator.researcher.sec-review"
    assert subagent_events[5].source == "coordinator.researcher"


@pytest.mark.asyncio
async def test_normalizer_nested_subagent_interrupted() -> None:
    """Normalizer emits subagent_interrupted with correct source for nested agents."""
    stream = _FakeV3Stream()
    stream._subagents = [
        _FakeSubagent("researcher", "started", ["ossia", "researcher"]),
        _FakeSubagent("auditor", "started", ["ossia", "researcher", "auditor"]),
        _FakeSubagent("auditor", "interrupted", ["ossia", "researcher", "auditor"]),
    ]
    stream._output = {}
    stream._interrupted = True

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    subagent_events = [e for e in events if e.type.startswith("subagent_")]

    # Find the interrupted event
    interrupted = [e for e in subagent_events if e.type == "subagent_interrupted"]
    assert len(interrupted) == 1
    assert interrupted[0].data["name"] == "auditor"
    assert interrupted[0].source == "coordinator.researcher.auditor"


@pytest.mark.asyncio
async def test_normalizer_subagent_message_delta() -> None:
    """Normalizer emits subagent_message_delta for unknown subagent statuses.

    When a subagent reports a status other than started/completed/error/interrupted
    (e.g. 'streaming', 'thinking', 'working'), the normalizer maps it to
    subagent_message_delta.
    """
    stream = _FakeV3Stream()
    stream._subagents = [
        _FakeSubagent("researcher", "started", ["ossia", "researcher"]),
        _FakeSubagent("researcher", "streaming", ["ossia", "researcher"]),
        _FakeSubagent("researcher", "completed", ["ossia", "researcher"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # Find the message_delta event
    delta_events = [e for e in events if e.type == "subagent_message_delta"]
    assert len(delta_events) == 1, (
        f"expected 1 subagent_message_delta, got {len(delta_events)}: "
        f"{[e.type for e in events if e.type.startswith('subagent_')]}"
    )
    assert delta_events[0].data["name"] == "researcher"
    assert delta_events[0].data["text"] == "streaming"
    assert delta_events[0].source == "coordinator.researcher"


@pytest.mark.asyncio
async def test_normalizer_subagent_message_delta_nested() -> None:
    """subagent_message_delta works for nested subagents with unknown status."""
    stream = _FakeV3Stream()
    stream._subagents = [
        _FakeSubagent("researcher", "started", ["ossia", "researcher"]),
        _FakeSubagent("security-reviewer", "started", ["ossia", "researcher", "security-reviewer"]),
        _FakeSubagent(
            "security-reviewer", "analyzing", ["ossia", "researcher", "security-reviewer"]
        ),
        _FakeSubagent(
            "security-reviewer", "completed", ["ossia", "researcher", "security-reviewer"]
        ),
        _FakeSubagent("researcher", "completed", ["ossia", "researcher"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    delta_events = [e for e in events if e.type == "subagent_message_delta"]
    assert len(delta_events) == 1
    assert delta_events[0].data["name"] == "security-reviewer"
    assert delta_events[0].data["text"] == "analyzing"
    assert delta_events[0].source == "coordinator.researcher.security-reviewer"


@pytest.mark.asyncio
async def test_normalizer_tool_call_source_nested_subagent() -> None:
    """Tool calls made while a nested subagent is active carry the subagent's source.

    Uses pre-seeded _active_subagents to avoid flakiness from concurrent
    task ordering between the tool call and subagent relays.
    """
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(tool_name="search_codebase", input_={"query": "test"}, output="results"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    # Pre-seed active subagents to simulate that researcher and
    # security-reviewer have already been spawned. This avoids a
    # race condition where the tool call relay reads _active_subagents
    # before the subagent relay has populated it.
    normalizer._active_subagents["coordinator.researcher"] = "researcher"
    normalizer._active_subagents["coordinator.researcher.security-reviewer"] = "security-reviewer"
    events = [e async for e in normalizer.normalize(stream)]

    tool_events = [e for e in events if e.type.startswith("tool_")]
    assert len(tool_events) >= 2  # started + completed

    # The tool's source should be the deepest active subagent (security-reviewer)
    assert tool_events[0].type == "tool_started"
    assert tool_events[0].source == "coordinator.researcher.security-reviewer", (
        f"expected nested subagent source, got '{tool_events[0].source}'"
    )


@pytest.mark.asyncio
async def test_normalizer_tool_call_source_returns_to_coordinator() -> None:
    """After all subagents complete, subsequent tool calls return to 'coordinator' source."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(tool_name="send_response", input_={"response": "final"}, output="sent"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    # Empty _active_subagents = no subagents active → source is coordinator
    events = [e async for e in normalizer.normalize(stream)]

    tool_events = [e for e in events if e.type.startswith("tool_")]
    assert len(tool_events) >= 2

    # Source should be coordinator since no subagents are active
    assert tool_events[0].source == "coordinator", (
        f"expected coordinator source, got '{tool_events[0].source}'"
    )


@pytest.mark.asyncio
async def test_normalizer_pipeline_bugfix_lifecycle() -> None:
    """Normalizer emits pipeline_started / step events / pipeline_completed for bugfix."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(
            tool_name="run_bugfix_pipeline",
            input_={"issue_description": "login fails"},
            output={"status": "ready", "pipeline": "bugfix", "js_code": "..."},
        ),
    ]
    stream._subagents = [
        _FakeSubagent("bug-diagnostician", "started", ["ossia", "agent", "bug-diagnostician"]),
        _FakeSubagent("bug-diagnostician", "completed", ["ossia", "agent", "bug-diagnostician"]),
        _FakeSubagent("fix-proposer", "started", ["ossia", "agent", "fix-proposer"]),
        _FakeSubagent("fix-proposer", "completed", ["ossia", "agent", "fix-proposer"]),
        _FakeSubagent("test-runner", "started", ["ossia", "agent", "test-runner"]),
        _FakeSubagent("test-runner", "completed", ["ossia", "agent", "test-runner"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # Collect the pipeline events (filter out surrounding tool/subagent/complete events)
    pipeline_events = [e for e in events if e.type.startswith("pipeline_")]
    # 1 start + 3 steps × (step_started + step_completed) + 1 complete = 8
    assert len(pipeline_events) == 8, f"expected 8 pipeline events, got {len(pipeline_events)}"

    # pipeline_started
    assert pipeline_events[0].type == "pipeline_started"
    assert pipeline_events[0].data["pipeline_type"] == "bugfix"
    assert pipeline_events[0].data["total_steps"] == 3
    pipeline_id = pipeline_events[0].data["pipeline_id"]
    assert pipeline_id.startswith("bugfix-")

    # Step 0: bug-diagnostician
    assert pipeline_events[1].type == "pipeline_step_started"
    assert pipeline_events[1].data["step_name"] == "bug-diagnostician"
    assert pipeline_events[1].data["step_index"] == 0
    assert pipeline_events[2].type == "pipeline_step_completed"
    assert pipeline_events[2].data["step_name"] == "bug-diagnostician"
    assert pipeline_events[2].data["step_index"] == 0

    # Step 1: fix-proposer
    assert pipeline_events[3].type == "pipeline_step_started"
    assert pipeline_events[3].data["step_name"] == "fix-proposer"
    assert pipeline_events[3].data["step_index"] == 1
    assert pipeline_events[4].type == "pipeline_step_completed"
    assert pipeline_events[4].data["step_name"] == "fix-proposer"
    assert pipeline_events[4].data["step_index"] == 1

    # Step 2: test-runner (last step → step_completed + pipeline_completed)
    assert pipeline_events[5].type == "pipeline_step_started"
    assert pipeline_events[5].data["step_name"] == "test-runner"
    assert pipeline_events[5].data["step_index"] == 2
    assert pipeline_events[6].type == "pipeline_step_completed"
    assert pipeline_events[6].data["step_name"] == "test-runner"
    assert pipeline_events[6].data["step_index"] == 2
    assert pipeline_events[7].type == "pipeline_completed"
    assert pipeline_events[7].data["pipeline_id"] == pipeline_id


def test_normalizer_pipeline_audit_has_correct_steps() -> None:
    """Audit pipeline has expected steps: code-researcher → bug-diagnostician."""
    from core.events.normalizer import _PIPELINE_STEPS

    assert _PIPELINE_STEPS["audit"] == ["code-researcher", "bug-diagnostician"]


def test_normalizer_pipeline_refactor_has_correct_steps() -> None:
    """Refactor pipeline has expected steps: code-researcher → fix-proposer ×2 → test-runner."""
    from core.events.normalizer import _PIPELINE_STEPS

    assert _PIPELINE_STEPS["refactor"] == [
        "code-researcher",
        "fix-proposer",
        "fix-proposer",
        "test-runner",
    ]


def test_normalizer_pipeline_tools_are_recognized() -> None:
    """All three pipeline orchestrator tools are in _PIPELINE_TOOLS."""
    from core.events.normalizer import _PIPELINE_TOOLS

    assert "run_bugfix_pipeline" in _PIPELINE_TOOLS
    assert "run_audit_pipeline" in _PIPELINE_TOOLS
    assert "run_refactor_pipeline" in _PIPELINE_TOOLS


@pytest.mark.asyncio
async def test_normalizer_pipeline_audit_lifecycle() -> None:
    """Normalizer emits pipeline events for audit pipeline (2 steps)."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(
            tool_name="run_audit_pipeline",
            input_={"target": "src/core", "focus": "security"},
            output={"status": "ready", "pipeline": "audit", "js_code": "..."},
        ),
    ]
    stream._subagents = [
        _FakeSubagent("code-researcher", "started", ["ossia", "agent", "code-researcher"]),
        _FakeSubagent("code-researcher", "completed", ["ossia", "agent", "code-researcher"]),
        _FakeSubagent("bug-diagnostician", "started", ["ossia", "agent", "bug-diagnostician"]),
        _FakeSubagent("bug-diagnostician", "completed", ["ossia", "agent", "bug-diagnostician"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    pipeline_events = [e for e in events if e.type.startswith("pipeline_")]
    # pipeline_started + (step_started + step_completed) × 2 + pipeline_completed = 6
    assert len(pipeline_events) == 6, f"expected 6 pipeline events, got {len(pipeline_events)}"

    assert pipeline_events[0].type == "pipeline_started"
    assert pipeline_events[0].data["pipeline_type"] == "audit"
    assert pipeline_events[0].data["total_steps"] == 2

    assert pipeline_events[1].type == "pipeline_step_started"
    assert pipeline_events[1].data["step_name"] == "code-researcher"
    assert pipeline_events[2].type == "pipeline_step_completed"
    assert pipeline_events[2].data["step_name"] == "code-researcher"
    assert pipeline_events[3].type == "pipeline_step_started"
    assert pipeline_events[3].data["step_name"] == "bug-diagnostician"
    assert pipeline_events[4].type == "pipeline_step_completed"
    assert pipeline_events[4].data["step_name"] == "bug-diagnostician"
    assert pipeline_events[5].type == "pipeline_completed"


@pytest.mark.asyncio
async def test_normalizer_pipeline_failure() -> None:
    """Normalizer emits pipeline_step_failed + pipeline_failed when a step errors."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(
            tool_name="run_bugfix_pipeline",
            input_={"issue_description": "crash"},
            output={"status": "ready", "pipeline": "bugfix", "js_code": "..."},
        ),
    ]
    stream._subagents = [
        _FakeSubagent("bug-diagnostician", "started", ["ossia", "agent", "bug-diagnostician"]),
        # First step fails
        _FakeSubagent("bug-diagnostician", "error", ["ossia", "agent", "bug-diagnostician"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    pipeline_events = [e for e in events if e.type.startswith("pipeline_")]
    # pipeline_started + step_started + step_failed + pipeline_failed = 4
    assert len(pipeline_events) == 4, f"expected 4 pipeline events, got {len(pipeline_events)}"

    assert pipeline_events[0].type == "pipeline_started"
    assert pipeline_events[0].data["pipeline_type"] == "bugfix"

    assert pipeline_events[1].type == "pipeline_step_started"
    assert pipeline_events[1].data["step_name"] == "bug-diagnostician"
    assert pipeline_events[1].data["step_index"] == 0

    assert pipeline_events[2].type == "pipeline_step_failed"
    assert pipeline_events[2].data["step_name"] == "bug-diagnostician"
    assert pipeline_events[2].data["step_index"] == 0

    assert pipeline_events[3].type == "pipeline_failed"
    assert pipeline_events[3].data["pipeline_id"] == pipeline_events[0].data["pipeline_id"]


@pytest.mark.asyncio
async def test_normalizer_pipeline_step_completed_emitted_before_pipeline_completed() -> None:
    """The final step emits both step_completed and pipeline_completed in the right order."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(
            tool_name="run_bugfix_pipeline",
            input_={"issue_description": "fix"},
            output={"status": "ready", "pipeline": "bugfix", "js_code": "..."},
        ),
    ]
    stream._subagents = [
        _FakeSubagent("bug-diagnostician", "started", ["ossia", "agent", "bug-diagnostician"]),
        _FakeSubagent("bug-diagnostician", "completed", ["ossia", "agent", "bug-diagnostician"]),
        _FakeSubagent("fix-proposer", "started", ["ossia", "agent", "fix-proposer"]),
        _FakeSubagent("fix-proposer", "completed", ["ossia", "agent", "fix-proposer"]),
        _FakeSubagent("test-runner", "started", ["ossia", "agent", "test-runner"]),
        _FakeSubagent("test-runner", "completed", ["ossia", "agent", "test-runner"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    pipeline_events = [e for e in events if e.type.startswith("pipeline_")]

    # The last two pipeline events should be: step_completed(test-runner) → pipeline_completed
    assert pipeline_events[-2].type == "pipeline_step_completed"
    assert pipeline_events[-2].data["step_name"] == "test-runner"
    assert pipeline_events[-2].data["step_index"] == 2
    assert pipeline_events[-1].type == "pipeline_completed"


@pytest.mark.asyncio
async def test_normalizer_no_pipeline_events_without_pipeline_tool() -> None:
    """Normalizer emits zero pipeline events when no pipeline tool is called."""
    stream = _FakeV3Stream()
    stream._tool_calls = [
        _FakeToolCall(tool_name="search_codebase", input_={"query": "test"}, output="results"),
    ]
    stream._subagents = [
        _FakeSubagent("code-researcher", "started", ["ossia", "agent", "code-researcher"]),
        _FakeSubagent("code-researcher", "completed", ["ossia", "agent", "code-researcher"]),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    pipeline_events = [e for e in events if e.type.startswith("pipeline_")]
    assert len(pipeline_events) == 0, f"expected 0 pipeline events, got {len(pipeline_events)}"


def test_normalizer_pipeline_step_name_constants() -> None:
    """Pipeline step definitions are internally consistent."""
    from core.events.normalizer import _PIPELINE_STEPS, _PIPELINE_TOOLS

    # Every pipeline type has a matching step list
    for pipeline_type in ("bugfix", "audit", "refactor"):
        assert pipeline_type in _PIPELINE_STEPS, f"missing steps for {pipeline_type}"
        assert len(_PIPELINE_STEPS[pipeline_type]) > 0, f"empty steps for {pipeline_type}"

    # Pipeline tools cover all known types
    assert len(_PIPELINE_TOOLS) == 3, "expected exactly 3 pipeline tools"


@pytest.mark.asyncio
async def test_normalizer_pipeline_event() -> None:
    """Normalizer emits artifact_received for request artifacts."""
    stream = _FakeV3Stream()
    stream._output = {}
    stream._interrupted = False

    class _FakeArtifact:
        type = "image"
        mime_type = "image/png"
        data = "base64data"
        url = None
        filename = "screenshot.png"

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream, artifacts=[_FakeArtifact()])]

    # 1 artifact_received + 1 image_analysis_completed + 1 complete = 3
    assert len(events) == 3
    assert events[0].type == "artifact_received"
    assert events[0].data["artifact_id"] is not None
    assert events[0].data["filename"] == "screenshot.png"
    assert events[1].type == "image_analysis_completed"
    assert events[1].data["artifact_id"] == "art-0"
    assert events[2].type == "complete"


@pytest.mark.asyncio
async def test_normalizer_async_tasks() -> None:
    """Normalizer emits async_task events from stream.values."""
    stream = _FakeV3Stream()
    stream._values = [
        _FakeValue(
            async_tasks=[
                {"task_id": "task-1", "name": "researcher", "status": "running"},
            ]
        ),
        _FakeValue(
            async_tasks=[
                {"task_id": "task-1", "name": "researcher", "status": "success"},
            ]
        ),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 1 async_task_started + 1 async_task_completed + 1 complete = 3
    assert len(events) == 3
    assert events[0].type == "async_task_started"
    assert events[0].data["task_id"] == "task-1"
    assert events[0].data["status"] == "running"
    assert events[1].type == "async_task_completed"
    assert events[2].type == "complete"


@pytest.mark.asyncio
async def test_normalizer_interrupt_flow() -> None:
    """Normalizer emits interrupt events before complete when stream is interrupted."""
    stream = _FakeV3Stream()
    stream._messages = [_FakeMsg("Please approve", role="ai", id_="msg-1")]
    stream._output = {}
    stream._interrupted = True
    stream._interrupts = [type("_Interrupt", (), {"value": {"type": "approve"}})()]

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 1 message_started + 1 message_completed + 1 interrupt + 1 complete = 4
    assert len(events) == 4
    assert events[0].type == "message_started"
    assert events[1].type == "message_completed"
    assert events[2].type == "interrupt"
    assert events[2].data["interrupts"][0]["type"] == "approve"
    assert events[3].type == "complete"
    assert events[3].data["interrupted"] is True


@pytest.mark.asyncio
async def test_normalizer_sequence_monotonic() -> None:
    """Normalizer assigns monotonically increasing seq numbers."""
    stream = _FakeV3Stream()
    stream._messages = [
        _FakeMsg("A", role="ai"),
        _FakeMsg("B", role="ai"),
        _FakeMsg("C", role="ai"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)  # monotonic
    assert len(set(seqs)) == len(seqs)  # unique


# ── _resolve_text projection tests ────────────────────────────────────────────
# These tests verify that `_resolve_text` correctly handles AsyncProjection
# (awaitable) and SyncTextProjection (str-able) objects emitted by the v3
# stream's .messages projection, instead of producing Python object reprs.


@pytest.mark.asyncio
async def test_resolve_text_async_projection() -> None:
    """_resolve_text awaits an AsyncProjection and returns the resolved text."""
    normalizer = EventNormalizer()
    proj = _FakeAsyncProjection("Hello from async projection")
    result = await normalizer._resolve_text(proj)
    assert result == "Hello from async projection"
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_resolve_text_async_projection_empty() -> None:
    """_resolve_text handles an empty AsyncProjection gracefully."""
    normalizer = EventNormalizer()
    proj = _FakeAsyncProjection("")
    result = await normalizer._resolve_text(proj)
    assert result == ""


@pytest.mark.asyncio
async def test_resolve_text_async_projection_with_unicode() -> None:
    """_resolve_text preserves Unicode in AsyncProjection resolved text."""
    normalizer = EventNormalizer()
    proj = _FakeAsyncProjection("Hello 世界 🌍")
    result = await normalizer._resolve_text(proj)
    assert result == "Hello 世界 🌍"


@pytest.mark.asyncio
async def test_resolve_text_sync_projection() -> None:
    """_resolve_text str()s a SyncTextProjection and returns the full text."""
    normalizer = EventNormalizer()
    proj = _FakeSyncProjection("Hello from sync projection")
    result = await normalizer._resolve_text(proj)
    assert result == "Hello from sync projection"
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_resolve_text_plain_string() -> None:
    """_resolve_text passes through a plain string unchanged."""
    normalizer = EventNormalizer()
    result = await normalizer._resolve_text("plain string")
    assert result == "plain string"


@pytest.mark.asyncio
async def test_resolve_text_plain_string_empty() -> None:
    """_resolve_text passes through an empty plain string unchanged."""
    normalizer = EventNormalizer()
    result = await normalizer._resolve_text("")
    assert result == ""


@pytest.mark.asyncio
async def test_resolve_text_non_string_fallback() -> None:
    """_resolve_text falls back to str() for non-string, non-projection types."""
    normalizer = EventNormalizer()
    result = await normalizer._resolve_text(42)
    assert result == "42"


@pytest.mark.asyncio
async def test_resolve_text_async_projection_detects_non_string() -> None:
    """_resolve_text correctly identifies AsyncProjection as non-string via __await__."""
    normalizer = EventNormalizer()
    proj = _FakeAsyncProjection("resolved")
    # Verify it's not a string (would go through awaitable path)
    assert not isinstance(proj, str)
    assert hasattr(proj, "__await__")
    result = await normalizer._resolve_text(proj)
    assert result == "resolved"


# ── Full normalizer flow with projection-style messages ──────────────────────


@pytest.mark.asyncio
async def test_normalizer_async_chat_model_stream_message() -> None:
    """Normalizer handles AsyncChatModelStream objects from stream.messages.

    Regression test for the bug where ``str(AsyncProjection)`` produced
    a Python object repr like ``'<...AsyncProjection object at 0x...>'``
    instead of the actual generated text. The normalizer must resolve
    the projection via ``_resolve_text`` before emitting events.
    """
    stream = _FakeV3Stream()
    stream._messages = [
        _FakeAsyncChatModelStream("Hello from async projection", message_id="proj-msg-1"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 1 message_started + 1 message_completed + 1 complete = 3
    assert len(events) == 3

    # message_started should contain the resolved text
    assert events[0].type == "message_started"
    assert events[0].data["text"] == "Hello from async projection"
    assert isinstance(events[0].data["text"], str)
    assert events[0].data["role"] == "ai"

    # message_completed should contain the accumulated text
    assert events[1].type == "message_completed"
    assert events[1].data["text"] == "Hello from async projection"
    assert isinstance(events[1].data["text"], str)
    assert events[1].data["role"] == "ai"

    # Verify the text is NOT a Python object repr (the original bug)
    assert not events[0].data["text"].startswith("<")
    assert not events[1].data["text"].startswith("<")


@pytest.mark.asyncio
async def test_normalizer_sync_chat_model_stream_message() -> None:
    """Normalizer handles ChatModelStream (sync variant) from stream.messages."""
    stream = _FakeV3Stream()
    stream._messages = [
        _FakeSyncChatModelStream("Hello from sync projection", message_id="sync-msg-1"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 1 message_started + 1 message_completed + 1 complete = 3
    assert len(events) == 3

    assert events[0].type == "message_started"
    assert events[0].data["text"] == "Hello from sync projection"
    assert events[1].type == "message_completed"
    assert events[1].data["text"] == "Hello from sync projection"

    # Verify NOT a Python object repr
    assert not events[1].data["text"].startswith("<")


@pytest.mark.asyncio
async def test_normalizer_mixed_message_types() -> None:
    """Normalizer handles a mix of plain _FakeMsg and projection-style messages."""
    stream = _FakeV3Stream()
    stream._messages = [
        _FakeMsg("Plain message", role="ai", id_="msg-1"),
        _FakeAsyncChatModelStream("Async projection message", message_id="msg-2"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # 2 message_started + 2 message_completed + 1 complete = 5
    assert len(events) == 5

    # First message: plain _FakeMsg
    assert events[0].type == "message_started"
    assert events[0].data["text"] == "Plain message"
    assert events[1].type == "message_completed"
    assert events[1].data["text"] == "Plain message"

    # Second message: AsyncChatModelStream (should have resolved text)
    assert events[2].type == "message_started"
    assert events[2].data["text"] == "Async projection message"
    assert not events[2].data["text"].startswith("<")
    assert events[3].type == "message_completed"
    assert events[3].data["text"] == "Async projection message"
    assert not events[3].data["text"].startswith("<")


@pytest.mark.asyncio
async def test_normalizer_async_projection_text_not_leaked_as_repr() -> None:
    """The normalizer never emits Python object reprs for projection text.

    This is the core regression test for the AsyncProjection bug: the
    message_completed event's text field must be the actual generated
    text, not a repr like ``'<...AsyncProjection object at 0x...>'``.
    """
    stream = _FakeV3Stream()
    stream._messages = [
        _FakeAsyncChatModelStream("Actual generated response", message_id="msg-1"),
    ]
    stream._output = {}
    stream._interrupted = False

    normalizer = EventNormalizer(thread_id="test")
    events = [e async for e in normalizer.normalize(stream)]

    # Collect all message events
    msg_events = [
        e for e in events if e.type in ("message_started", "message_delta", "message_completed")
    ]

    for ev in msg_events:
        text = ev.data.get("text", "")
        # The text should NOT be a Python object repr
        assert not text.startswith("<"), f"{ev.type} text is a Python object repr: {text!r}"
        # The text should NOT be the word "None"
        assert text != "None", f"{ev.type} text is 'None'"
        # The text should NOT be the str() of a non-string object
        assert text == "Actual generated response", (
            f"{ev.type} expected 'Actual generated response', got {text!r}"
        )


# ── Backward compatibility via StreamEvent ───────────────────────────────────


def test_stream_event_kind_includes_new_types() -> None:
    """StreamEvent accepts 'pipeline' and 'async_task' as valid kinds."""
    for kind in (
        "message",
        "tool_call",
        "subagent",
        "value",
        "artifact",
        "interrupt",
        "complete",
        "protocol",
        "async_task",
        "pipeline",
    ):
        evt = StreamEvent(kind=kind, data={"x": 1})  # type: ignore[arg-type]
        assert evt.kind == kind


def test_stream_event_rejects_unknown_kind() -> None:
    """StreamEvent rejects unknown kind values."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StreamEvent(kind="unknown_event_type", data={})  # type: ignore[arg-type]


# ── ThreadEventBuffer tests ──────────────────────────────────────────────────


def test_buffer_store_and_get() -> None:
    """ThreadEventBuffer stores events and returns them on get()."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    events = [
        OssiaEvent(seq=1, type="message_delta", data={"text": "Hello"}),
        OssiaEvent(seq=2, type="message_completed", data={"role": "ai", "text": "Hello"}),
        OssiaEvent(seq=3, type="complete", data={"interrupted": False, "output": {}}),
    ]
    buf.store("test-thread", events)

    retrieved = buf.get("test-thread")
    assert len(retrieved) == 3
    assert retrieved[0].type == "message_delta"
    assert retrieved[1].type == "message_completed"
    assert retrieved[2].type == "complete"

    # Unknown thread returns []
    assert buf.get("non-existent") == []


def test_buffer_empty_store_is_noop() -> None:
    """ThreadEventBuffer.store() with empty list does nothing."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    buf.store("test-thread", [])
    assert buf.get("test-thread") == []
    assert buf.get("other-thread") == []


def test_buffer_append_on_multiple_stores() -> None:
    """Multiple store() calls append events to the same thread buffer."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    buf.store("t1", [OssiaEvent(seq=1, type="message_delta", data={"text": "A"})])
    buf.store("t1", [OssiaEvent(seq=2, type="message_delta", data={"text": "B"})])
    buf.store("t1", [OssiaEvent(seq=3, type="complete", data={"interrupted": False, "output": {}})])

    retrieved = buf.get("t1")
    assert len(retrieved) == 3
    assert [e.seq for e in retrieved] == [1, 2, 3]


def test_buffer_thread_isolation() -> None:
    """Events from different threads are isolated."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    buf.store(
        "thread-alpha",
        [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})],
    )
    buf.store("thread-beta", [OssiaEvent(seq=1, type="message_delta", data={"text": "Beta"})])

    assert len(buf.get("thread-alpha")) == 1
    assert buf.get("thread-alpha")[0].data == {"interrupted": False, "output": {}}
    assert len(buf.get("thread-beta")) == 1
    assert buf.get("thread-beta")[0].data["text"] == "Beta"


def test_buffer_clear() -> None:
    """clear() removes all events for a specific thread."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    buf.store("t1", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})])
    buf.store("t2", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})])

    buf.clear("t1")
    assert buf.get("t1") == []
    assert len(buf.get("t2")) == 1  # t2 unaffected


def test_buffer_clear_all() -> None:
    """clear_all() removes all events across all threads."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    buf.store("t1", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})])
    buf.store("t2", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})])

    buf.clear_all()
    assert buf.get("t1") == []
    assert buf.get("t2") == []
    assert buf.thread_ids() == []


def test_buffer_thread_ids() -> None:
    """thread_ids() returns all threads with buffered events."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    assert buf.thread_ids() == []

    buf.store(
        "alpha", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})]
    )
    buf.store(
        "beta", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})]
    )

    ids = buf.thread_ids()
    assert "alpha" in ids
    assert "beta" in ids


def test_buffer_get_returns_copy() -> None:
    """get() returns a copy so mutating the result doesn't affect internal state."""
    from core.events.buffer import ThreadEventBuffer

    buf = ThreadEventBuffer()
    buf.store("t1", [OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})])

    retrieved = buf.get("t1")
    retrieved.append(OssiaEvent(seq=2, type="message_delta", data={"text": "extra"}))

    # Internal state should not have changed
    assert len(buf.get("t1")) == 1


def test_buffer_global_singleton() -> None:
    """get_thread_event_buffer() returns the same singleton instance."""
    from core.events.buffer import get_thread_event_buffer

    buf1 = get_thread_event_buffer()
    buf2 = get_thread_event_buffer()
    assert buf1 is buf2


def test_buffer_trim_exceeds_max() -> None:
    """When store() exceeds MAX_EVENTS_PER_THREAD, the buffer trims from the front."""
    from core.events.buffer import MAX_EVENTS_PER_THREAD, ThreadEventBuffer

    buf = ThreadEventBuffer()
    many = [
        OssiaEvent(seq=i, type="message_delta", data={"text": str(i)})
        for i in range(MAX_EVENTS_PER_THREAD + 50)
    ]
    buf.store("t1", many)

    retrieved = buf.get("t1")
    # Should be trimmed to MAX_EVENTS_PER_THREAD
    assert len(retrieved) == MAX_EVENTS_PER_THREAD
    # Should have trimmed from the front (oldest events dropped)
    assert retrieved[0].data["text"] == str(50)  # seq 50 was the 51st event
    assert retrieved[-1].data["text"] == str(MAX_EVENTS_PER_THREAD + 49)  # last event kept
