"""Integration tests for the unified HTTP API.

Exercises the real FastAPI app via ``TestClient`` against the in-process
agent. Verifies auth, error envelope, request_id, the typed Pydantic
surface, the eval/audit routes, and the threads/* endpoints.

No live LLM calls are made: routes that would invoke the agent are either
exercised against a test-specific path (``/v1/eval`` with no API key set)
or stubbed via direct graph calls. Streaming, history, and resume are
covered by the unit tests in ``test_graph.py``.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import suppress

import pytest
from dotenv import find_dotenv, load_dotenv
from fastapi.testclient import TestClient
from pydantic import ValidationError as _PydanticVE

load_dotenv(find_dotenv(usecwd=True))

from core.api import app  # noqa: E402
from core.config import get_settings  # noqa: E402

API_KEY = "test-api-key"


@pytest.fixture(scope="module", autouse=True)
def _api_test_env() -> Generator[None, None, None]:
    """Lock the API test env for this module's whole lifetime.

    Module-scoped because the ``client`` fixture (also module-scoped)
    enters the FastAPI lifespan once, and the lifespan reads the env at
    startup. Function-scoping would let the env revert between the
    client fixture and the test, breaking the lifespan's view.

    Restores the original values and clears the settings cache on
    teardown so other test modules (``test_graph``, ``test_mcp_tools``)
    are not affected by what this module does to the env.
    """
    saved = {k: os.environ.get(k) for k in ("OSSIA_API_KEY", "ENABLE_HUMAN_REVIEW", "POSTGRES_URL")}
    os.environ["OSSIA_API_KEY"] = API_KEY
    os.environ["ENABLE_HUMAN_REVIEW"] = "false"
    os.environ["POSTGRES_URL"] = ""
    get_settings.cache_clear()
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    """Module-scoped TestClient; lifespan is started once for the whole module."""
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    assert "X-Request-ID" in r.headers


def test_chat_requires_api_key(client: TestClient) -> None:
    r = client.post("/v1/chat", json={"message": "hi"})
    assert r.status_code == 401
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "http_401"
    assert "request_id" in body["error"]


def test_chat_rejects_bad_api_key(client: TestClient) -> None:
    r = client.post(
        "/v1/chat",
        json={"message": "hi"},
        headers={"X-API-Key": "wrong"},
    )
    assert r.status_code == 401


def test_chat_validates_request_body(client: TestClient) -> None:
    r = client.post(
        "/v1/chat",
        json={},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "http_422"


def test_chat_validates_message_nonempty(client: TestClient) -> None:
    r = client.post(
        "/v1/chat",
        json={"message": ""},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 422


def test_threads_history_empty_for_unknown_thread(client: TestClient) -> None:
    r = client.get(
        "/v1/threads/never-existed/history",
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"].endswith(":never-existed")
    assert body["messages"] == []


def test_threads_state_empty_for_unknown_thread(client: TestClient) -> None:
    r = client.get(
        "/v1/threads/never-existed/state",
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"].endswith(":never-existed")
    assert body["values"] == {}
    assert body["next"] == []


def test_tools_returns_core_tools(client: TestClient) -> None:
    r = client.get("/v1/tools", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    body = r.json()
    names = {t["name"] for t in body["tools"]}
    assert "search_knowledge_base" in names
    assert "send_response" in names
    assert "grade_response" in names
    for t in body["tools"]:
        assert t["source"] in {"core", "mcp"}


def test_eval_with_no_api_key_returns_empty_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When OPENROUTER_API_KEY is unset, /v1/eval returns ok=false (no crash)."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = client.post(
        "/v1/eval",
        json={"dataset_path": "tests/golden_dataset.json", "min_pass_rate": 0.5},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["queries"] == []


def test_audit_returns_structured_report(client: TestClient) -> None:
    r = client.get("/v1/audit", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    body = r.json()
    assert "sections" in body
    assert "ok" in body
    names = {s["name"] for s in body["sections"]}
    assert {"memory", "process", "fix-verifications", "runtime", "langsmith"} <= names
    for s in body["sections"]:
        assert "checks" in s
        assert isinstance(s["ok"], bool)


def test_request_id_echo(client: TestClient) -> None:
    """Client-supplied X-Request-ID is echoed on the response."""
    rid = "test-request-id-12345"
    r = client.get(
        "/health",
        headers={"X-Request-ID": rid},
    )
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == rid


def test_request_id_in_error_envelope(client: TestClient) -> None:
    rid = "test-request-id-error"
    r = client.post(
        "/v1/chat",
        json={},
        headers={"X-API-Key": API_KEY, "X-Request-ID": rid},
    )
    assert r.status_code == 422
    assert r.json()["error"]["request_id"] == rid


def test_thread_ids_scoped_to_caller(client: TestClient) -> None:
    """Two different thread ids from the same caller are scoped, not mixed."""
    r1 = client.get(
        "/v1/threads/alpha/state",
        headers={"X-API-Key": API_KEY},
    )
    r2 = client.get(
        "/v1/threads/beta/state",
        headers={"X-API-Key": API_KEY},
    )
    assert r1.json()["thread_id"] != r2.json()["thread_id"]


def test_resume_with_empty_decisions_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/threads/anything/resume",
        json={"decisions": []},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 422


def test_resume_with_invalid_decision_type_rejected(client: TestClient) -> None:
    r = client.post(
        "/v1/threads/anything/resume",
        json={"decisions": [{"type": "maybe"}]},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 422


def test_resume_accepts_all_four_decision_types(client: TestClient) -> None:
    """approve / edit / reject / respond are all valid per DeepAgents docs."""
    for dtype in ("approve", "edit", "reject", "respond"):
        payload: dict = {"decisions": [{"type": dtype}]}
        if dtype == "edit":
            payload["decisions"][0]["edited_action"] = {"name": "x", "args": {}}
        if dtype in ("reject", "respond"):
            payload["decisions"][0]["message"] = "ok"
        r = client.post(
            "/v1/threads/anything/resume",
            json=payload,
            headers={"X-API-Key": API_KEY},
        )
        # The checkpointer is None in test env, so ainvoke fails on its
        # own; what we care about is that the request validates (no 422).
        assert r.status_code != 422, f"type={dtype} unexpectedly rejected by validation: {r.text}"


def test_resume_rejects_top_level_feedback(client: TestClient) -> None:
    """The upstream API has no top-level 'feedback' field; it lives on each decision."""
    r = client.post(
        "/v1/threads/anything/resume",
        json={"decisions": [{"type": "approve"}], "feedback": "looks good"},
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 422


def test_chat_stream_sends_sse_headers(client: TestClient) -> None:
    """Streaming response uses text/event-stream content type.

    We do not iterate the body here; that would require a live LLM and
    consumes a real run. The header check is enough to lock the contract.
    """
    with suppress(ImportError):
        # Streaming consumes a real run; skip if no LLM key in env.
        if not os.environ.get("OPENROUTER_API_KEY"):
            pytest.skip("no OPENROUTER_API_KEY; skipping streaming smoke test")
        with client.stream(
            "POST",
            "/v1/chat/stream",
            json={"message": "ping"},
            headers={"X-API-Key": API_KEY},
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            for _ in r.iter_lines():
                break


def test_stream_event_schema_validates_v3_envelope() -> None:
    """Lock the v3 wire contract: StreamEvent accepts the new kind discriminator."""
    from core.schemas import StreamEvent

    for kind in (
        "message",
        "tool_call",
        "subagent",
        "value",
        "artifact",
        "interrupt",
        "complete",
        "protocol",
    ):
        evt = StreamEvent(kind=kind, data={"x": 1})  # type: ignore[arg-type]
        assert evt.kind == kind
    # Unknown kinds are rejected by the Literal discriminator.
    with pytest.raises(ValueError):
        StreamEvent(kind="unknown_kind", data={})  # type: ignore[arg-type]


def test_resume_request_supports_all_four_decision_types() -> None:
    """Lock the resume contract: approve / edit / reject / respond are all valid."""
    from core.schemas import ResumeDecision, ResumeRequest

    for dtype in ("approve", "edit", "reject", "respond"):
        d = ResumeDecision(type=dtype)  # type: ignore[arg-type]
        req = ResumeRequest(decisions=[d])
        assert req.decisions[0].type == dtype
    with pytest.raises(ValueError):
        ResumeDecision(type="maybe")  # type: ignore[arg-type]


# ── Multimodal artifact tests ────────────────────────────────────────────────


def test_normalize_artifact_base64_image() -> None:
    """A base64 image artifact becomes an image_url content block with a data URI."""
    from core.api import _normalize_artifact
    from core.schemas import Artifact

    art = Artifact(
        type="image",
        mime_type="image/png",
        data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAA=",
        filename="screenshot.png",
    )
    result = _normalize_artifact(art)
    assert result is not None
    assert result["type"] == "image_url"
    assert result["image_url"]["url"].startswith("data:image/png;base64,iVBORw0K")


def test_normalize_artifact_url_image() -> None:
    """A URL-referenced image artifact becomes an image_url content block."""
    from core.api import _normalize_artifact
    from core.schemas import Artifact

    art = Artifact(
        type="image",
        mime_type="image/png",
        url="https://example.com/screenshot.png",
        filename="screenshot.png",
    )
    result = _normalize_artifact(art)
    assert result is not None
    assert result["type"] == "image_url"
    assert result["image_url"]["url"] == "https://example.com/screenshot.png"


def test_normalize_artifact_document_as_text() -> None:
    """Non-image artifacts (document, audio, video) are embedded as text descriptions."""
    from core.api import _normalize_artifact
    from core.schemas import Artifact

    art = Artifact(
        type="document",
        mime_type="application/pdf",
        data="JVBERi0xLjQK...",
        filename="diagram.pdf",
    )
    result = _normalize_artifact(art)
    assert result is not None
    assert result["type"] == "text"
    assert "diagram.pdf" in result["text"]
    assert "document" in result["text"]
    assert "application/pdf" in result["text"]


def test_normalize_artifact_audio_as_text() -> None:
    """Audio artifacts become text descriptions with metadata."""
    from core.api import _normalize_artifact
    from core.schemas import Artifact

    art = Artifact(
        type="audio",
        mime_type="audio/wav",
        url="https://example.com/recording.wav",
        filename="recording.wav",
    )
    result = _normalize_artifact(art)
    assert result is not None
    assert result["type"] == "text"
    assert "recording.wav" in result["text"]
    assert "audio" in result["text"]
    assert "URL" in result["text"]


def test_normalize_artifact_video_as_text() -> None:
    """Video artifacts become text descriptions with metadata."""
    from core.api import _normalize_artifact
    from core.schemas import Artifact

    art = Artifact(
        type="video",
        mime_type="video/mp4",
        url="https://example.com/demo.mp4",
        filename="demo.mp4",
    )
    result = _normalize_artifact(art)
    assert result is not None
    assert result["type"] == "text"
    assert "demo.mp4" in result["text"]
    assert "video" in result["text"]


def test_normalize_artifact_no_data_or_url() -> None:
    """An artifact with neither data nor url returns None (silently dropped)."""
    from core.api import _normalize_artifact
    from core.schemas import Artifact

    art = Artifact(
        type="image",
        mime_type="image/png",
        filename="missing.png",
    )
    result = _normalize_artifact(art)
    assert result is None


def test_msg_to_chat_message_extracts_image_artifact() -> None:
    """A HumanMessage with an image_url content block yields a ChatMessage with artifacts."""
    from langchain_core.messages import HumanMessage

    from core.api import _msg_to_chat_message

    msg = HumanMessage(
        content=[
            {"type": "text", "text": "What is in this screenshot?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
    )
    result = _msg_to_chat_message(msg)
    assert result.role == "user"
    assert result.content == "What is in this screenshot?"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].type == "image"
    assert result.artifacts[0].filename == "image-1.png"
    assert result.artifacts[0].mime_type == "image/png"
    assert result.artifacts[0].analysis_state == "pending"


def test_msg_to_chat_message_extracts_multiple_images() -> None:
    """Multiple image_url blocks yield multiple ArtifactInfo entries."""
    from langchain_core.messages import HumanMessage

    from core.api import _msg_to_chat_message

    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Compare these:"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,before"}},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,after"}},
        ]
    )
    result = _msg_to_chat_message(msg)
    assert len(result.artifacts) == 2
    assert result.artifacts[0].id == "img-1"
    assert result.artifacts[1].id == "img-2"


def test_msg_to_chat_message_extracts_file_artifact() -> None:
    """A HumanMessage with a file content block yields a ChatMessage with document artifact."""
    from langchain_core.messages import HumanMessage

    from core.api import _msg_to_chat_message

    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Analyze this PDF"},
            {
                "type": "file",
                "source": {"type": "base64", "mime_type": "application/pdf"},
                "filename": "report.pdf",
            },
        ]
    )
    result = _msg_to_chat_message(msg)
    assert result.role == "user"
    assert len(result.artifacts) == 1
    assert result.artifacts[0].type == "document"
    assert result.artifacts[0].filename == "report.pdf"
    assert result.artifacts[0].mime_type == "application/pdf"


def test_msg_to_chat_message_text_only_yields_empty_artifacts() -> None:
    """Backward compat: a text-only message produces no artifacts."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from core.api import _msg_to_chat_message

    for msg in [
        HumanMessage(content="Hello"),
        AIMessage(content="Hi there"),
        ToolMessage(content="result", tool_call_id="call-1"),
    ]:
        result = _msg_to_chat_message(msg)
        assert len(result.artifacts) == 0, f"expected empty artifacts for msg type {type(msg).__name__}"


