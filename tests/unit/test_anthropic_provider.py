"""Unit tests for AnthropicProvider (spark.reasoning.providers.anthropic).

Mocks the ``anthropic`` SDK boundary at the same seam the module itself
uses for lazy import (``AnthropicProvider._import_sdk``'s ``import
anthropic`` statement), by making the real import fail via the
``sys.modules[name] = None`` sentinel technique used in
``tests/unit/test_windows_ocr.py`` for 'winsdk not installed', and by
patching ``AsyncAnthropic`` construction for the success-path tests.
"""
from __future__ import annotations

import sys

import pytest

from spark.reasoning.provider import ImagePart, Message, MessageRole, ProviderUnavailableError
from spark.reasoning.providers.anthropic import AnthropicProvider


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeMessage:
    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


class _FakeMessages:
    def __init__(self, response, calls):
        self._response = response
        self._calls = calls

    async def create(self, **kwargs):
        self._calls.append(kwargs)
        return self._response


class _FakeAsyncAnthropic:
    def __init__(self, response, calls, api_key=None):
        self.api_key = api_key
        self.messages = _FakeMessages(response, calls)


def _install_fake_sdk(monkeypatch, *, response, calls):
    class _FakeAnthropicModule:
        @staticmethod
        def AsyncAnthropic(api_key=None):
            return _FakeAsyncAnthropic(response, calls, api_key=api_key)

    monkeypatch.setattr(AnthropicProvider, "_import_sdk", staticmethod(lambda: _FakeAnthropicModule))


@pytest.mark.asyncio
async def test_text_only_completion_returns_raw_completion_with_usage(monkeypatch):
    calls = []
    response = _FakeMessage([_FakeTextBlock("hello from claude")], _FakeUsage(15, 9))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = AnthropicProvider(model="claude-sonnet-5", api_key="test-key")
    completion = await provider._raw_complete(
        [Message(role=MessageRole.user, text="hi")], max_tokens=256, temperature=0.5
    )

    assert completion.text == "hello from claude"
    assert completion.usage.input_tokens == 15
    assert completion.usage.output_tokens == 9
    assert len(calls) == 1
    assert calls[0]["model"] == "claude-sonnet-5"
    assert calls[0]["max_tokens"] == 256
    # temperature is intentionally NOT forwarded (see module docstring) —
    # the current Claude Messages API this SDK targets has no such param.
    assert "temperature" not in calls[0]


@pytest.mark.asyncio
async def test_multimodal_completion_includes_base64_image_in_request(monkeypatch):
    calls = []
    response = _FakeMessage([_FakeTextBlock("I see a dog")], _FakeUsage(20, 4))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = AnthropicProvider(model="claude-sonnet-5", api_key="test-key")
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

    sent_messages = calls[0]["messages"]
    assert len(sent_messages) == 1
    blocks = sent_messages[0]["content"]
    image_blocks = [b for b in blocks if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"
    assert image_blocks[0]["source"]["type"] == "base64"
    # The data sent over the wire must actually decode back to the original bytes.
    import base64

    assert base64.b64decode(image_blocks[0]["source"]["data"]) == image_bytes


@pytest.mark.asyncio
async def test_system_messages_collected_into_system_param(monkeypatch):
    calls = []
    response = _FakeMessage([_FakeTextBlock("ok")], _FakeUsage(1, 1))
    _install_fake_sdk(monkeypatch, response=response, calls=calls)

    provider = AnthropicProvider(model="claude-sonnet-5", api_key="test-key")
    await provider._raw_complete(
        [
            Message(role=MessageRole.system, text="Be terse."),
            Message(role=MessageRole.user, text="hi"),
        ],
        max_tokens=10,
        temperature=0.0,
    )

    assert calls[0]["system"] == "Be terse."
    assert len(calls[0]["messages"]) == 1
    assert calls[0]["messages"][0]["role"] == "user"


@pytest.mark.asyncio
async def test_missing_api_key_raises_provider_unavailable_at_call_time(monkeypatch):
    monkeypatch.setattr("spark.reasoning.providers.anthropic.get_api_key", lambda name: None)
    # Constructing the provider with no key must NOT raise (only calling it should).
    provider = AnthropicProvider(model="claude-sonnet-5", api_key=None)

    with pytest.raises(ProviderUnavailableError, match="API key"):
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )


@pytest.mark.asyncio
async def test_sdk_not_installed_raises_provider_unavailable_not_import_error(monkeypatch):
    """Simulates 'anthropic' genuinely not being installed via the same
    sys.modules[name] = None sentinel technique test_windows_ocr.py uses.
    """
    monkeypatch.setitem(sys.modules, "anthropic", None)

    provider = AnthropicProvider(model="claude-sonnet-5", api_key="test-key")

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await provider._raw_complete(
            [Message(role=MessageRole.user, text="hi")], max_tokens=10, temperature=0.2
        )
    assert "anthropic" in str(excinfo.value).lower()
    assert not isinstance(excinfo.value, ImportError)
