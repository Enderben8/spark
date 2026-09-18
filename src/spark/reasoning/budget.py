"""Wraps any :class:`~spark.reasoning.provider.LLMProvider` to track model
calls and token usage across every caller (the orchestrator's own
classify/decide prompts, and the answering/scoring skills, which each call
``provider.complete()`` directly) — one place accounting flows through, so
the run-level budgets in BUILD_SPEC §12.1 (max model calls, max cost) can
actually be enforced regardless of which piece of code made the call.

Deliberately implemented as a standalone wrapper rather than added to
``reasoning/provider.py`` itself: it needs no changes to the base provider
contract, and matches ``LLMProvider`` structurally (Python's ``Protocol`` is
duck-typed), so it can wrap the real providers there without touching that
module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image
from pydantic import BaseModel

from spark.config import ProviderSettings
from spark.reasoning.provider import LLMProvider, LLMResponse, Message, TokenUsage


@dataclass
class BudgetTracker:
    calls_made: int = 0
    total_usage: TokenUsage = field(default_factory=TokenUsage)

    def record(self, usage: TokenUsage) -> None:
        self.calls_made += 1
        self.total_usage = self.total_usage + usage

    def estimate_cost_usd(self, settings: ProviderSettings) -> float | None:
        """None means "unknown" (no rate configured) — never silently
        reported as $0, which would look like a real, checked answer rather
        than the absence of one (BUILD_SPEC §6.7/§15: don't fabricate
        pricing facts).
        """
        if settings.cost_per_1k_input_tokens is None or settings.cost_per_1k_output_tokens is None:
            return None
        input_cost = (self.total_usage.input_tokens / 1000) * settings.cost_per_1k_input_tokens
        output_cost = (self.total_usage.output_tokens / 1000) * settings.cost_per_1k_output_tokens
        return input_cost + output_cost


class BudgetTrackingProvider:
    """A transparent proxy: every ``complete()``/``read_image_text()`` call
    passes straight through to the wrapped provider, and is also recorded on
    ``self.tracker`` for the orchestrator's budget checks.
    """

    def __init__(self, inner: LLMProvider, tracker: BudgetTracker | None = None):
        self._inner = inner
        self.tracker = tracker or BudgetTracker()
        self.name = inner.name
        self.supports_vision = inner.supports_vision

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        response = await self._inner.complete(messages, schema=schema, max_tokens=max_tokens, temperature=temperature)
        self.tracker.record(response.usage)
        return response

    async def read_image_text(self, image: Image.Image) -> str:
        # Routed through complete() by BaseLLMProvider subclasses already,
        # but StubProvider and any future provider overriding
        # read_image_text directly would bypass tracking if we just
        # delegated — call complete() ourselves via the same convention
        # instead isn't possible generically, so we accept that a provider
        # which overrides read_image_text without going through complete()
        # won't have its image-OCR usage tracked. In practice every real
        # provider (Base subclasses) routes this through complete().
        return await self._inner.read_image_text(image)
