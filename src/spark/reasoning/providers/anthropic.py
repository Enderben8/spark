"""Anthropic (Claude) provider. See BUILD_SPEC.md §6.7.

**Honesty / verification status:** unlike ``gemini.py`` and ``openai.py``,
almost nothing here needed a live web fetch, because the strongest possible
evidence was available locally: the real ``anthropic`` SDK, installed into
this repo's venv (``pip install anthropic`` resolved to **1.6.0** on
2026-09-17), was inspected directly — ``inspect.signature`` on
``AsyncMessages.create``, ``grep`` across the installed package's
``types/`` source, and reading the exact ``README.md``/``CHANGELOG.md``
shipped inside the downloaded sdist (``pip download anthropic --no-deps``,
then untarred and read directly, not summarized). Where this file states a
parameter exists or doesn't, that is from direct inspection of that
installed code, not memory or a doc summary.

## A genuinely surprising, load-bearing finding: there is no ``temperature`` parameter

I grepped every non-beta and beta type module shipped in ``anthropic``
1.6.0 for the literal string ``"temperature"`` and got **zero matches** —
not in ``messages.create``'s signature, not in ``MessageCreateParamsBase``,
not in ``OutputConfigParam`` (the closest-looking replacement — it only has
``effort`` (a `low|medium|high|xhigh|max` literal) and ``format``). The
package's own README (fetched from the real sdist, not a doc site) also
never mentions ``temperature``, and neither does its full
``CHANGELOG.md`` back through the ``0.x`` -> ``1.0.0`` migration (the
1.0.0 entry only calls out an ``httpx``->``httpx2`` client swap as the
breaking change, no mention of sampling parameters). **This means the
current Claude Messages API this SDK talks to has no per-request sampling
temperature at all** — as of this generation, the closest analogous control
is ``output_config.effort`` (a qualitative reasoning-effort level, not a
0-2 float), which is not the same knob and this module does not attempt to
fake an equivalence between the two. Accordingly, ``_raw_complete`` here
**does not forward Spark's ``temperature`` argument to the SDK at all** — it
accepts it (required by the shared ``BaseLLMProvider``/``_raw_complete``
contract) and silently ignores it rather than raising or guessing a mapping.
If a future ``anthropic`` release reintroduces a temperature-like knob, or a
human wants to map ``temperature`` to ``output_config.effort`` heuristically,
that would be a deliberate, documented follow-up — not something to infer
silently here.

## Model id

The requesting task explicitly says to use ``"claude-sonnet-5"`` here
because it is independently, unambiguously verifiable: it is the identity
of the model actually running this task, not something to look up. This is
also corroborated by the anthropic SDK's own bundled ``README.md`` (the real
package README, fetched directly from the downloaded sdist), whose
"Getting started" example uses ``model="claude-opus-5"`` — i.e. the
"claude-<tier>-5" naming scheme (opus/sonnet/haiku + a bare major version
number, no date suffix) is exactly what Anthropic's own SDK documentation
uses as of this SDK version, which is consistent with — though not a
substitute for — the direct-identity evidence for ``claude-sonnet-5``
itself. **This is only a shipped default**, though: BUILD_SPEC §6.7 and
``config.py``'s comment block are explicit that Settings, not this file,
controls the model id actually used, and a human should still confirm the
current lineup at deploy time (Anthropic's own model list lived at
https://docs.anthropic.com/en/docs/about-claude/models at last check, but
note the SDK's own README links to ``platform.claude.com`` now — Anthropic
appears to have moved/renamed its docs domain since this SDK's earlier
releases, another small "don't trust a stale URL" data point worth a
human's attention).

## API key, message shape, images, usage — all confirmed by direct SDK inspection

- ``anthropic.AsyncAnthropic(api_key=...)`` — confirmed via
  ``inspect.signature``.
- ``await client.messages.create(model=, max_tokens=, system=, messages=[...])``
  — ``system`` is still a plain top-level ``Union[str, Iterable[TextBlockParam]]``
  parameter (confirmed present in ``message_create_params.py``), separate
  from the ``messages`` list, exactly as in older SDK generations — so this
  module collects any ``MessageRole.system`` message text into that
  parameter rather than needing the (also technically-permitted, per
  ``MessageParam.role: Literal["user", "assistant", "system"]``) "system"
  message role, which keeps this code compatible with older ``anthropic``
  releases too, not just 1.x.
- Content blocks: a message's ``content`` is a plain string, or a list of
  blocks. A text block is ``{"type": "text", "text": ...}``
  (``TextBlockParam``). An image block is ``{"type": "image", "source":
  {"type": "base64", "media_type": ..., "data": ...}}``
  (``ImageBlockParam`` / ``Base64ImageSourceParam``, both confirmed by
  reading the installed types directly). ``media_type`` is a closed literal
  of exactly ``image/jpeg | image/png | image/gif | image/webp`` — this
  module passes ``ImagePart.mime_type`` straight through and relies on the
  API to reject anything else (Spark only ever produces PNG screenshots in
  practice, per ``BaseLLMProvider.read_image_text``). ``data`` accepts
  either a raw base64 *string* or a file-like/bytes object the SDK
  auto-encodes (``Base64FileInput = Union[IO[bytes], PathLike]`` — confirmed
  in ``anthropic/_types.py``); this module base64-encodes explicitly
  (``base64.b64encode(...).decode("ascii")``) rather than relying on that
  auto-encoding path, to keep the wire payload this module sends fully
  explicit and testable.
- Response usage: ``message.usage.input_tokens`` / ``message.usage.output_tokens``
  (both required ``int`` fields on ``Usage``, confirmed by reading
  ``types/usage.py`` directly — not the various optional cache/server-tool
  breakdown fields also on that class, which this module does not need).
  ``message.content`` is a list of content blocks; this module joins the
  ``.text`` of every block with ``type == "text"`` (a response can only
  reasonably contain text blocks for a non-tool-use request, but this is
  written defensively in case a future default response ever includes e.g.
  a thinking block first).

Sources: the ``anthropic`` 1.6.0 package installed in this repo's venv
(``site-packages/anthropic/...``, inspected directly), its ``README.md`` and
``CHANGELOG.md`` as shipped in the sdist downloaded via
``pip download anthropic --no-deps``, and this session's own model identity
for ``claude-sonnet-5``.
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

log = get_logger("reasoning.providers.anthropic")


class AnthropicProvider(BaseLLMProvider):
    """Claude, via the ``anthropic`` SDK's async Messages API."""

    name = "anthropic"
    supports_vision = True

    def __init__(
        self,
        model: str = "claude-sonnet-5",  # shipped default only — Settings controls this, see docstring
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
            import anthropic
        except ImportError as exc:
            raise ProviderUnavailableError(
                "The 'anthropic' package is not installed, so the Anthropic provider is "
                "unavailable. Install it with `pip install spark[anthropic]` (or `pip install "
                "anthropic` directly). See anthropic.py's module docstring for the SDK this "
                "was verified against."
            ) from exc
        return anthropic

    def _resolve_api_key(self) -> str:
        key = self._api_key or get_api_key("anthropic")
        if not key:
            raise ProviderUnavailableError(
                "No Anthropic API key is configured. Set one in Settings (stored via the OS "
                "credential store, spark.secrets) or pass api_key= directly."
            )
        return key

    def _get_client(self, anthropic_sdk):
        if self._client is None:
            self._client = anthropic_sdk.AsyncAnthropic(api_key=self._resolve_api_key())
        return self._client

    @staticmethod
    def _to_content_blocks(message: Message) -> list[dict]:
        blocks: list[dict] = []
        if message.text:
            blocks.append({"type": "text", "text": message.text})
        for image in message.images:
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.mime_type,
                        "data": base64.b64encode(image.data).decode("ascii"),
                    },
                }
            )
        return blocks

    def _to_request(self, messages: list[Message]) -> tuple[list[dict], str | None]:
        turns: list[dict] = []
        system_parts: list[str] = []
        for message in messages:
            if message.role == MessageRole.system:
                if message.text:
                    system_parts.append(message.text)
                continue
            blocks = self._to_content_blocks(message)
            if not blocks:
                continue
            role = "assistant" if message.role == MessageRole.assistant else "user"
            turns.append({"role": role, "content": blocks})
        system = "\n\n".join(system_parts) if system_parts else None
        return turns, system

    async def _raw_complete(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> RawCompletion:
        # `temperature` is intentionally not forwarded — see module docstring:
        # the current Claude Messages API this SDK talks to has no such
        # parameter (verified by grepping the installed SDK's own types).
        del temperature

        anthropic_sdk = self._import_sdk()
        client = self._get_client(anthropic_sdk)
        turns, system = self._to_request(messages)

        kwargs = {"model": self.model, "max_tokens": max_tokens, "messages": turns}
        if system is not None:
            kwargs["system"] = system

        response = await client.messages.create(**kwargs)

        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")

        return RawCompletion(
            text=text,
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
        )
