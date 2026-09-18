"""Unit tests for OllamaProvider (spark.reasoning.providers.ollama).

Mocks at the ``httpx.AsyncClient`` boundary (Ollama has no dedicated SDK —
this provider talks to its local HTTP server directly with ``httpx``, per
BUILD_SPEC §6.7 and the task that created this module), by monkeypatching
``spark.reasoning.providers.ollama.httpx.AsyncClient`` with a fake async
context manager, and by raising real ``httpx`` exception classes to prove
the connection-failure -> ``ProviderUnavailableError`` translation actually
matches on the real exception hierarchy, not a stand-in.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from spark.reasoning.provider import ImagePart, Message, MessageRole, ProviderUnavailableError
from spark.reasoning.providers import ollama as ollama_module
from spark.reasoning.providers.ollama import OllamaProvider


class _FakeHttpResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.request = httpx.Request("POST", "http://localhost:11434/api/chat")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, calls, response=None, post_error=None, **kwargs):
        self._calls = calls
        self._response = response
        self._post_error = post_error
        self.init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, path, json):  # noqa: A002 - matches httpx's own kwarg name
        self._calls.append({"path": path, "json": json})
        if self._post_error is not None:
            raise self._post_error
        return self._response


def _install_fake_client(monkeypatch, *, response=None, post_error=None):
    calls = []

    def factory(**kwargs):
        return _FakeAsyncClient(calls, response=response, post_error=post_error, **kwargs)

    monkeypatch.setattr(ollama_module.httpx, "AsyncClient", factory)
    return calls


@pytest.mark.asyncio
async def test_text_only_completion_returns_raw_completion_with_usage(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "hello from llama"},
        "done": True,
        "prompt_eval_count": 18,
        "eval_count": 6,
    }
    calls = _install_fake_client(monkeypatch, response=_FakeHttpResponse(payload))

    provider = OllamaProvider(model="llama3.2")
    completion = await provider._raw_complete(
        [Message(role=MessageRole.user, text="hi")], max_tokens=128, temperature=0.7
    )

    assert completion.text == "hello from llama"
    assert completion.usage.input_tokens == 18
    assert completion.usage.output_tokens == 6
    assert len(calls) == 1
    assert calls[0]["path"] == "/api/chat"
    body = calls[0]["json"]
    assert body["model"] == "llama3.2"
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0.7
    assert body["options"]["num_predict"] == 128


@pytest.mark.asyncio
async def test_multimodal_completion_includes_base64_image_in_request(monkeypatch):
    payload = {
        "message": {"role": "assistant", "content": "I see a fish"},
        "done": True,
        "prompt_eval_count": 25,
        "eval_count": 5,
    }
    calls = _install_fake_client(monkeypatch, response=_FakeHttpResponse(payload))

    provider = OllamaProvider(model="llava")
    image_bytes = b"\x89PNG-fake-bytes"
    await provider._raw_complete(
        [
            Message(
                role=MessageRole.user,
                text="what is this?",
                images=[ImagePart(data=image_bytes, mime_type="image/png")],
            )
        ],
        max_tokens=100,
        temperature=0.2,
    )

    body = calls[0]["json"]
    sent_message = body["messages"][0]
    assert sent_message["images"] == [base64.b64encode(image_bytes).decode("ascii")]
    # Ollama's own format takes a *raw* base64 string, no data: URI prefix.
    assert not sent_message["images"][0].startswith("data:")


@pytest.mark.asyncio
async def test_connection_failure_raises_provider_unavailable_naming_ollama(monkeypatch):
    _install_fake_client(
        monkeypatch, post_error=httpx.ConnectError("Connection refused")
    )

    provider = OllamaProvider(model="llama3.2", base_url="http://localhost:11434")

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )
    message = str(excinfo.value)
    assert "Ollama" in message
    assert "running" in message.lower()


@pytest.mark.asyncio
async def test_http_error_status_raises_provider_unavailable(monkeypatch):
    payload = {"error": "model 'llama3.2' not found"}
    _install_fake_client(monkeypatch, response=_FakeHttpResponse(payload, status_code=404))

    provider = OllamaProvider(model="llama3.2")

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )
    assert "404" in str(excinfo.value)
