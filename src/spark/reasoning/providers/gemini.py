"""Google Gemini provider. See BUILD_SPEC.md §6.7.

**Honesty / verification status (read this before trusting this file):**
This sandbox has no live network access to ``ai.google.dev`` (WebFetch to
that domain returns ``EGRESS_BLOCKED`` — same restriction noted in
``perception/ocr/WINDOWS_OCR_NOTES.md`` for ``learn.microsoft.com``), so the
model-list claims below are via ``WebSearch`` result summaries, not a direct
read of Google's own page — flagged explicitly where that matters. Everything
about the **SDK's actual Python API surface**, by contrast, was verified the
strongest way available: by installing the real, current ``google-genai``
package into this repo's venv (``pip install google-genai`` resolved to
**2.24.0** on 2026-09-17) and inspecting its real source/signatures directly
with ``inspect.signature`` and by grepping ``google/genai/types.py`` — not
from memory, not from documentation summaries. That is strictly better
evidence than a doc page for "does this exact function accept this exact
keyword argument", so the SDK-shape claims below should be trusted more than
the model-name claims.

## Package name / import path

``pyproject.toml`` already lists ``google-genai`` under the ``gemini``
extra, and this is still correct: ``google-genai`` (imported as
``from google import genai``) is Google's current, actively-developed
unified SDK (GitHub: https://github.com/googleapis/python-genai — installed
version 2.24.0, actively released — ``pip index versions google-genai``
shows releases up through 2.24.0 with no gap, one release roughly every 1-2
weeks). The older ``google-generativeai`` package (a different PyPI
distribution, ``import google.generativeai``) is Google's *previous*
generation SDK; multiple search results (e.g. the "Migrate to the Google
GenAI SDK" guide title itself, and the ``deprecated-generative-ai-python``
GitHub repo name Google renamed it to) indicate it is the deprecated one.
**However, I bumped the ``pyproject.toml`` version floor** from
``google-genai>=0.3`` to ``google-genai>=1.0`` — 0.x was this SDK's early,
pre-stabilization API (see the 0.0.1-0.8.0 releases in
``pip index versions google-genai``); the ``Client``/``client.aio``/
``types.Part.from_bytes`` shape this module depends on is the *current*,
long-stable 1.x+ design, so pinning below 1.0 would let ``pip install
spark[gemini]`` resolve to an SDK generation this module was not written
against.

## Client construction, calling convention, and async

Verified by direct inspection of the installed SDK:

- ``genai.Client(api_key="...")`` — confirmed via
  ``inspect.signature(genai.Client.__init__)``; ``api_key`` is a keyword-only
  optional parameter, matching the README's own example
  (``client = genai.Client(api_key='GEMINI_API_KEY')`` —
  https://github.com/googleapis/python-genai README, fetched directly).
- The async surface is ``client.aio.models.generate_content(...)`` — the
  ``Client`` class exposes an ``aio`` property returning an ``AsyncClient``
  (confirmed by reading ``client.py``: ``self._aio = AsyncClient(...)`` /
  ``def aio(self) -> AsyncClient: return self._aio``), and
  ``AsyncModels.generate_content`` is confirmed to be an actual
  ``async def`` via ``inspect.iscoroutinefunction`` returning ``True`` — so
  it is directly awaitable, no ``asyncio.to_thread`` wrapper needed (unlike
  a purely-synchronous SDK).
- Call shape: ``await client.aio.models.generate_content(model=..., contents=[...],
  config=types.GenerateContentConfig(...))``. ``contents`` accepts a list of
  ``types.Content`` objects. ``types.Content`` (confirmed via
  ``model_fields``) has exactly two fields, ``role`` and ``parts`` — and
  ``role`` is documented on the field itself (read directly off the pydantic
  field) as "Must be either 'user' or 'model'" — **not** "assistant". This
  module maps ``MessageRole.assistant`` -> ``"model"`` accordingly, and
  collects any ``MessageRole.system`` message text into
  ``GenerateContentConfig.system_instruction`` (confirmed present via
  ``'system_instruction' in types.GenerateContentConfig.model_fields``)
  rather than sending it as a turn, since there is no "system" role in
  ``Content``.
- Image parts: ``types.Part.from_bytes(data=..., mime_type=...)`` — confirmed
  via ``inspect.signature``. This is the base64-free path (the SDK itself
  handles base64 encoding for the wire format); this module passes the raw
  PNG bytes from ``ImagePart.data`` straight through, plus its
  ``mime_type``.
- ``GenerateContentConfig`` has ``temperature`` and ``max_output_tokens``
  fields (confirmed via ``model_fields``) — used directly for this
  provider's ``temperature``/``max_tokens`` parameters.

## Token usage

``response.usage_metadata`` is a ``GenerateContentResponseUsageMetadata``
(confirmed by reading ``google/genai/types.py`` directly — note this is a
*different*, response-specific class from the more generic ``UsageMetadata``
also defined in that file, which is used elsewhere, e.g. cached-content
metadata; the generic one uses different field names like
``response_token_count``, which is **not** what appears on a
``generate_content`` response and would be a bug if used here). The
response-specific class has ``prompt_token_count`` and
``candidates_token_count`` — those are the fields this module reads into
``TokenUsage.input_tokens`` / ``TokenUsage.output_tokens``, both
``Optional[int]`` (defaulting to 0 if the SDK ever omits them, e.g. on an
empty/blocked response).

## Model IDs — the one part of this file not verified against primary
## source, because ``ai.google.dev`` is blocked from this sandbox

``WebSearch`` summaries (not a direct fetch — flagged per the rule above)
of Google's own release-notes/model pages suggest, as of September 2026:
a ``gemini-3.x`` generation exists (``gemini-3.1-pro`` GA since February
2026, a ``gemini-3.5-flash``/``gemini-3.6-flash``/``gemini-3.8-flash``
lineage), that Google publishes a rolling alias ``gemini-flash-latest``
(this one *is* independently corroborated — it appears directly in the
googleapis/python-genai README's own streaming example, fetched directly,
not just search snippets), and — **importantly** — that **``gemini-2.5-flash``
(the id currently hard-coded as this app's default in ``config.py``) is
reported to shut down 2026-10-16**, per WebSearch summaries of Google's own
changelog page. **I did not change ``config.py``'s default for this reason**
— BUILD_SPEC §6.7 and ``config.py``'s own comment block are explicit that
model ids are Settings' job, verified by a human at deploy time, not
something to churn based on search-result summaries I could not fetch
directly — but this is exactly the kind of "verify before shipping" fact
that comment block warns about, so it is repeated here loudly: **if
deploying after 2026-10-16, ``gemini-2.5-flash`` will likely be a dead
model id and must be updated in Settings** (``gemini-flash-latest`` is the
best-evidenced current alternative, or whatever
https://ai.google.dev/gemini-api/docs/models shows when a human with
working access to that domain checks it).

Sources: https://github.com/googleapis/python-genai (README, fetched
directly), the installed ``google-genai`` 2.24.0 package's own source
(``client.py``, ``types.py`` — inspected directly, the strongest evidence
used in this file), and WebSearch summaries of
https://ai.google.dev/gemini-api/docs/models and
https://ai.google.dev/gemini-api/docs/changelog (direct fetch blocked by
this sandbox's egress proxy, see above).
"""
from __future__ import annotations

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