def test_msg_to_chat_message_with_artifact_message_attribute() -> None:
    """When a message has an 'artifacts' attribute set by middleware, it is picked up.

    This tests the fallback path in _msg_to_chat_message that reads
    ``msg.artifacts`` or ``msg.artifact_refs`` when no content blocks
    produce artifacts.
    """
    from langchain_core.messages import HumanMessage

    from core.api import _msg_to_chat_message
    from core.schemas import ArtifactInfo

    msg = HumanMessage(content="Here is the analysis result.")
    refs = [
        ArtifactInfo(
            id="img-0",
            type="image",
            filename="chart.png",
            mime_type="image/png",
            analysis_state="completed",
            analysis_result="Revenue chart showing Q3 growth",
        )
    ]
    msg.artifacts = refs
    result = _msg_to_chat_message(msg)
    assert len(result.artifacts) == 1
    assert result.artifacts[0].id == "img-0"
    assert result.artifacts[0].analysis_state == "completed"
    assert result.artifacts[0].analysis_result == "Revenue chart showing Q3 growth"


def test_artifact_schema_valid_types_accepted() -> None:
    """ChatRequest accepts valid artifact configurations."""
    from core.schemas import Artifact, ChatRequest

    # Image with base64 data
    req = ChatRequest(
        message="Analyze this",
        artifacts=[
            Artifact(type="image", mime_type="image/png", data="base64data"),
        ],
    )
    assert len(req.artifacts) == 1
    assert req.artifacts[0].type == "image"

    # Image with URL
    req2 = ChatRequest(
        message="Check this URL",
        artifacts=[
            Artifact(type="image", mime_type="image/jpeg", url="https://example.com/photo.jpg"),
        ],
    )
    assert len(req2.artifacts) == 1
    assert req2.artifacts[0].url == "https://example.com/photo.jpg"

    # Multiple artifacts
    req3 = ChatRequest(
        message="Multiple artifacts",
        artifacts=[
            Artifact(type="image", mime_type="image/png", data="img1"),
            Artifact(type="document", mime_type="application/pdf", url="https://example.com/doc.pdf"),
            Artifact(type="audio", mime_type="audio/wav", data="audio1"),
            Artifact(type="video", mime_type="video/mp4", url="https://example.com/vid.mp4"),
        ],
    )
    assert len(req3.artifacts) == 4
    assert [a.type for a in req3.artifacts] == ["image", "document", "audio", "video"]

    # No artifacts (backward compat)
    req4 = ChatRequest(message="No artifacts")
    assert req4.artifacts == []


