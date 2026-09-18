import pytest
from pydantic import BaseModel

from spark.config import ProviderSettings
from spark.reasoning.budget import BudgetTracker, BudgetTrackingProvider
from spark.reasoning.provider import TokenUsage
from spark.reasoning.providers.stub import StubProvider


class Choice(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_tracks_call_count_and_usage_across_calls():
    inner = StubProvider()
    inner.queue_response(Choice, Choice(answer="a"))
    inner.queue_response(Choice, Choice(answer="b"))
    wrapped = BudgetTrackingProvider(inner)

    await wrapped.complete([], schema=Choice)
    await wrapped.complete([], schema=Choice)

    assert wrapped.tracker.calls_made == 2
    assert wrapped.tracker.total_usage.input_tokens == 2  # StubProvider reports 1 in / 1 out per call
    assert wrapped.tracker.total_usage.output_tokens == 2


def test_estimate_cost_is_none_without_configured_rates():
    tracker = BudgetTracker()
    tracker.record(TokenUsage(input_tokens=1000, output_tokens=1000))
    settings = ProviderSettings()  # rates default to None
    assert tracker.estimate_cost_usd(settings) is None


def test_estimate_cost_with_configured_rates():
    tracker = BudgetTracker()
    tracker.record(TokenUsage(input_tokens=1000, output_tokens=2000))
    settings = ProviderSettings(cost_per_1k_input_tokens=0.01, cost_per_1k_output_tokens=0.02)
    cost = tracker.estimate_cost_usd(settings)
    assert cost == pytest.approx(0.01 * 1 + 0.02 * 2)


@pytest.mark.asyncio
async def test_wrapped_provider_exposes_name_and_vision_flag():
    inner = StubProvider()
    wrapped = BudgetTrackingProvider(inner)
    assert wrapped.name == inner.name
    assert wrapped.supports_vision == inner.supports_vision


@pytest.mark.asyncio
async def test_wrapped_provider_passes_through_image_text():
    inner = StubProvider()
    inner.set_image_text("hello")
    wrapped = BudgetTrackingProvider(inner)
    from PIL import Image

    text = await wrapped.read_image_text(Image.new("RGB", (2, 2)))
    assert text == "hello"
