"""Unit tests for OpenAIProvider (spark.reasoning.providers.openai).

Mocks the ``openai`` SDK boundary at the same seam the module itself uses
for lazy import (``OpenAIProvider._import_sdk``'s ``import openai``
statement), via the ``sys.modules[name] = None`` sentinel technique used in
``tests/unit/test_windows_ocr.py`` for 'winsdk not installed'.
"""
from __future__ import annotations

import base64
import sys

import pytest

from spark.reasoning.provider import ImagePart, Message, MessageRole, ProviderUnavailableError
from spark.reasoning.providers.openai import OpenAIProvider


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, output_text, usage):
        self.output_text = output_text
        self.usage = usage


class _FakeResponses:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def create(self, **kwargs):
        self._calls.append(kwargs)
        return self._response


class _FakeAsyncOpenAI:
    def __init__(self, response, calls, api_key=None):
        self.api_key = api_key
        self.responses = _FakeResponses(response, calls)


def _install_fake_sdk(monkeypatch, *, response, calls):
    class _FakeOpenAIModule:
        @staticmethod
        def AsyncOpenAI(api_key=None):
            return _FakeAsyncOpenAI(response, calls, api_key=api_key)

    monkeypatch.setattr(OpenAIProvider, "_import_sdk", staticmethod(lambda: _FakeOpenAIModule))


@pytest.mark.asyncio
async def test_text_only_completion_returns_raw_completion_with_usage(monkeypatch):
    calls = []
    response = _FakeResponse("hello from gpt", _FakeUsage(30, 11))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = OpenAIProvider(model="gpt-5.5", api_key="test-key")
    completion = await provider._raw_complete(
        [Message(role=MessageRole.user, text="hi")], max_tokens=512, temperature=0.4
    )

    assert completion.text == "hello from gpt"
    assert completion.usage.input_tokens == 30
    assert completion.usage.output_tokens == 11
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5.5"
    assert calls[0]["max_output_tokens"] == 512
    assert calls[0]["temperature"] == 0.4


@pytest.mark.asyncio
async def test_multimodal_completion_includes_base64_image_data_uri(monkeypatch):
    calls = []
    response = _FakeResponse("I see a bird", _FakeUsage(40, 6))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = OpenAIProvider(model="gpt-5.5", api_key="test-key")
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

    sent_input = calls[0]["input"]
    assert len(sent_input) == 1
    parts = sent_input[0]["content"]
    image_parts = [p for p in parts if p["type"] == "input_image"]
    assert len(image_parts) == 1
    expected_b64 = base64.b64encode(image_bytes).decode("ascii")
    assert image_parts[0]["image_url"] == f"data:image/png;base64,{expected_b64}"


@pytest.mark.asyncio
async def test_system_messages_collected_into_instructions(monkeypatch):
    calls = []
    response = _FakeResponse("ok", _FakeUsage(1, 1))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = OpenAIProvider(model="gpt-5.5", api_key="test-key")
    await provider._raw_complete(
        [
            Message(role=MessageRole.system, text="Be terse."),
            Message(role=MessageRole.user, text="hi"),
        ],
        max_tokens=10,
        temperature=0.0,
    )

    assert calls[0]["instructions"] == "Be terse."
    assert len(calls[0]["input"]) == 1
    assert calls[0]["input"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_missing_api_key_raises_provider_unavailable_at_call_time(monkeypatch):
    monkeypatch.setattr("spark.reasoning.providers.openai.get_api_key", lambda name: None)
    # Constructing the provider with no key must NOT raise (only calling it should).
    provider = OpenAIProvider(model="gpt-5.5", api_key=None)

    with pytest.raises(ProviderUnavailableError, match="API key"):
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )


@pytest.mark.asyncio
async def test_sdk_not_installed_raises_provider_unavailable_not_import_error(monkeypatch):
    """Simulates 'openai' genuinely not being installed via the same
    sys.modules[name] = None sentinel technique test_windows_ocr.py uses.
    """
    monkeypatch.setitem(sys.modules, "openai", None)

    provider = OpenAIProvider(model="gpt-5.5", api_key="test-key")

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )
    assert "openai" in str(excinfo.value).lower()
    assert not isinstance(excinfo.value, ImportError)
