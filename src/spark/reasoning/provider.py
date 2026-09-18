"""The pluggable LLM provider layer. See BUILD_SPEC.md §6.7.

Every provider (Gemini, Anthropic, OpenAI, Ollama, and the test-only Stub)
implements the same small async interface. Structured output is mandatory
for every decision the model makes (BUILD_SPEC §6.7): a provider that can't
enforce a JSON schema natively still gets one enforced here, with exactly
one retry (schema + the previous validation error) before giving up loudly.

Providers subclass :class:`BaseLLMProvider` and implement only
``_raw_complete`` — the schema-retry loop, JSON extraction, and the
image-transcription convenience method used by the OCR escalation engine
(perception/ocr/vision.py) all live here, once, rather than being
reimplemented per provider.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from spark.config import ProviderSettings

from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from spark.logsetup import get_logger
from spark.perception.ocr.vision import build_read_image_text_prompt

log = get_logger("reasoning.provider")


class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


class ImagePart(BaseModel):
    data: bytes
    mime_type: str = "image/png"


class Message(BaseModel):
    role: MessageRole
    text: str | None = None
    images: list[ImagePart] = Field(default_factory=list)


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class LLMResponse(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    raw_text: str
    parsed: BaseModel | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str
    provider: str


class LLMSchemaValidationError(RuntimeError):
    """Raised when a provider fails to produce schema-valid JSON even after
    the one permitted retry (BUILD_SPEC §6.7). Callers must treat this as a
    hard failure of that step, not silently fall back to parsing prose.
    """


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider's SDK is not installed or its API key is
    missing — distinguished from a schema failure so callers/GUI can show
    "install X" / "set your API key" rather than a generic error.
    """


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    supports_vision: bool

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse: ...

    async def read_image_text(self, image: Image.Image) -> str: ...


def _extract_json(raw_text: str) -> str:
    """Best-effort extraction of a JSON object/array from a model response
    that may be wrapped in a ```json ... ``` fence or preceded/followed by
    prose the model added despite instructions not to.
    """
    text = raw_text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    # Fall back to the first balanced-looking {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
    return text


@dataclass
class RawCompletion:
    text: str
    usage: TokenUsage


class BaseLLMProvider:
    """Shared schema-enforcement, JSON-extraction, and image-OCR logic.
    Subclasses implement only ``_raw_complete`` (one call to their SDK) and
    set ``name``, ``model``, and ``supports_vision``.
    """

    name: str = "base"
    model: str = ""
    supports_vision: bool = False

    async def _raw_complete(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> RawCompletion:
        raise NotImplementedError

    async def complete(
        self,
        messages: list[Message],
        *,
        schema: type[BaseModel] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResponse:
        working = list(messages)
        if schema is not None:
            working = working + [
                Message(
                    role=MessageRole.user,
                    text=(
                        "Respond with ONLY a single JSON object matching this schema, "
                        "no prose before or after, no markdown code fence:\n"
                        f"{json.dumps(schema.model_json_schema())}"
                    ),
                )
            ]

        completion = await self._raw_complete(working, max_tokens=max_tokens, temperature=temperature)
        usage = completion.usage
        raw_text = completion.text
        parsed = None
        last_error = None

        if schema is not None:
            parsed, last_error = self._try_parse(raw_text, schema)
            if parsed is None:
                log.info(
                    "%s: response failed schema validation for %s, retrying once (%s)",
                    self.name,
                    schema.__name__,
                    last_error,
                )
                retry_messages = working + [
                    Message(role=MessageRole.assistant, text=raw_text),
                    Message(
                        role=MessageRole.user,
                        text=(
                            "That response was not valid JSON matching the required schema. "
                            f"Validation error: {last_error}. Reply again with ONLY the "
                            "corrected JSON object, nothing else."
                        ),
                    ),
                ]
                retry_completion = await self._raw_complete(
                    retry_messages, max_tokens=max_tokens, temperature=temperature
                )
                usage = usage + retry_completion.usage
                raw_text = retry_completion.text
                parsed, last_error = self._try_parse(raw_text, schema)
                if parsed is None:
                    raise LLMSchemaValidationError(
                        f"{self.name} failed to produce valid {schema.__name__} JSON "
                        f"after one retry: {last_error}"
                    )

        return LLMResponse(raw_text=raw_text, parsed=parsed, usage=usage, model=self.model, provider=self.name)

    @staticmethod
    def _try_parse(raw_text: str, schema: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
        json_str = _extract_json(raw_text)
        try:
            return schema.model_validate_json(json_str), None
        except (ValidationError, json.JSONDecodeError) as exc:
            return None, str(exc)

    async def read_image_text(self, image: Image.Image) -> str:
        """Used by perception/ocr/vision.py's ``VisionOcrEngine`` as the
        injected ``read_image_text`` callable (BUILD_SPEC §6.4).
        """
        if not self.supports_vision:
            raise ProviderUnavailableError(f"Provider {self.name} does not support image input")
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
        message = Message(
            role=MessageRole.user,
            text=build_read_image_text_prompt(),
            images=[ImagePart(data=buf.getvalue())],
        )
        response = await self.complete([message], schema=None, max_tokens=4096, temperature=0.0)
        return response.raw_text


def build_provider(name: str, settings: "ProviderSettings") -> BaseLLMProvider:
    """Construct a concrete provider from a config name and its settings.

    Mirrors ``perception/ocr/registry.py``'s ``build_engine`` for the
    reasoning layer (BUILD_SPEC §6.7): maps the provider-name strings used by
    ``spark.config`` (``AppSettings.active_provider`` / ``PROVIDER_NAMES``)
    onto a concrete :class:`BaseLLMProvider` subclass.

    Construction here is cheap and offline on purpose: it never touches the
    network or the OS keyring itself. Each provider resolves its API key
    (via :mod:`spark.secrets`) and imports its SDK lazily, inside
    ``_raw_complete``, only when an actual completion is requested — see
    each provider module's docstring for why (so that constructing a
    provider, e.g. to inspect ``.name``/``.supports_vision``, never requires
    a key or an installed SDK, only calling it does).

    The concrete provider modules are imported lazily, inside this function
    rather than at module load time, specifically to avoid a circular
    import: every provider module imports ``BaseLLMProvider`` and friends
    from *this* module.
    """
    if name == "gemini":
        from spark.reasoning.providers.gemini import GeminiProvider

        return GeminiProvider(
            model=settings.model, temperature=settings.temperature, max_tokens=settings.max_tokens
        )
    if name == "anthropic":
        from spark.reasoning.providers.anthropic import AnthropicProvider

        kwargs = {"temperature": settings.temperature, "max_tokens": settings.max_tokens}
        if settings.model:
            kwargs["model"] = settings.model
        return AnthropicProvider(**kwargs)
    if name == "openai":
        from spark.reasoning.providers.openai import OpenAIProvider

        return OpenAIProvider(
            model=settings.model, temperature=settings.temperature, max_tokens=settings.max_tokens
        )
    if name == "ollama":
        from spark.reasoning.providers.ollama import OllamaProvider

        kwargs = {"model": settings.model, "temperature": settings.temperature, "max_tokens": settings.max_tokens}
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        return OllamaProvider(**kwargs)
    if name == "stub":
        from spark.reasoning.providers.stub import StubProvider

        return StubProvider()
    raise ValueError(f"Unknown provider: {name!r}")
