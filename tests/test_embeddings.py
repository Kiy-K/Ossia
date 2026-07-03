"""Tests for the Ollama embedding adapter.

Mocks httpx to avoid hitting the real Ollama server in CI. The
adapter is small (one POST per text, gathered concurrently) so the
tests cover: the URL + body shape, the response parsing, error
propagation, and concurrent dispatch.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from core.config import Settings
from core.embeddings import make_ollama_embedder


def _settings(**overrides: Any) -> Settings:
    """Return a Settings instance with the test embedder config."""
    overrides.setdefault("ollama_base_url", "http://example-ollama:11434")
    overrides.setdefault("embedding_model", "embeddinggemma")
    overrides.setdefault("embedding_dim", 768)
    return Settings(**overrides)


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stub: records requests, returns canned responses."""

    def __init__(self, responses: list[dict[str, Any]], status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)
        self._status = status
        self._index = 0

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def post(self, url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        request = httpx.Request("POST", url, json=json)
        self.requests.append(request)
        body = self._responses[self._index]
        self._index += 1
        return httpx.Response(self._status, json=body, request=request)


@pytest.mark.asyncio
async def test_embed_sends_correct_url_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """The embedder POSTs to ``{ollama_base_url}/api/embeddings`` with
    the configured model and the input text as ``prompt``."""
    fake = _FakeAsyncClient(responses=[{"embedding": [0.1] * 768}, {"embedding": [0.2] * 768}])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: fake)

    embed = make_ollama_embedder(_settings())
    result = await embed(["hello", "world"])

    assert len(fake.requests) == 2
    assert str(fake.requests[0].url) == "http://example-ollama:11434/api/embeddings"
    body1 = json.loads(fake.requests[0].content.decode())
    assert body1 == {"model": "embeddinggemma", "prompt": "hello"}
    body2 = json.loads(fake.requests[1].content.decode())
    assert body2 == {"model": "embeddinggemma", "prompt": "world"}

    assert len(result) == 2
    assert result[0] == [0.1] * 768
    assert result[1] == [0.2] * 768


@pytest.mark.asyncio
async def test_embed_uses_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model name comes from Settings, not a constant."""
    fake = _FakeAsyncClient(responses=[{"embedding": [0.0] * 384}])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: fake)

    embed = make_ollama_embedder(
        _settings(embedding_model="qwen3-embedding:0.6b", embedding_dim=1024)
    )
    await embed(["test"])
    body = json.loads(fake.requests[0].content.decode())
    assert body["model"] == "qwen3-embedding:0.6b"


@pytest.mark.asyncio
async def test_embed_strips_trailing_slash_from_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing ``/`` on the base URL does not produce ``//api/embeddings``."""
    fake = _FakeAsyncClient(responses=[{"embedding": [0.0] * 768}])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: fake)

    embed = make_ollama_embedder(_settings(ollama_base_url="http://x:11434/"))
    await embed(["y"])
    assert str(fake.requests[0].url) == "http://x:11434/api/embeddings"


@pytest.mark.asyncio
async def test_embed_propagates_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP error from Ollama raises; the caller sees the failure."""
    fake = _FakeAsyncClient(responses=[{"error": "model not loaded"}], status=500)
    monkeypatch.setattr(httpx, "AsyncClient", lambda: fake)
    embed = make_ollama_embedder(_settings())
    with pytest.raises(httpx.HTTPStatusError):
        await embed(["x"])


@pytest.mark.asyncio
async def test_embed_returns_one_vector_per_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A list of N texts produces a list of N vectors (same order)."""
    fake = _FakeAsyncClient(responses=[{"embedding": [float(i)] * 768} for i in range(3)])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: fake)

    embed = make_ollama_embedder(_settings())
    result = await embed(["a", "b", "c"])
    assert [v[0] for v in result] == [0.0, 1.0, 2.0]
