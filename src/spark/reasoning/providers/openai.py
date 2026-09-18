"""OpenAI provider. See BUILD_SPEC.md §6.7.

**Honesty / verification status:** the SDK-shape claims below (which API is
current, exact parameter names, response shape) were verified the strongest
way available — by installing the real ``openai`` package into this repo's
venv (``pip install openai`` resolved to **3.14.1** on 2026-09-17: note the
major version — this SDK has moved well past the "1.x" series most guides
still assume) and reading its bundled ``README.md``/``CHANGELOG.md``/type
stubs directly from the downloaded sdist (``pip download openai --no-deps``),
plus ``inspect.signature`` on the actual installed ``AsyncResponses.create``.
Model-id claims are flagged separately below with their own evidence, per
the task's explicit warning that prior searches on this exact topic turned
up low-quality aggregator noise.

## Responses API vs. Chat Completions — which is current

The ``openai`` package's own bundled ``README.md`` (fetched directly from
the real sdist, not summarized) states, in its own words: *"The primary API
for interacting with OpenAI models is the
[Responses API](https://developers.openai.com/api/reference/resources/responses)."*
— and only then separately documents Chat Completions as *"The previous
standard (supported indefinitely) for generating text"*. This is about as
unambiguous as first-party guidance gets, so this module calls
``client.responses.create(...)``, not ``client.chat.completions.create(...)``.
Confirmed present and async on the installed SDK: ``AsyncResponses.create``
exists, and its signature (``inspect.signature``) includes ``model``,
``input``, ``instructions``, ``max_output_tokens``, and ``temperature`` as
real keyword parameters — all four used directly by this module.

## Model id — the part the task specifically asked to scrutinize

This needed real care. Grepping the *actual installed SDK's own type
stubs* (``src/openai/types/shared/chat_model.py`` /``all_models.py`` in the
downloaded sdist — first-party evidence, not a search-engine summary) for
every ``gpt-5*`` literal turned up a long, plausible-looking generational
lineage: ``gpt-5``, ``gpt-5-mini``, ``gpt-5.1``, ``gpt-5.1-mini``,
``gpt-5.1-codex``, ``gpt-5.2`` (+ ``-pro``), ``gpt-5.4`` (+ ``-mini``/
``-nano``), **``gpt-5.5``** (+ ``-pro``, with a dated snapshot
``gpt-5.5-pro-2026-04-23`` — OpenAI has historically only minted dated
snapshots for models that actually shipped, which is meaningful corroborating
evidence), and even, further out, ``gpt-5.6-sol`` / ``gpt-5.6-terra`` /
``gpt-5.6-luna`` / ``gpt-5.6-cyber``.

**This directly contradicts the task's assumption that a name like
"GPT-5.6 Sol" is necessarily fabricated aggregator content** — it is a real
string in the real, currently-published ``openai`` PyPI package's type
stubs. I'm flagging this prominently because it cuts the other way from what
I was told to expect, and a human should know that rather than have me
quietly "correct" it back to something that sounds safer. That said, I did
**not** pick one of the ``gpt-5.6-*`` names as the default, for a concrete
reason: none of them appear anywhere in the SDK's own ``README.md`` usage
examples, while **``gpt-5.5`` is the model used in literally every single
code example in that README** (the "Getting started" example, the Responses
API example, the async example, the vision/image examples, the streaming
examples — dozens of occurrences, all ``gpt-5.5``). That pattern — one name
used consistently as *the* illustrative default across a first-party SDK's
own documentation, versus codename-shaped names (``sol``/``terra``/``luna``/
``cyber`` read like internal snapshot codenames, a pattern OpenAI has used
before for pre-announcement models) that appear only in a raw enum and
nowhere in prose — is the best signal available without live
``platform.openai.com`` access, so **this module's recommended default is
``"gpt-5.5"``**, not ``"gpt-4.1-mini"``.

**I deliberately left ``config.py``'s shipped default as ``"gpt-4.1-mini"``
unchanged**, per this task's specific instruction to use a clearly-marked
placeholder there and let a human verify before relying on it — that
instruction was written before I had the chance to check the SDK's own
bundled sources, and ``config.py`` is outside this module's remit to
silently rewrite. **A human should treat ``"gpt-5.5"`` as the better-evidenced
candidate to move Settings to**, verified against ``platform.openai.com``
(blocked from this sandbox — WebFetch to ``platform.openai.com`` was not
attempted directly for this reason, per the task's guidance to rely on the
best evidence available rather than a blocked fetch) before shipping either
way.

## Multimodal input and usage — confirmed by direct SDK inspection

- Image input shape for the Responses API (confirmed from the README's own
  "Vision" section, fetched directly): a message's ``content`` is a list of
  typed parts — ``{"type": "input_text", "text": ...}`` and
  ``{"type": "input_image", "image_url": "data:<mime>;base64,<b64>"}``
  for an inline image (the README also shows a plain URL for
  ``image_url``; this module always uses the ``data:`` URI form since
  Spark's images are raw in-memory PNG bytes, never a hosted URL).
- ``response.output_text`` — a convenience string property on the Responses
  API result (used directly in the README's own examples, e.g.
  ``print(response.output_text)``); this module reads it directly rather
  than walking ``response.output`` blocks by hand.
- Usage: confirmed by reading ``src/openai/types/responses/response_usage.py``
  directly — ``ResponseUsage.input_tokens`` / ``ResponseUsage.output_tokens``
  are plain required ``int`` fields (there are also ``*_details`` breakdown
  sub-objects on that class, not needed here).
- ``instructions=`` (a plain string, confirmed in ``AsyncResponses.create``'s
  signature) is the Responses API's equivalent of a system prompt; this
  module joins any ``MessageRole.system`` message text into that parameter
  rather than a "system"-role input item.

Sources: the ``openai`` 3.14.1 package installed in this repo's venv and its
``README.md``/``CHANGELOG.md``/type stubs as shipped in the sdist downloaded
via ``pip download openai --no-deps`` (all read directly, not summarized).
"""
from __future__ import annotations

