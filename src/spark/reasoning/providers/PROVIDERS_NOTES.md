# Reasoning providers — research notes (Gemini, Anthropic, OpenAI, Ollama)

Written 2026-09-17 while implementing `gemini.py`, `anthropic.py`, `openai.py`
and `ollama.py` (BUILD_SPEC.md §6.7). This mirrors
`perception/ocr/WINDOWS_OCR_NOTES.md`'s house style: cite everything, flag
what's uncertain, and say plainly when a fact came from a live SDK
inspection versus a doc summary versus a blocked fetch.

**Overall method, and why it differs by provider:** for the three hosted
providers this sandbox has no live API key or network access to the actual
inference endpoints — and must not attempt one (see the task's hard
constraint). But it *can* `pip install` the real, current SDKs and inspect
their actual shipped source/signatures directly — `inspect.signature(...)`
on real installed classes, `grep` across real installed `types/*.py` files,
and reading each package's own `README.md`/`CHANGELOG.md` as shipped in the
sdist (`pip download <pkg> --no-deps`, untarred, read directly). That is
**first-party, load-bearing evidence** for "does this exact function accept
this exact keyword argument" — stronger than a documentation page, let alone
a search-result summary — so most of the SDK-shape claims below are cited
that way rather than to a fetched doc URL. Model-*lineup* facts (what's
GA, what's deprecated) can't be gotten this way (a pip package doesn't know
what's been deprecated server-side), so those are flagged separately with
weaker (WebSearch-summary or SDK-type-stub) evidence.

**Network restrictions actually hit**: `ai.google.dev` and
`platform.openai.com` are blocked by this sandbox's egress proxy — the exact
same `EGRESS_BLOCKED` failure documented in `WINDOWS_OCR_NOTES.md` for
`learn.microsoft.com`. `github.com` (raw docs, READMEs) was *not* blocked and
was used directly wherever possible instead.

## 1. Gemini (`gemini.py`)

- **Package**: `google-genai` is confirmed still current (imported as
  `from google import genai`) — actively released (`pip index versions
  google-genai` shows a steady cadence through **2.24.0**, the version this
  was verified against). The older `google-generativeai` package is the
  deprecated predecessor (its own GitHub repo has been renamed
  `deprecated-generative-ai-python`, and Google publishes a "Migrate to the
  Google GenAI SDK" guide). **Action taken**: bumped `pyproject.toml`'s
  `gemini` extra floor from `google-genai>=0.3` to `>=1.0` — the
  `Client`/`client.aio`/`types.Part.from_bytes` API this module uses did not
  exist in the pre-1.0 releases.
- **Client construction**: `genai.Client(api_key="...")` — confirmed via
  `inspect.signature`, and matches the README fetched directly from
  https://github.com/googleapis/python-genai.
- **Async calling convention**: `client.aio.models.generate_content(...)` —
  confirmed `AsyncModels.generate_content` is a real `async def`
  (`inspect.iscoroutinefunction` → `True`) reachable via `Client.aio`
  (confirmed by reading `client.py`'s own source: `self._aio =
  AsyncClient(...)`).
- **Roles**: `types.Content.role` must be `"user"` or `"model"` (confirmed
  from the field's own description string, read directly off the pydantic
  model) — **not** `"assistant"`. System-role messages have no `Content`
  role to go in; they're collected into `GenerateContentConfig.system_instruction`
  instead (field confirmed present via `model_fields`).
- **Images**: `types.Part.from_bytes(data=<raw bytes>, mime_type=...)` —
  confirmed via `inspect.signature`; no manual base64 needed, the SDK
  handles wire encoding.
- **Usage**: `response.usage_metadata` is a
  `GenerateContentResponseUsageMetadata` (confirmed by reading
  `google/genai/types.py` directly) with `prompt_token_count` /
  `candidates_token_count` — **not** the generically-named `UsageMetadata`
  class also defined in that same file (used for e.g. cached-content
  metadata), which has *different* field names (`response_token_count`
  instead of `candidates_token_count`) and would have been a real bug if
  conflated. `gemini.py`'s docstring flags this explicitly since it's an
  easy mistake to make from a quick doc skim.
- **Model IDs — weakest-evidenced part of this whole task, flagged loudly**:
  `ai.google.dev` is blocked from this sandbox, so this is via WebSearch
  summaries only. Findings, with that caveat: a `gemini-3.x` generation
  reportedly exists (`gemini-3.1-pro` GA since Feb 2026;
  `gemini-3.5/3.6/3.8-flash` variants); `gemini-flash-latest` is a rolling
  alias, and — unlike the rest of this bullet — *that one specific string*
  is independently corroborated by a **direct fetch** of the
  googleapis/python-genai README's own streaming example, not just search
  snippets. Most importantly: **WebSearch summaries of Google's own
  changelog claim `gemini-2.5-flash` (this app's current hard-coded default
  in `config.py`) is scheduled to shut down 2026-10-16.** I did not change
  `config.py`'s default over a search-summary claim I couldn't verify
  directly — but a human deploying this after that date should treat it as
  a real risk and check https://ai.google.dev/gemini-api/docs/models
  themselves before relying on the shipped default.

## 2. Anthropic (`anthropic.py`)

- **SDK installed and inspected directly**: `anthropic` **1.6.0** — a major
  version well past the `0.x` series many guides assume. The package's own
  bundled README now calls itself the *"Claude SDK for Python"* and points
  to `platform.claude.com/docs/en/api/sdks/python` — Anthropic appears to
  have renamed/moved its docs domain since older SDK releases pointed at
  `docs.anthropic.com`; worth knowing so a human doesn't chase a stale URL.
- **The single most important finding across all four providers**: grepping
  every non-beta and beta type module shipped in this SDK version for the
  literal string `"temperature"` returned **zero matches** anywhere —
  not in `messages.create`'s real signature, not in `MessageCreateParamsBase`,
  not in the new `OutputConfigParam` (which only has `effort`, a
  `low|medium|high|xhigh|max` qualitative literal, and `format`). The
  package's own README and full `CHANGELOG.md` (read directly from the
  downloaded sdist, back through the 0.x→1.0.0 migration entry) never
  mention it either. **The current Claude Messages API this SDK talks to
  has no per-request sampling temperature at all.** `anthropic.py`'s
  `_raw_complete` accepts the shared `temperature` argument (required by
  `BaseLLMProvider`'s contract) and explicitly does **not** forward it —
  documented prominently in that module's docstring so nobody "fixes" this
  by guessing a mapping onto `effort` (which is a qualitatively different
  knob) without a deliberate decision to do so.
- **Model id**: `"claude-sonnet-5"`, per the task's own instruction — this
  is the identity of the model that did this research, independently
  verifiable without a web search. Corroborated (not proven) by the
  installed SDK's own README "Getting started" example, which uses
  `model="claude-opus-5"` — the same `claude-<tier>-5` naming convention.
- **Messages/content shape**: `system=` remains a plain top-level string (or
  block-list) parameter, separate from `messages` — confirmed present in
  `message_create_params.py` — so system-role `Message`s are collected into
  that parameter rather than a `"system"`-role message item (which the
  `MessageParam.role` literal technically also allows now, but using the
  top-level `system=` keeps this code compatible with older 0.x SDK
  releases too, not just 1.x). Images:
  `{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}`
  — `media_type` is a **closed literal** of exactly `image/jpeg | image/png
  | image/gif | image/webp` (confirmed in `base64_image_source_param.py`);
  Spark only ever produces PNG, so this is a non-issue in practice but is
  worth knowing if that ever changes.
- **Usage**: `message.usage.input_tokens` / `.output_tokens` — both required
  plain `int` fields (confirmed in `types/usage.py`), not the various
  optional cache/server-tool-use breakdown fields also present on that
  class.

## 3. OpenAI (`openai.py`)

- **SDK installed and inspected directly**: `openai` **3.14.1** — again a
  major version well past the "1.x" most guides assume.
- **Responses API vs. Chat Completions**: the installed package's own
  bundled `README.md` states outright, in its own words: *"The primary API
  for interacting with OpenAI models is the Responses API."*, with Chat
  Completions described as *"The previous standard (supported
  indefinitely)"*. `openai.py` therefore calls `client.responses.create(...)`,
  confirmed present and genuinely async on the installed SDK via
  `inspect.signature(AsyncResponses.create)` (which lists `model`, `input`,
  `instructions`, `max_output_tokens`, `temperature` as real parameters —
  note `temperature` *is* still a real parameter here, unlike Anthropic).
- **Model id — the part the task asked to scrutinize hardest.** Grepping the
  *actual installed SDK's own type stubs*
  (`src/openai/types/shared/chat_model.py` / `all_models.py`, from the sdist
  downloaded via `pip download openai --no-deps`) turned up a real,
  plausible `gpt-5*` lineage: `gpt-5`, `gpt-5-mini`, `gpt-5.1` (+ `-mini`,
  `-codex`), `gpt-5.2` (+ `-pro`), `gpt-5.4` (+ `-mini`, `-nano`), **`gpt-5.5`**
  (+ `-pro`, with a dated snapshot `gpt-5.5-pro-2026-04-23` — OpenAI has
  historically only minted dated snapshots for models that actually
  shipped), and further out `gpt-5.6-sol` / `-terra` / `-luna` / `-cyber`.
  **This directly contradicts the task's working assumption that a name
  like "GPT-5.6 Sol" is necessarily fabricated aggregator content — it is a
  real string in the real, currently-published `openai` PyPI package.**
  Flagging this loudly because it cuts against what I was told to expect. I
  still did **not** pick a `gpt-5.6-*` name as the recommended default,
  because none of them appear in the SDK's own README usage examples, while
  **`gpt-5.5` is used in every single code example in that README** (dozens
  of occurrences: getting-started, Responses API, async, vision, streaming).
  That — one name used consistently as *the* illustrative default across a
  first-party SDK's own docs, versus codename-shaped names appearing only in
  a raw enum and nowhere in prose — is the best signal available without
  live `platform.openai.com` access. **Recommendation: `"gpt-5.5"`** is the
  best-evidenced current default; **I left `config.py`'s shipped default as
  `"gpt-4.1-mini"` unchanged** per the task's specific instruction to leave
  a clearly-marked placeholder there for a human to verify — `openai.py`'s
  own docstring carries the full finding and the recommendation, and a
  human should update Settings after checking `platform.openai.com`
  directly (blocked from this sandbox).
- **Multimodal input**: confirmed directly from the README's own "Vision"
  section — a content list of `{"type": "input_text", "text": ...}` and
  `{"type": "input_image", "image_url": "data:<mime>;base64,<b64>"}` (or a
  plain URL, not used here since Spark's images are always in-memory bytes).
- **Usage**: `response.usage.input_tokens` / `.output_tokens` (confirmed
  by reading `src/openai/types/responses/response_usage.py` directly);
  `response.output_text` is the README's own documented convenience
  property for the full text output.

## 4. Ollama (`ollama.py`)

- No dedicated SDK — implemented directly over HTTP with `httpx`, per the
  task's instruction. `https://github.com/ollama/ollama/blob/main/docs/api.md`
  fetched **directly** (not blocked, unlike the two domains above) — twice,
  once for the base request/response/images shape and once specifically for
  the `options` object and health-check endpoints.
- **Endpoint**: `/api/chat` (not `/api/generate`) — it's the one with a
  `messages` list (roles) and per-message `images`, matching this
  provider's `Message`-list interface.
- **Images**: a list of **raw base64 strings, no `data:` URI prefix**,
  attached per-message as `"images": ["<base64>", ...]` — confirmed directly
  from the fetched doc's own example JSON.
- **Generation params live under a nested `options` object**, not top-level:
  `options.temperature`, and — this is the one worth calling out —
  **`options.num_predict`, not `max_tokens`**, is Ollama's max-output-tokens
  knob. Confirmed directly from the fetched doc's own example.
- **Token usage — confirmed, matches the task's own terminology**:
  `prompt_eval_count` ("how many input tokens were in the prompt") and
  `eval_count` ("how many output tokens were processed") are Ollama's own
  terms, present on the final (non-streamed, since this module always sends
  `"stream": false`) response object.
- **"Is Ollama running?" handling**: `httpx.RequestError` (parent of
  connection-refused/DNS-failure/timeout, confirmed via
  `httpx.ConnectError.__mro__` on this repo's installed `httpx` 0.28.1) is
  caught and re-raised as `ProviderUnavailableError` naming "is Ollama
  running?" explicitly, per the task's requirement — never a raw `httpx`
  traceback. A non-2xx response (`httpx.HTTPStatusError`, e.g. the model
  isn't pulled locally) is likewise translated to `ProviderUnavailableError`
  with the response body attached, rather than left to propagate raw.

## Cross-cutting: `spark/secrets.py`

Did not already exist (grepped the whole repo for `keyring` first — only
`config.py`'s own docstring and `pyproject.toml`'s dependency line mentioned
it, no concrete get/set helper). Added a minimal module: `get_api_key(name)
-> str | None` and `set_api_key(name, key) -> None`, both backed by the
`keyring` package under one service name, `"spark"`, keyed by provider name.
**Worth flagging**: in *this sandbox*, `keyring.get_keyring()` resolves to
`keyring.backends.fail.Keyring` and `get_password` raises
`keyring.errors.NoKeyringError` rather than returning `None` — there is no
usable OS credential store here at all. `get_api_key` catches this (and any
other backend error) and returns `None`, logging a warning, so every
provider's "no API key configured" path is exercised the same way a real
"user hasn't set a key yet" case would be. A brand-new Linux dev machine
with no Secret Service/KWallet running can hit the identical situation, so
this isn't purely a sandbox artifact — worth a human's attention if
`get_api_key` ever silently returns `None` unexpectedly on a real machine
too.

## Summary: what a human must verify before this ships with a real key

1. **OpenAI's model id** — check `platform.openai.com`'s own current model
   list directly (blocked here) before trusting either `"gpt-4.1-mini"`
   (`config.py`'s current placeholder, safe but likely stale) or `"gpt-5.5"`
   (this task's better-evidenced recommendation, from the SDK's own bundled
   sources — still not a live-API confirmation).
2. **Gemini's `gemini-2.5-flash` default** — WebSearch summaries claim it
   shuts down 2026-10-16; confirm directly at
   https://ai.google.dev/gemini-api/docs/models (blocked here) and update
   Settings if deploying near or after that date.
3. **Anthropic's missing `temperature` parameter** — confirm this is still
   true on whatever `anthropic` SDK version actually ships (this was
   verified against 1.6.0); if a future release reintroduces a
   temperature-like control, `anthropic.py`'s silent no-op should become a
   deliberate mapping instead.
4. **All four `pip install`-time version floors** in `pyproject.toml` were
   set from what's resolvable *today* (2026-09-17) — re-check them
   periodically; SDKs at this pace of major-version churn (`anthropic`
   0.x→1.x, `openai` 1.x→3.x, both within roughly the last year) will keep
   moving.
5. **None of this was exercised against a real API key or live network
   call**, by design (the task's hard constraint) — the unit tests mock the
   SDK/HTTP boundary and prove this code's own translation logic, not that
   the real APIs still look exactly like this on the day someone flips a
   real key in.