def test_artifact_schema_rejects_extra_fields() -> None:
    """Artifact schema ignores extra fields via extra='forbid'."""
    from core.schemas import Artifact

    with pytest.raises(_PydanticVE):
        Artifact(
            type="image",
            mime_type="image/png",
            data="abc",
            unknown_field="not allowed",  # type: ignore[call-arg]
        )


def test_artifact_schema_rejects_invalid_type() -> None:
    """Artifact type must be one of image/document/audio/video."""
    from core.schemas import Artifact

    with pytest.raises(_PydanticVE):
        Artifact(
            type="invalid_type",  # type: ignore[arg-type]
            mime_type="image/png",
            data="abc",
        )


def test_chat_request_rejects_extra_fields_on_artifacts(client: TestClient) -> None:
    """API request with extra fields in artifacts returns 422."""
    r = client.post(
        "/v1/chat",
        json={
            "message": "hi",
            "artifacts": [
                {
                    "type": "image",
                    "mime_type": "image/png",
                    "data": "abc",
                    "unknown": "field",
                }
            ],
        },
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 422


def test_chat_stream_with_artifacts_returns_sse(client: TestClient) -> None:
    """Streaming with artifacts returns 200 and SSE headers.

    We do not iterate the body (needs a live LLM), but the header check
    confirms the request is well-formed through validation.
    """
    from contextlib import suppress

    with suppress(ImportError):
        if not os.environ.get("OPENROUTER_API_KEY"):
            pytest.skip("no OPENROUTER_API_KEY; skipping streaming test with artifacts")
        with client.stream(
            "POST",
            "/v1/chat/stream",
            json={
                "message": "What is in this image?",
                "artifacts": [
                    {
                        "type": "image",
                        "mime_type": "image/png",
                        "url": "https://example.com/screenshot.png",
                        "filename": "screenshot.png",
                    }
                ],
            },
            headers={"X-API-Key": API_KEY},
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")


# ── Artifact persistence tests ───────────────────────────────────────────────


def test_artifact_info_preserved_across_chat_messages() -> None:
    """ArtifactInfo survives reconstruction through the checkpointer's message format.

    This test constructs a HumanMessage with multimodal content blocks (the same
    shape produced by _build_invocation after normalization), then passes it
    through _msg_to_chat_message to verify the ArtifactInfo metadata is correctly
    reconstructed. This simulates the checkpoint round-trip: the checkpointer
    stores the message as-is (preserving content blocks), and on retrieval
    _msg_to_chat_message re-derives the artifact metadata.
    """
    from langchain_core.messages import HumanMessage

    from core.api import _msg_to_chat_message

    # Simulate what _build_invocation creates after normalizing artifacts
    original_content = [
        {"type": "text", "text": "What is in this diagram?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,chart123"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,diagram456"}},
    ]
    msg = HumanMessage(content=original_content)

    # First pass: reconstruct artifacts
    result = _msg_to_chat_message(msg)
    assert len(result.artifacts) == 2
    assert result.artifacts[0].id == "img-1"
    assert result.artifacts[0].type == "image"
    assert result.artifacts[0].mime_type == "image/png"
    assert result.artifacts[1].id == "img-2"

    # Simulate checkpoint round-trip by reconstructing a new message from the
    # same content blocks (the checkpointer preserves content as-is).
    restored_msg = HumanMessage(content=original_content)
    restored_result = _msg_to_chat_message(restored_msg)
    assert len(restored_result.artifacts) == 2
    assert restored_result.artifacts[0].id == "img-1"
    assert restored_result.artifacts[0].type == "image"
    assert restored_result.artifacts[1].filename == "image-2.png"

    # Verify artifact IDs are deterministic (same content → same IDs)
    assert result.artifacts[0].id == restored_result.artifacts[0].id
    assert result.artifacts[1].id == restored_result.artifacts[1].id


def test_artifact_info_in_thread_history_no_checkpointer(client: TestClient) -> None:
    """Thread history returns empty messages when no checkpointer is configured.

    This confirms backward compatibility: the history endpoint doesn't crash
    when asked about a thread that never ran (no checkpointer in test env).
    """
    r = client.get(
        "/v1/threads/artifact-thread/history",
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == []
    # The thread_id is scoped to the caller
    assert ":artifact-thread" in body["thread_id"]


# ── Thread events buffer / replay tests ──────────────────────────────────────


def test_thread_events_returns_empty_for_unknown_thread(client: TestClient) -> None:
    """GET /v1/threads/{id}/events returns empty events for unknown threads."""
    r = client.get(
        "/v1/threads/never-existed/events",
        headers={"X-API-Key": API_KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"].endswith(":never-existed")
    assert body["events"] == []
    assert body["count"] == 0


@pytest.mark.asyncio
async def test_thread_events_returns_events_after_stream(client: TestClient) -> None:
    """After a stream completes, events are available via the events endpoint.

    This test uses the normalizer directly to simulate what chat_stream does:
    normalize events → serialize to SSE → buffer for replay. It computes the
    actual caller hash so stored events match the scoped thread_id the endpoint
    will compute from the API key.
    """
    import hashlib

    from core.events import EventNormalizer, get_thread_event_buffer, serialize_sse

    # Compute the actual caller hash the same way verify_api_key does.
    # This ensures the scoped thread_id we store matches what the
    # endpoint computes from the API key header.
    from argon2 import low_level as argon2_low_level
    caller_hash = argon2_low_level.hash_secret_raw(
        secret=b"test-api-key",
        salt=b"ossia-caller-id",  # must be exactly 16 bytes
        time_cost=2,
        memory_cost=65536,
        parallelism=1,
        hash_len=16,
        type=argon2_low_level.Type.ID,
    ).hex()

    # Clear any previous state from the singleton
    buf = get_thread_event_buffer()
    buf.clear_all()

    # Create a fake stream to produce events
    from tests.test_events import _FakeMsg, _FakeV3Stream

    stream = _FakeV3Stream()
    stream._messages = [
        _FakeMsg("Hello", role="ai", id_="msg-1"),
        _FakeMsg(" World", role="ai", id_="msg-1"),
    ]
    stream._output = {}
    stream._interrupted = False

    # Simulate what chat_stream does: normalize events, serialize to SSE,
    # then store them in the buffer for replay.
    scoped_thread = f"{caller_hash}:stream-thread"
    normalizer = EventNormalizer(thread_id=scoped_thread)
    collected: list[Any] = []
    async for event in normalizer.normalize(stream):
        collected.append(event)
        serialize_sse(event)  # simulate streaming
    if collected:
        buf.store(scoped_thread, collected)

    # Now verify via the API endpoint
    r = client.get(
        "/v1/threads/stream-thread/events",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"] == scoped_thread
    assert body["count"] == 4  # message_started + message_delta + message_completed + complete
    assert len(body["events"]) == 4

    # Verify event ordering
    assert body["events"][0]["type"] == "message_started"
    assert body["events"][0]["data"]["text"] == "Hello"
    assert body["events"][1]["type"] == "message_delta"
    assert body["events"][1]["data"]["text"] == " World"
    assert body["events"][2]["type"] == "message_completed"
    assert body["events"][3]["type"] == "complete"

    # Verify each event has the standard fields
    for evt in body["events"]:
        assert "id" in evt
        assert "seq" in evt
        assert "timestamp" in evt
        assert "source" in evt
        assert "thread_id" in evt
        assert evt["thread_id"] == scoped_thread

    # Clear events via DELETE
    r_del = client.delete(
        "/v1/threads/stream-thread/events",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r_del.status_code == 200
    del_body = r_del.json()
    assert del_body["thread_id"] == scoped_thread
    assert del_body["cleared"] is True

    # Verify events are gone after DELETE
    r_after = client.get(
        "/v1/threads/stream-thread/events",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r_after.status_code == 200
    after_body = r_after.json()
    assert after_body["count"] == 0
    assert after_body["events"] == []


def test_thread_events_delete_does_not_affect_other_threads(client: TestClient) -> None:
    """DELETE events for one thread does not affect other threads."""
    import hashlib

    from core.events import (
        OssiaEvent,
        get_thread_event_buffer,
    )

    from argon2 import low_level as argon2_low_level
    caller_hash = argon2_low_level.hash_secret_raw(
        secret=b"test-api-key",
        salt=b"ossia-caller-id",  # must be exactly 16 bytes
        time_cost=2,
        memory_cost=65536,
        parallelism=1,
        hash_len=16,
        type=argon2_low_level.Type.ID,
    ).hex()
    buf = get_thread_event_buffer()
    buf.clear_all()

    evt = OssiaEvent(seq=1, type="complete", data={"interrupted": False, "output": {}})
    buf.store(f"{caller_hash}:alpha", [evt])
    buf.store(f"{caller_hash}:beta", [evt])

    # Delete alpha
    r = client.delete(
        "/v1/threads/alpha/events",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r.status_code == 200

    # Alpha should be empty
    r_alpha = client.get(
        "/v1/threads/alpha/events",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r_alpha.json()["count"] == 0

    # Beta should still have its events
    r_beta = client.get(
        "/v1/threads/beta/events",
        headers={"X-API-Key": "test-api-key"},
    )
    assert r_beta.json()["count"] == 1
