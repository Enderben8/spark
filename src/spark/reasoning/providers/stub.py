"""A scripted, deterministic provider used by tests (BUILD_SPEC.md §11.2:
"Integration (no model): drive the fixture site with a stub provider that
returns canned actions. This must pass in CI without any API key.") and
usable as a manual "dry run" mode in the GUI later.

Not part of the shipped provider choices in Settings — it exists purely so
the rest of the pipeline (orchestrator, answering, scoring) can be tested
end-to-end without a live API key or network access.
"""
from __future__ import annotations

from collections import deque
from typing import Callable

from pydantic import BaseModel

from spark.reasoning.provider import BaseLLMProvider, LLMResponse, Message, TokenUsage, RawCompletion


class StubProvider(BaseLLMProvider):
    """Returns pre-scripted responses in order, or computes one via a
    caller-supplied function keyed by the requested schema. Two ways to
    script it, pick whichever fits the test:

    * ``queue_response(schema, model_instance)`` — pop one canned answer per
      call to that schema, in the order queued.
    * ``set_responder(schema, fn)`` — call ``fn(messages) -> BaseModel`` every
      time that schema is requested, for tests that need to react to what
      was actually asked (e.g. answering different questions correctly).

    Either way, ``complete()`` still runs through the same JSON-shaped path
    as a real provider (round-tripping through ``model_dump_json`` and back)
    so schema-validation bugs surface in tests using the stub, not only
    against a live model.
    """

    name = "stub"
    model = "stub-1"
    supports_vision = True  # tests can also exercise the vision OCR path

    def __init__(self) -> None:
        self._queues: dict[type[BaseModel], deque[BaseModel]] = {}
        self._responders: dict[type[BaseModel], Callable[[list[Message]], BaseModel]] = {}
        self._default_image_text = ""
        self.calls: list[list[Message]] = []

    def queue_response(self, schema: type[BaseModel], instance: BaseModel) -> None:
        self._queues.setdefault(schema, deque()).append(instance)

    def set_responder(self, schema: type[BaseModel], fn: Callable[[list[Message]], BaseModel]) -> None:
        self._responders[schema] = fn

    def set_image_text(self, text: str) -> None:
        self._default_image_text = text

    async def _raw_complete(self, messages: list[Message], *, max_tokens: int, temperature: float) -> RawCompletion:
        self.calls.append(messages)
        # No schema context is available at this layer (BaseLLMProvider
        # appends the schema instruction as a plain message) — StubProvider
        # instead overrides `complete()` entirely so it can key off the
        # actual schema type. This method exists only to satisfy the
        # abstract contract; it should not be reached in practice.
        return RawCompletion(text="{}", usage=TokenUsage(input_tokens=0, output_tokens=0))

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(messages)
        if schema is None:
            return LLMResponse(raw_text="", usage=TokenUsage(), model=self.model, provider=self.name)

        instance: BaseModel | None = None
        if schema in self._responders:
            instance = self._responders[schema](messages)
        elif schema in self._queues and self._queues[schema]:
            instance = self._queues[schema].popleft()

        if instance is None:
            raise AssertionError(
                f"StubProvider was asked for {schema.__name__} but has no queued response "
                f"or responder for it — the test didn't script this step."
            )

        return LLMResponse(
            raw_text=instance.model_dump_json(),
            parsed=instance,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            model=self.model,
            provider=self.name,
        )

    async def read_image_text(self, image) -> str:
        return self._default_image_text