log = get_logger("reasoning.providers.gemini")


class GeminiProvider(BaseLLMProvider):
    """Google Gemini, via the ``google-genai`` SDK's async client."""

    name = "gemini"
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
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise ProviderUnavailableError(
                "The 'google-genai' package is not installed, so the Gemini provider is "
                "unavailable. Install it with `pip install spark[gemini]` (or `pip install "
                "google-genai` directly). See gemini.py's module docstring for the SDK this "
                "was verified against."
            ) from exc
        return genai, types

    def _resolve_api_key(self) -> str:
        key = self._api_key or get_api_key("gemini")
        if not key:
            raise ProviderUnavailableError(
                "No Gemini API key is configured. Set one in Settings (stored via the OS "
                "credential store, spark.secrets) or pass api_key= directly."
            )
        return key

    def _get_client(self, genai):
        if self._client is None:
            self._client = genai.Client(api_key=self._resolve_api_key())
        return self._client

    def _to_contents(self, messages: list[Message], types) -> tuple[list, str | None]:
        contents = []
        system_parts: list[str] = []
        for message in messages:
            if message.role == MessageRole.system:
                if message.text:
                    system_parts.append(message.text)
                continue
            role = "model" if message.role == MessageRole.assistant else "user"
            parts = []
            if message.text:
                parts.append(types.Part.from_text(text=message.text))
            for image in message.images:
                parts.append(types.Part.from_bytes(data=image.data, mime_type=image.mime_type))
            if not parts:
                continue
            contents.append(types.Content(role=role, parts=parts))
        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return contents, system_instruction

    async def _raw_complete(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> RawCompletion:
        genai, types = self._import_sdk()
        client = self._get_client(genai)
        contents, system_instruction = self._to_contents(messages, types)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system_instruction,
        )

        response = await client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )

        usage = response.usage_metadata
        input_tokens = (usage.prompt_token_count or 0) if usage is not None else 0
        output_tokens = (usage.candidates_token_count or 0) if usage is not None else 0

        return RawCompletion(
            text=response.text or "",
            usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )
