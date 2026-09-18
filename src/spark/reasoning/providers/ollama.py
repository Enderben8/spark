"""Ollama provider — a local model over Ollama's HTTP API. See BUILD_SPEC.md §6.7.

Unlike the other three providers, this one deliberately has no dedicated
Python SDK dependency: it talks to Ollama's own local HTTP server directly
with ``httpx`` (already a hard dependency of this project — see
``pyproject.toml``), matching the task's explicit instruction. There is also
no API key, since Ollama serves models from the user's own machine.

**Honesty / verification status:** ``ai.google.dev``-style domains are
blocked from this sandbox's egress proxy, but
``https://github.com/ollama/ollama/blob/main/docs/api.md`` (the project's
own primary API reference, on GitHub) fetched directly without any block —
everything below is quoted/paraphrased from that direct fetch, not a search
summary, except where noted.

## Endpoint: ``/api/chat``, not ``/api/generate``

``/api/chat`` is the endpoint that takes a ``messages`` list (with
system/user/assistant roles) and supports per-message ``images``, which is
what this provider needs for multi-turn, system-prompted, optionally
multimodal calls. ``/api/generate`` is the older single-prompt-string
endpoint; some Ollama docs/examples show it also accepting a top-level
``images`` array for a single-turn multimodal request, but it has no concept
of conversation roles, so ``/api/chat`` is the correct fit for this
provider's ``Message``-list interface and is what this module uses
exclusively.

## Request shape (confirmed via direct fetch of ``docs/api.md``)

```json
{
  "model": "llama3.2",
  "messages": [
    {"role": "user", "content": "why is the sky blue?", "images": ["<base64>"]}
  ],
  "stream": false,
  "options": {"temperature": 0.8, "num_predict": 100}
}
```

- ``images`` is a list of **base64-encoded strings with no ``data:...;base64,``
  prefix** — just the raw base64 payload, one string per image, attached to
  the specific message it belongs to (confirmed by the doc's own example:
  ``"images": ["base64_encoded_string"]``).
- Runtime generation parameters (temperature, max output tokens, ...) go
  under a nested ``options`` object, **not** top-level fields. The token
  count analog of "max_tokens" is ``options.num_predict`` (confirmed
  directly in the fetched doc's own example JSON, alongside ``temperature``,
  ``top_k``, ``top_p`` in the same object) — there is no bare top-level
  ``max_tokens``/``temperature`` field on the ``/api/chat`` request.
- ``"stream": false`` is required to get a single JSON object back instead
  of newline-delimited streaming chunks; this module always sets it, since
  ``BaseLLMProvider``'s interface is a single non-streaming completion.

## Response shape and token usage (confirmed via direct fetch)

With ``stream: false``, the response is one JSON object:

```json
{
  "model": "llama3.2",
  "message": {"role": "assistant", "content": "..."},
  "done": true,
  "prompt_eval_count": 26,
  "eval_count": 298,
  ...
}
```

Per the fetched doc: ``prompt_eval_count`` is "how many input tokens were in
the prompt" and ``eval_count`` is "how many output tokens were processed" —
exactly Ollama's own terms for prompt/completion tokens, confirmed directly
rather than assumed from the field names alone. Both are optional in
principle (e.g. a request that errors before generating), so this module
defaults each to 0 if absent rather than raising ``KeyError``.

## No API key; "is it even running" as a first-class error

Ollama is local and unauthenticated by design, so there is no
``spark.secrets`` lookup here (unlike the other three providers) — the
constructor only needs ``base_url`` (from ``ProviderSettings.base_url``,
defaulting to ``http://localhost:11434`` — confirmed as Ollama's documented
default port). The one failure mode specific to a local server is "Ollama
isn't running at all" or "the base URL is wrong" — an ``httpx.RequestError``
(the parent class of connection refused / DNS failure / timeout — confirmed
via ``httpx.ConnectError.__mro__`` in this repo's installed ``httpx``
0.28.1) is caught and re-raised as a ``ProviderUnavailableError`` that
explicitly asks "is Ollama running?", per the task's requirement, rather
than letting a raw ``httpx`` exception propagate. A non-2xx HTTP response
(``httpx.HTTPStatusError`` after ``raise_for_status()``) — e.g. the model
name isn't pulled locally — is also translated to ``ProviderUnavailableError``
with the response body, since from the caller's point of view "the local
Ollama isn't usable for this request" is the same category of problem either
way; it is not a schema-validation failure, which is what
``LLMSchemaValidationError`` is reserved for.

Sources: https://github.com/ollama/ollama/blob/main/docs/api.md (fetched
directly, twice — once for the base request/response/images shape, once
specifically for the ``options``/temperature/``num_predict`` shape and the
``/api/tags`` and ``/api/version`` health-check endpoints), and this repo's
installed ``httpx`` 0.28.1 exception hierarchy (inspected directly).
"""
from __future__ import annotations

import base64

import httpx

from spark.logsetup import get_logger
from spark.reasoning.provider import (
    BaseLLMProvider,
    Message,
    MessageRole,
    ProviderUnavailableError,
    RawCompletion,
    TokenUsage,
)

log = get_logger("reasoning.providers.ollama")


class OllamaProvider(BaseLLMProvider):
    """A local model served by Ollama, via its HTTP ``/api/chat`` endpoint."""

    name = "ollama"
    supports_vision = True  # true for a multimodal-capable local model (e.g. llava); a
    # text-only local model will simply ignore/reject images per its own
    # behaviour — BUILD_SPEC §6.7 requires a provider that lacks vision to
    # still work for DOM-only pages, which this satisfies regardless.

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.default_temperature = temperature
        self.default_max_tokens = max_tokens

    @staticmethod
    def _to_ollama_message(message: Message) -> dict:
        payload: dict = {
            "role": "assistant" if message.role == MessageRole.assistant else (
                "system" if message.role == MessageRole.system else "user"
            ),
            "content": message.text or "",
        }
        if message.images:
            payload["images"] = [base64.b64encode(image.data).decode("ascii") for image in message.images]
        return payload

    async def _raw_complete(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> RawCompletion:
        body = {
            "model": self.model,
            "messages": [self._to_ollama_message(m) for m in messages],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=120.0) as client:
                response = await client.post("/api/chat", json=body)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderUnavailableError(
                f"Ollama returned an error for model {self.model!r}: "
                f"{exc.response.status_code} {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"Could not reach Ollama at {self.base_url} — is Ollama running? "
                f"({exc})"
            ) from exc

        data = response.json()
        text = (data.get("message") or {}).get("content", "")

        return RawCompletion(
            text=text,
            usage=TokenUsage(
                input_tokens=data.get("prompt_eval_count") or 0,
                output_tokens=data.get("eval_count") or 0,
            ),
        )
