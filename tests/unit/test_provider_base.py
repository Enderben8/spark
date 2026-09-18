import pytest
from pydantic import BaseModel

from spark.reasoning.provider import (
    BaseLLMProvider,
    LLMSchemaValidationError,
    Message,
    MessageRole,
    RawCompletion,
    TokenUsage,
    _extract_json,
)


class Choice(BaseModel):
    answer: str
    confidence: float


class ScriptedProvider(BaseLLMProvider):
    """Returns a queued sequence of raw completion texts, in order —
    used to exercise BaseLLMProvider's schema-retry logic directly.
    """

    name = "scripted"
    model = "scripted-1"
    supports_vision = True

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.raw_calls: list[list[Message]] = []

    async def _raw_complete(self, messages, *, max_tokens, temperature) -> RawCompletion:
        self.raw_calls.append(messages)
        text = self._texts.pop(0)
        return RawCompletion(text=text, usage=TokenUsage(input_tokens=10, output_tokens=5))


@pytest.mark.asyncio
async def test_complete_without_schema_returns_raw_text():
    provider = ScriptedProvider(["hello there"])
    response = await provider.complete([Message(role=MessageRole.user, text="hi")])
    assert response.raw_text == "hello there"
    assert response.parsed is None
    assert response.usage.input_tokens == 10


@pytest.mark.asyncio
async def test_complete_with_schema_parses_clean_json():
    provider = ScriptedProvider(['{"answer": "b", "confidence": 0.9}'])
    response = await provider.complete([Message(role=MessageRole.user, text="q")], schema=Choice)
    assert isinstance(response.parsed, Choice)
    assert response.parsed.answer == "b"
    assert len(provider.raw_calls) == 1  # no retry needed


@pytest.mark.asyncio
async def test_complete_with_schema_strips_markdown_fence():
    provider = ScriptedProvider(['```json\n{"answer": "a", "confidence": 0.5}\n```'])
    response = await provider.complete([Message(role=MessageRole.user, text="q")], schema=Choice)
    assert response.parsed.answer == "a"


@pytest.mark.asyncio
async def test_complete_with_schema_retries_once_on_bad_json_then_succeeds():
    provider = ScriptedProvider(
        [
            "sorry, here's my answer: b (very confident)",  # invalid on first try
            '{"answer": "b", "confidence": 0.8}',  # valid on retry
        ]
    )
    response = await provider.complete([Message(role=MessageRole.user, text="q")], schema=Choice)
    assert response.parsed.answer == "b"
    assert len(provider.raw_calls) == 2
    # Usage from both calls must be summed, not just the last one.
    assert response.usage.input_tokens == 20
    assert response.usage.output_tokens == 10


@pytest.mark.asyncio
async def test_complete_with_schema_raises_after_one_failed_retry():
    provider = ScriptedProvider(["not json at all", "still not json"])
    with pytest.raises(LLMSchemaValidationError):
        await provider.complete([Message(role=MessageRole.user, text="q")], schema=Choice)
    assert len(provider.raw_calls) == 2  # exactly one retry, not more


def test_extract_json_handles_plain_object():
    assert _extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_handles_fenced_block():
    raw = 'Sure!\n```json\n{"a": 1}\n```\nHope that helps.'
    assert _extract_json(raw) == '{"a": 1}'


def test_extract_json_handles_surrounding_prose_without_fence():
    raw = 'The answer is {"a": 1} as requested.'
    assert _extract_json(raw) == '{"a": 1}'


@pytest.mark.asyncio
async def test_read_image_text_uses_vision_prompt_and_returns_text():
    from PIL import Image

    provider = ScriptedProvider(["Transcribed passage text."])
    text = await provider.read_image_text(Image.new("RGB", (20, 20)))
    assert text == "Transcribed passage text."
    sent = provider.raw_calls[0][0]
    assert sent.images
    assert "verbatim" in (sent.text or "").lower()


@pytest.mark.asyncio
async def test_read_image_text_rejects_non_vision_provider():
    from PIL import Image
    from spark.reasoning.provider import ProviderUnavailableError

    provider = ScriptedProvider([])
    provider.supports_vision = False
    with pytest.raises(ProviderUnavailableError):
        await provider.read_image_text(Image.new("RGB", (10, 10)))