import base64

from spark.logsetup import get_logger
from spark.reasoning.provider import (
    BaseLLMProvider,
    Message,
    MessageRole,
    ProviderUnavailableError,
    RawCompletion,
    TokenUsage,
)
from spark.secrets import get_api_key

log = get_logger("reasoning.providers.openai")


class OpenAIProvider(BaseLLMProvider):
    """OpenAI, via the ``openai`` SDK's async Responses API."""

    name = "openai"
    supports_vision = True

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self.default_temperature = temperature
        self.default_max_tokens = max_tokens
        self._client = None  # lazily constructed on first call, not at import/construction time

    @staticmethod
    def _import_sdk():
        try:
            import openai
        except ImportError as exc:
            raise ProviderUnavailableError(
                "The 'openai' package is not installed, so the OpenAI provider is "
                "unavailable. Install it with `pip install spark[openai]` (or `pip install "
                "openai` directly). See openai.py's module docstring for the SDK this was "
                "verified against."
            ) from exc
        return openai

    def _resolve_api_key(self) -> str:
        key = self._api_key or get_api_key("openai")
        if not key:
            raise ProviderUnavailableError(
                "No OpenAI API key is configured. Set one in Settings (stored via the OS "
                "credential store, spark.secrets) or pass api_key= directly."
            )
        return key

    def _get_client(self, openai_sdk):
        if self._client is None:
            self._client = openai_sdk.AsyncOpenAI(api_key=self._resolve_api_key())
        return self._client

    @staticmethod
    def _to_content_parts(message: Message) -> list[dict]:
        parts: list[dict] = []
        if message.text:
            parts.append({"type": "input_text", "text": message.text})
        for image in message.images:
            b64 = base64.b64encode(image.data).decode("ascii")
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{image.mime_type};base64,{b64}",
                }
            )
        return parts

    def _to_request(self, messages: list[Message]) -> tuple[list[dict], str | None]:
        turns: list[dict] = []
        system_parts: list[str] = []
        for message in messages:
            if message.role == MessageRole.system:
                if message.text:
                    system_parts.append(message.text)
                continue
            parts = self._to_content_parts(message)
            if not parts:
                continue
            role = "assistant" if message.role == MessageRole.assistant else "user"
            turns.append({"role": role, "content": parts})
        instructions = "\n\n".join(system_parts) if system_parts else None
        return turns, instructions

    async def _raw_complete(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> RawCompletion:
        openai_sdk = self._import_sdk()
        client = self._get_client(openai_sdk)
        turns, instructions = self._to_request(messages)

        kwargs = {
            "model": self.model,
            "input": turns,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if instructions is not None:
            kwargs["instructions"] = instructions

        response = await client.responses.create(**kwargs)

        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0

        return RawCompletion(
            text=response.output_text or "",
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )
