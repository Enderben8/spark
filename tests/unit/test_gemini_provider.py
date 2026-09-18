"""Unit tests for GeminiProvider (spark.reasoning.providers.gemini).

Mocks the ``google-genai`` SDK boundary at the same seam the module itself
uses for lazy import (``GeminiProvider._import_sdk``), mirroring
``tests/unit/test_windows_ocr.py``'s approach of injecting a plausible fake
of the documented API surface rather than exercising the real SDK (which
would require a live API key/network call this task must not make).
"""
from __future__ import annotations

import sys

import pytest

from spark.reasoning.provider import ProviderUnavailableError
from spark.reasoning.providers.gemini import GeminiProvider


class _FakePart:
    def __init__(self, *, text=None, data=None, mime_type=None):
        self.text = text
        self.data = data
        self.mime_type = mime_type

    @staticmethod
    def from_text(*, text):
        return _FakePart(text=text)

    @staticmethod
    def from_bytes(*, data, mime_type):
        return _FakePart(data=data, mime_type=mime_type)


class _FakeContent:
    def __init__(self, *, role, parts):
        self.role = role
        self.parts = parts


class _FakeConfig:
    def __init__(self, *, temperature, max_output_tokens, system_instruction=None):
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.system_instruction = system_instruction


class _FakeUsage:
    def __init__(self, prompt_token_count, candidates_token_count):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class _FakeResponse:
    def __init__(self, text, usage):
        self.text = text
        self.usage_metadata = usage


class _FakeAsyncModels:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def generate_content(self, *, model, contents, config):
        self._calls.append({"model": model, "contents": contents, "config": config})
        return self._response


class _FakeAio:
    def __init__(self, response, calls):
        self.models = _FakeAsyncModels(response, calls)


class _FakeClient:
    def __init__(self, response, calls, api_key=None):
        self.api_key = api_key
        self.aio = _FakeAio(response, calls)


def _fake_types_module():
    ns = type("types", (), {})
    ns.Part = _FakePart
    ns.Content = _FakeContent
    ns.GenerateContentConfig = _FakeConfig
    return ns


def _install_fake_sdk(monkeypatch, *, response, calls, client_factory=None):
    types_ns = _fake_types_module()

    class _FakeGenaiModule:
        @staticmethod
        def Client(api_key=None):
            if client_factory is not None:
                return client_factory(api_key)
            return _FakeClient(response, calls, api_key=api_key)

    monkeypatch.setattr(
        GeminiProvider, "_import_sdk", staticmethod(lambda: (_FakeGenaiModule, types_ns))
    )


@pytest.mark.asyncio
async def test_text_only_completion_returns_raw_completion_with_usage(monkeypatch):
    from spark.reasoning.provider import Message, MessageRole

    calls = []
    response = _FakeResponse("hello from gemini", _FakeUsage(12, 7))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = GeminiProvider(model="gemini-2.5-flash", api_key="test-key")
    completion = await provider._raw_complete(
        [Message(role=MessageRole.user, text="hi")], max_tokens=100, temperature=0.3
    )

    assert completion.text == "hello from gemini"
    assert completion.usage.input_tokens == 12
    assert completion.usage.output_tokens == 7
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-2.5-flash"
    assert calls[0]["config"].temperature == 0.3
    assert calls[0]["config"].max_output_tokens == 100


@pytest.mark.asyncio
async def test_multimodal_completion_includes_image_bytes_in_request(monkeypatch):
    from spark.reasoning.provider import ImagePart, Message, MessageRole

    calls = []
    response = _FakeResponse("I see a cat", _FakeUsage(20, 5))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = GeminiProvider(model="gemini-2.5-flash", api_key="test-key")
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

    sent_contents = calls[0]["contents"]
    assert len(sent_contents) == 1
    parts = sent_contents[0].parts
    image_parts = [p for p in parts if p.data is not None]
    assert len(image_parts) == 1
    assert image_parts[0].data == image_bytes
    assert image_parts[0].mime_type == "image/png"


@pytest.mark.asyncio
async def test_system_messages_collected_into_system_instruction(monkeypatch):
    from spark.reasoning.provider import Message, MessageRole

    calls = []
    response = _FakeResponse("ok", _FakeUsage(1, 1))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = GeminiProvider(model="gemini-2.5-flash", api_key="test-key")
    await provider._raw_complete(
        [
            Message(role=MessageRole.system, text="Be terse."),
            Message(role=MessageRole.user, text="hi"),
        ],
        max_tokens=10,
        temperature=0.0,
    )

    assert calls[0]["config"].system_instruction == "Be terse."
    # The system message must not appear as a turn in `contents`.
    assert len(calls[0]["contents"]) == 1
    assert calls[0]["contents"][0].role == "user"


@pytest.mark.asyncio
async def test_missing_api_key_raises_provider_unavailable_at_call_time(monkeypatch):
    from spark.reasoning.provider import Message, MessageRole

    monkeypatch.setattr("spark.reasoning.providers.gemini.get_api_key", lambda name: None)
    # Constructing the provider with no key must NOT raise (only calling it should).
    provider = GeminiProvider(model="gemini-2.5-flash", api_key=None)

    with pytest.raises(ProviderUnavailableError, match="API key"):
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )


@pytest.mark.asyncio
async def test_sdk_not_installed_raises_provider_unavailable_not_import_error(monkeypatch):
    """Simulates 'google-genai' genuinely not being installed by making the
    real `from google import genai` import statement inside
    GeminiProvider._import_sdk fail, the same technique
    test_windows_ocr.py uses for 'winsdk': set the module to None in
    sys.modules (Python's own sentinel for "this import must fail") and
    clear it as a cached attribute on the parent package.
    """
    from spark.reasoning.provider import Message, MessageRole

    import google  # the real namespace package, already imported by this test file

    monkeypatch.setitem(sys.modules, "google.genai", None)
    if hasattr(google, "genai"):
        monkeypatch.delattr(google, "genai")

    provider = GeminiProvider(model="gemini-2.5-flash", api_key="test-key")

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )
    assert "google-genai" in str(excinfo.value)
    assert not isinstance(excinfo.value, ImportError)
