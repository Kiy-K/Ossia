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

load_dotenv(find_dotenv(usecwd=True))

from ossia.api import app  # noqa: E402
from ossia.config import get_settings  # noqa: E402

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
    from ossia.schemas import StreamEvent

    for kind in (
        "message",
        "tool_call",
        "subagent",
        "value",
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
    from ossia.schemas import ResumeDecision, ResumeRequest

    for dtype in ("approve", "edit", "reject", "respond"):
        d = ResumeDecision(type=dtype)  # type: ignore[arg-type]
        req = ResumeRequest(decisions=[d])
        assert req.decisions[0].type == dtype
    with pytest.raises(ValueError):
        ResumeDecision(type="maybe")  # type: ignore[arg-type]
