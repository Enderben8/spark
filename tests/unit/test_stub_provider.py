import pytest
from pydantic import BaseModel

from spark.reasoning.providers.stub import StubProvider


class Choice(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_queued_responses_pop_in_order():
    provider = StubProvider()
    provider.queue_response(Choice, Choice(answer="first"))
    provider.queue_response(Choice, Choice(answer="second"))

    r1 = await provider.complete([], schema=Choice)
    r2 = await provider.complete([], schema=Choice)
    assert r1.parsed.answer == "first"
    assert r2.parsed.answer == "second"


@pytest.mark.asyncio
async def test_responder_sees_the_actual_messages():
    provider = StubProvider()

    def responder(messages):
        return Choice(answer="reacted")

    provider.set_responder(Choice, responder)
    response = await provider.complete([], schema=Choice)
    assert response.parsed.answer == "reacted"


@pytest.mark.asyncio
async def test_unscripted_schema_raises_assertion_not_silent_none():
    provider = StubProvider()
    with pytest.raises(AssertionError, match="Choice"):
        await provider.complete([], schema=Choice)


@pytest.mark.asyncio
async def test_image_text_is_settable():
    provider = StubProvider()
    provider.set_image_text("known passage text")
    from PIL import Image

    text = await provider.read_image_text(Image.new("RGB", (5, 5)))
    assert text == "known passage text"
