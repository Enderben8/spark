# Spark — Build Specification

**Audience:** the engineer/model implementing this app. Read the whole document before writing code.
**Status:** requirements locked by the product owner (see §1). Anything not locked is marked **[ASSUMPTION]** — implement as written, but flag it in your first progress report.
**Target platform:** Windows 10/11 desktop (x64).
**Implementation:** all twelve milestones below are built and tested — see the repo root [`README.md`](../README.md#status) for what's verified vs. still needing a real Windows machine before shipping. This document remains the design reference; it was not rewritten to read as a changelog.

---

## 1. What is being built

A Windows desktop application ("Spark") that drives the user's own Chrome browser with an AI model. It:

1. Reads the text of a web page — from the DOM where possible, falling back to OCR of a screenshot when the DOM yields nothing useful.
2. Remembers what it read.
3. Clicks buttons and links to advance through the page flow.
4. When a set of multiple-choice questions appears — questions *about the text it just read* — it selects the correct answers.
5. After a question set, the site displays a score. Spark reads that score and **keeps looping through the flow until the score reaches a configured target**, then stops.

### 1.1 Primary use case (the owner's words)

> "OCR will be needed to read the text, then a button, then questions based on the text will show up on the page with answers to be clicked. Other things may also have to be clicked."

> "There is a score that shows up once a set of questions is answered; once the score reaches target, stop."

The site is the owner's **own personal site**. The stated purpose is to exercise it as though real people were using it. The owner also wants the result to be a **reusable general-purpose tool**, not a single-site script — so nothing site-specific may be hard-coded in the core. Site-specific knowledge lives only in task/script files (§9) and settings.

### 1.2 Locked decisions

| Decision | Choice |
| --- | --- |
| Browser control | **Attach to the user's existing Chrome** over the Chrome DevTools Protocol (CDP). Do **not** launch a throwaway Playwright browser as the primary path. |
| AI model | **Pluggable** — provider chosen in settings. Gemini, Anthropic Claude, OpenAI and a local Ollama option behind one interface. **Ship Gemini as the default** (owner wants the free tier); see §5.1 for the caveats that come with it. |
| Autonomy | **Fully autonomous.** Given a task, run to completion without prompting. (Safety rails in §12 are still mandatory — they are budget/scope limits, not approval prompts.) |
| Page reading | **Hybrid**: DOM text first, OCR fallback. Never assume one or the other. OCR defaults to **free local Windows OCR, escalating to the AI model only when the local read looks poor** (§6.4). |
| Answer policy | **Always select the correct answer**, reasoned from the text actually read. |
| Task input | **Both**: a plain-English goal (AI-driven), and optional saved YAML scripts for deterministic, cheap reruns. |
| Stop condition | **Score-target based**: the site shows a **plain points number** that is **cumulative across rounds**; stop once it reaches the configured target (§6.10). |
| Stack | **Python 3.11+** with a small desktop window (start/stop, live log, settings). |
| Packaging | Runnable from source during development; single-file `.exe` via PyInstaller as the final milestone. |
| Authentication | The site **requires a login**. One-time manual sign-in in the Spark Chrome profile is a required part of first-run setup (§2.1, §6.1). |
| Run artefacts | Full text log every run; screenshots of **question and score pages only** (§13). |

### 1.3 Explicit non-goals

- No parallel/concurrent browser sessions in v1 (it conflicts with attaching to the user's everyday Chrome).
- No CAPTCHA solving, no anti-bot evasion, no fingerprint spoofing. If a CAPTCHA or bot-check appears, Spark stops and reports it.
- No payment flows, no account deletion, no destructive operations (§12.2).
- No mobile, macOS or Linux support in v1 (keep the code portable where free, but do not spend effort on it).

---

## 2. Two constraints that will break naive implementations

Read these before designing anything. Both were verified during specification; **re-verify both against primary sources before you rely on them**, because browser behaviour changes.

### 2.1 Chrome will silently ignore `--remote-debugging-port` on the default profile

Since **Chrome 136**, `--remote-debugging-port` and `--remote-debugging-pipe` are **not honoured when Chrome is running on the default user-data directory**. They must be accompanied by a `--user-data-dir` pointing at a **non-default** directory. Google made this change deliberately, to stop an attacker attaching to a real user profile and extracting its state: a non-default data directory is encrypted with a different key.

The failure mode is nasty and is the single most likely way this project wastes a day:

> Chrome **starts normally, does not error, and does not warn**. The debugging port is simply never opened. An automation client that blindly connects may report healthy and then hang on a blank page.

**Required implementation consequences:**

- Spark must **never** attempt CDP against Chrome running on the default profile.
- Spark must maintain its own **dedicated automation profile directory** (default: `%LOCALAPPDATA%\Spark\chrome-profile`), passed as `--user-data-dir` on every launch.
- On first run, Spark must tell the user plainly: *"This uses a separate Chrome profile. Sign in to your site once in this window; the login is then remembered for future runs."* Offer a "Set up browser profile" button in settings that launches the profile and waits for the user to sign in.
- **Do not copy the user's real Chrome profile directory.** It is encrypted against the original path, copying it is fragile, and it drags credentials for unrelated sites into the automation profile. If the user explicitly asks for their existing logins, the supported answer is "sign in once in the Spark profile".
- Before connecting, Spark must **positively verify** the port is live by polling `http://127.0.0.1:<port>/json/version` and checking for a valid JSON body with a `webSocketDebuggerUrl`. A timeout here is a hard, loud error naming this exact cause — not a hang, and not a silent retry loop.

Primary source to verify: *"Changes to remote debugging switches to improve security"*, Chrome for Developers blog — https://developer.chrome.com/blog/remote-debugging-port (this sandbox could not fetch it directly; open it and confirm the version number and wording before finalising the launcher).

### 2.2 The page is taller than the screen

The owner raised this directly: **the whole page does not fit on the screen without scrolling.** Any capture strategy that grabs only the visible viewport will read a fraction of the passage and produce confidently wrong answers. Handle it as follows, in order:

1. **Prefer DOM text.** DOM extraction is not bounded by the viewport at all, and is the accurate, cheap path. OCR is the fallback, not the default.
2. **Scroll the full page once before capture** to trigger lazy-loading / virtualised content, then return to the top. Without this, a full-page screenshot of a lazy-loading page captures placeholders.
3. **Full-page screenshot** via Playwright's `full_page=True` (which drives CDP's `captureBeyondViewport`).
4. **Segment tall pages.** Chrome cannot produce an unbounded image, and very tall images degrade both OCR and vision-model accuracy. If the full-page image exceeds a configured height (**[ASSUMPTION]** default 8000 CSS px), fall back to **scroll-and-stitch**: capture viewport-sized tiles with a **15% overlap**, and either (a) OCR each tile and de-duplicate the overlapping text, or (b) pass the tiles to the vision model as an ordered sequence of images. De-duplication must be text-based (longest common overlap between consecutive tiles), not pixel-based.
5. **Watch for sticky headers/footers**, which duplicate in every tile. Detect elements with `position: fixed`/`sticky` via DOM and either hide them for the duration of capture (set `visibility: hidden`, restore after) or strip their text from every tile but the first.
6. Record the **device pixel ratio** and the scroll offset of every tile. Without them, OCR bounding boxes cannot be mapped back to clickable page coordinates (§6.4).

---

## 3. Architecture

Perceive → Decide → Act, with an explicit memory of what was read.

```
┌──────────────────────────────────────────────────────────────┐
│ GUI (PySide6)  — start/stop, live log, settings, run history │
└───────────────┬──────────────────────────────────────────────┘
                │ commands / events (Qt signals over a queue)
┌───────────────▼──────────────────────────────────────────────┐
│ Orchestrator  — owns the run loop, budgets, stop conditions  │
└──┬────────────┬─────────────┬──────────────┬─────────────────┘
   │            │             │              │
┌──▼──────┐ ┌───▼───────┐ ┌───▼──────────┐ ┌─▼──────────────┐
│ Browser │ │ Perception│ │ Reasoner     │ │ Run Recorder   │
│ Session │ │ (DOM+OCR) │ │ (LLM router) │ │ (logs, shots)  │
└──┬──────┘ └───┬───────┘ └───┬──────────┘ └────────────────┘
   │            │             │
┌──▼────────────▼─────────────▼──────────────────────────────┐
│ Chrome (user's own, separate automation profile, via CDP)  │
└────────────────────────────────────────────────────────────┘
                     ▲
            ┌────────┴─────────┐
            │ Memory Store     │  passages read, Q&A history,
            │ (per run)        │  score readings, visited steps
            └──────────────────┘
```

**Key architectural rule:** the Orchestrator is the only component that decides *what happens next*. The Reasoner proposes, it never acts. The Browser Session acts, it never decides. This separation is what makes the deterministic script mode (§9.2) and the AI mode share one execution path.

---

## 4. Repository layout

```
spark/
├── README.md
├── pyproject.toml
├── docs/
│   ├── BUILD_SPEC.md            ← this document
│   └── USAGE.md                 ← write this at M8
├── src/spark/
│   ├── __main__.py              # entry point: python -m spark
│   ├── config.py                # settings model + load/save + secrets
│   ├── orchestrator.py          # the run loop, budgets, stop conditions
│   ├── memory.py                # per-run memory store
│   ├── browser/
│   │   ├── launcher.py          # Chrome profile bootstrap + launch + port health check
│   │   ├── session.py           # Playwright connect_over_cdp, page handle, navigation
│   │   └── actions.py           # click/type/scroll/select primitives + verification
│   ├── perception/
│   │   ├── dom.py               # DOM text + interactive-element inventory
│   │   ├── capture.py           # full-page / tiled screenshots, DPR, sticky handling
│   │   ├── ocr/
│   │   │   ├── base.py          # OcrEngine protocol
│   │   │   ├── vision.py        # LLM-vision OCR (escalation path)
│   │   │   ├── windows.py       # Windows.Media.Ocr via winsdk (default, offline)
│   │   │   └── tesseract.py     # optional
│   │   └── page_view.py         # merges DOM + OCR into one PageView object
│   ├── reasoning/
│   │   ├── provider.py          # LLMProvider protocol + router
│   │   ├── providers/           # gemini.py, anthropic.py, openai.py, ollama.py
│   │   ├── prompts.py           # all prompt templates, versioned
│   │   └── schemas.py           # pydantic models for every structured LLM output
│   ├── skills/
│   │   ├── answering.py         # question detection + grounded answering
│   │   └── scoring.py           # score extraction + target evaluation
│   ├── scripts/
│   │   ├── model.py             # YAML task/script schema
│   │   ├── runner.py            # deterministic replay
│   │   └── recorder.py          # turn a successful AI run into a script
│   ├── logging/
│   │   └── recorder.py          # run artefacts: JSONL, screenshots, report
│   └── gui/
│       ├── main_window.py
│       ├── settings_dialog.py
│       └── log_view.py
└── tests/
    ├── fixtures/site/           # local test site (§11.1) — build this early
    └── ...
```

---

## 5. Stack and dependencies

| Concern | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.11+ | 3.12 fine. Pin in `pyproject.toml`. |
| Browser automation | `playwright` (Python) | Used **only** in `connect_over_cdp` mode. |
| GUI | `PySide6` | Qt6. Official Qt binding, LGPL. Keep the GUI thin; all work on a worker thread. |
| Config/validation | `pydantic` v2 | Every LLM output and every config file is a pydantic model. |
| Secrets | `keyring` | API keys go in Windows Credential Manager, **never** in a plaintext config file. |
| HTTP | `httpx` | For provider SDKs that need it and for the CDP health check. |
| Images | `Pillow` | Tiling, stitching, downscaling before sending to a model. |
| YAML | `ruamel.yaml` or `pyyaml` | For task/script files. |
| Logging | stdlib `logging` + JSONL | Structured run logs. |
| Tests | `pytest`, `pytest-asyncio` | |
| Packaging | `pyinstaller` | Final milestone only. |

Optional, install-on-demand: `winsdk` (Windows OCR), `pytesseract` (+ Tesseract binary).

**Model SDKs:** each provider module imports its SDK lazily inside the module, so a user who only uses Gemini does not need the Anthropic SDK installed. A missing SDK must produce a clear "install X to use this provider" message, not an import traceback at startup.

### 5.1 Default provider: Gemini, and what comes with the free tier

The owner wants zero running cost, so **Gemini ships as the default provider** and the free tier is the assumed starting point. Three consequences for the implementation — none is a blocker, all are things that will bite if ignored:

1. **Free-tier data use.** Google's published terms distinguish paid from unpaid use: content submitted to the **unpaid** services, and the responses, may be used to improve Google's products, and may be reviewed by humans — Google explicitly advises against submitting sensitive, confidential or personal information to unpaid services. Linking a billing account moves you to the paid terms, under which prompts and responses are not used for product improvement. **Verify the current wording at https://ai.google.dev/gemini-api/terms and https://ai.google.dev/gemini-api/docs/billing** (this could not be fetched during specification — the sandbox blocks that domain). Practical effect here: the passages on the owner's own test site are almost certainly fine, but Spark must **never** send login pages, account pages or redacted-secret content to a free-tier model. The redaction rule in §12.3 is therefore mandatory, not optional, and the GUI should state which tier the configured key is on if that is detectable.
2. **Rate limits are low and change often.** Free-tier limits are per-project, differ sharply by model, and reset daily. Do not hard-code any number. Implement: respect `Retry-After`, exponential backoff on 429, a clear "daily free-tier quota exhausted" state that **pauses the run rather than failing it**, and a setting for requests-per-minute throttling. Check current limits at https://ai.google.dev/gemini-api/docs/rate-limits. Widely-circulated figures for these limits are inconsistent and mostly from third-party blogs — use Google's table, not a summary of it.
3. **This is why local-first OCR matters.** With `windows` OCR doing the reading (§6.4), model calls drop to roughly one classify + one answer per question set, plus occasional escalations. That is what keeps a long loop inside a free tier. If you ever change the default back to vision-OCR, re-check the arithmetic against the daily quota first.

**Alternatives worth mentioning to the owner if Gemini's limits prove too tight:** a local model via **Ollama** (free forever, fully private, no quota — but materially weaker at reading pages and choosing actions, so expect lower answer accuracy), or any provider's paid tier at a few pence per run. The pluggable provider layer means switching is a settings change, not a rewrite.

---

## 6. Component specifications

### 6.1 `browser/launcher.py` — getting a debuggable Chrome

Responsibilities:

- Locate `chrome.exe`: check `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe` in the registry, then the standard Program Files paths, then a user-configured override in settings.
- Ensure the automation profile directory exists.
- Launch with, at minimum:
  `--remote-debugging-port=<port> --user-data-dir=<automation profile> --no-first-run --no-default-browser-check`
- **Never** pass `--remote-debugging-port` without `--user-data-dir` (§2.1).
- Poll `http://127.0.0.1:<port>/json/version` until it returns valid JSON containing `webSocketDebuggerUrl`, with a timeout (**[ASSUMPTION]** 20 s). On timeout, raise `ChromeDebugPortUnavailable` with a message that names the Chrome-136 default-profile cause explicitly.
- **Reuse, don't duplicate:** if the port is already serving, attach to it rather than launching a second Chrome.
- Port selection: default 9222, configurable; if occupied by something that is *not* Chrome DevTools, pick the next free port and say so in the log.
- Clean shutdown: Spark should leave Chrome running by default (the user may want to inspect the result). "Close browser when run ends" is a setting, default off.

### 6.2 `browser/session.py` — the Playwright attachment

- `chromium.connect_over_cdp(f"http://127.0.0.1:{port}")`.
- Take the existing default context (`browser.contexts[0]`) rather than creating a new one — a CDP-attached browser already has the user's context, and creating a new one loses the profile's session state.
- Page selection: prefer an existing tab whose URL matches the task's target; otherwise open a new tab. Never assume `context.pages[0]`.
- Expose: `goto`, `current_url`, `wait_for_settle`, `evaluate`, `screenshot`, `frames`.
- **`wait_for_settle`** is important and must not be a fixed sleep. Implement as: wait for `load`, then wait for network to be quiet for 500 ms **or** a DOM-mutation-free window of 500 ms, capped at a configurable maximum (**[ASSUMPTION]** 10 s). Single-page apps frequently never reach `networkidle`; the cap must not be fatal.
- **iframes:** page content may live in an iframe. All DOM extraction and element inventory must walk `page.frames`, tagging each element with its frame path so actions can be dispatched to the right frame.

### 6.3 `perception/dom.py` — DOM text and the element inventory

Two outputs.

**(a) Readable text.** Extract visible text content in document order. Requirements:
- Skip `script`, `style`, `noscript`, `template`, and elements that are not visible (zero size, `display:none`, `visibility:hidden`, `opacity:0`, `aria-hidden="true"`).
- Preserve block structure — emit paragraph breaks at block-level boundaries, so the passage reads correctly.
- Record, per text run, the owning element's bounding box and frame, so text can later be linked to a location.
- Return a **quality signal**: total visible character count, and the ratio of characters found inside `canvas`/`img`-dominant regions. The Orchestrator uses this to decide whether OCR is needed (§6.5).

**(b) Interactive-element inventory.** Every element a user could act on: `a`, `button`, `input`, `select`, `textarea`, `[role=button|link|radio|checkbox|option|tab]`, `[onclick]`, `[tabindex]:not([tabindex="-1"])`, plus labels associated with radio/checkbox inputs. For each, record:

```jsonc
{
  "id": "e12",                    // Spark-assigned, stable within one page view
  "frame": "main",                // or a frame path
  "tag": "button",
  "role": "button",
  "name": "Next",                 // accessible name: aria-label > label > text > title > alt
  "text": "Next",
  "value": null,
  "state": {"checked": false, "disabled": false, "selected": false},
  "bbox": {"x": 412, "y": 980, "w": 96, "h": 40},  // CSS px, page coordinates
  "in_viewport": false,
  "selector": "form#quiz > button.next",           // best-effort stable selector
  "nearby_text": "…the text immediately preceding it…"
}
```

Prefer Playwright's accessibility/locator machinery over hand-rolled selectors where you can. `nearby_text` matters a lot for multiple-choice answers, where the clickable radio is unlabelled and the answer text sits in a sibling element.

### 6.4 `perception/capture.py` and `perception/ocr/` — the OCR path

Capture per §2.2. The OCR layer is **pluggable behind `OcrEngine`**, exactly like the LLM layer:

```python
class OcrEngine(Protocol):
    def read(self, image: Image.Image) -> OcrResult: ...

class OcrResult(BaseModel):
    text: str
    blocks: list[OcrBlock]      # may be empty for engines without geometry
    engine: str
    confidence: float | None
```

Three engines:

| Engine | Use | Geometry |
| --- | --- | --- |
| `windows` (**default**) | `Windows.Media.Ocr` via `winsdk`. Free, offline, zero API cost, already present on Windows 10/11. Weaker on unusual fonts and multi-column layouts. | **Yes** — per-word bounding boxes. |
| `vision` (**escalation**) | Send the image(s) to the configured vision model and ask for verbatim text. Best accuracy on real-world layouts, handles multi-column and unusual fonts. Costs money per page, so it runs only when the local read looks poor. | **Unreliable.** Do not trust coordinates from a general vision model. Text only. |
| `tesseract` | Optional, needs a separate installer. | Yes. |

**This distinction is load-bearing.** Reading text and locating a click target are different jobs:

- **Reading the passage** → `windows` first, escalating to `vision` per the rule below.
- **Clicking something that exists only in pixels** (canvas-rendered buttons, image maps) → you need bounding boxes, so you need `windows` or `tesseract`. Map image coordinates to page coordinates as:
  `page_x = tile_scroll_x + (image_x / device_pixel_ratio)`, likewise for y. Get the click in via CDP mouse events at those coordinates.
- **Default configuration** (owner's choice — minimise API cost): `ocr.read_engine = "windows"`, `ocr.escalation_engine = "vision"`, `ocr.geometry_engine = "windows"`. The geometry engine is only invoked when a coordinate-click is actually required.

If a required geometry engine is unavailable, Spark must fail the step with a clear message rather than guessing at coordinates.

#### 6.4.1 The escalation rule

Local OCR is free but fallible, and **a misread passage produces confidently wrong answers** — which is exactly the failure this tool must not have. So escalation to the vision model must be generous, not grudging. Escalate to `vision` for a given capture when **any** of:

1. Windows OCR reports mean confidence below a threshold (**[ASSUMPTION]** 0.75), where the engine exposes one.
2. The result fails a **sanity check**: fewer than 100 characters from a capture whose image is substantially non-blank; a dictionary-word ratio below ~70% (garbled output); or no sentence-ending punctuation in a long result.
3. The layout is multi-column or the capture contains a table — detect via DOM geometry where available, or via widely separated text blocks at the same vertical offset. Local OCR commonly interleaves columns into nonsense here, and it does so *without* lowering its confidence, which is why this is a separate trigger.
4. A question could not be answered confidently from the local read (§6.9 step 3) — re-read that page with `vision` before answering.

Log every escalation with its trigger. If escalations exceed ~50% of captures on the owner's real site, local-first is not paying for itself and the default should be revisited — say so in the run report rather than quietly burning API calls.

**Language packs:** `Windows.Media.Ocr` only works for installed OCR language packs. At startup, enumerate available languages and, if the needed one is missing, fall back to `vision` with a clear one-time warning naming the Windows Settings path to install it. Do not let a missing language pack present as bad OCR.

### 6.5 `perception/page_view.py` — one merged view

`PageView` is the single object handed to the Reasoner:

```python
class PageView(BaseModel):
    url: str
    title: str
    text: str                       # merged readable text
    text_source: Literal["dom", "ocr", "dom+ocr"]
    elements: list[InteractiveElement]
    screenshots: list[Path]         # full-page or ordered tiles
    page_height_css: int
    device_pixel_ratio: float
    captured_at: datetime
```

**Merge policy (the hybrid rule):**

1. Always run DOM extraction — it is nearly free.
2. Run OCR if **any** of: DOM visible text is below a threshold (**[ASSUMPTION]** 200 characters); the main content region is dominated by `canvas`/`img`/`embed`/`object`; the page contains a PDF viewer; the user forced OCR for this task; or the Reasoner explicitly asks for it (§7.3).
3. When both exist, prefer DOM text for the body and use OCR only to fill regions the DOM could not explain. **Never concatenate both blindly** — duplicated passage text makes the model answer from a doubled, misaligned context.
4. Record which source was used, per run, in the log. The owner needs to know whether OCR was actually necessary.

### 6.6 `memory.py` — what it read

The passage and the questions are on **different screens**. This is the crux of the task and the most common place a naive agent fails: it navigates away, loses the passage, and then answers the questions from the model's general knowledge instead of from the text.

`RunMemory` holds:

- `passages`: ordered list of `{step_index, url, text, source, captured_at}` — every substantive body of text read this run.
- `qa_history`: every question seen, the options, the chosen answer, the reasoning, the citation (the span of passage text the answer came from), and whether the site marked it right or wrong if that is visible.
- `score_readings`: `{step_index, raw_text, parsed_value, target, captured_at}`.
- `visited`: URLs/step signatures, for loop detection.

**Rules:**
- A passage is never dropped for the duration of a run.
- When answering, the model receives the relevant passage(s) explicitly in the prompt. If total passage text exceeds the context budget, select by recency and lexical overlap with the question — do **not** silently truncate from the front.
- Memory is serialised into the run log so a failed answer can be diagnosed afterwards.

### 6.7 `reasoning/provider.py` — the pluggable model layer

```python
class LLMProvider(Protocol):
    name: str
    supports_vision: bool
    def complete(self, messages: list[Message], *, schema: type[BaseModel] | None,
                 max_tokens: int, temperature: float) -> LLMResponse: ...
```

`LLMResponse` carries the parsed object (when `schema` is given), raw text, and **token usage** — usage must be recorded for the cost budget (§12.1).

Providers to implement: `gemini` (google-genai), `anthropic`, `openai`, `ollama`. Each maps the common interface onto its SDK, including structured output (JSON schema / tool-use / response-format, whichever that provider supports) and image inputs.

**Requirements:**
- Structured output is **mandatory** for every decision. Never parse free-form prose into an action. If a provider cannot enforce a schema natively, request JSON and validate with pydantic, retrying once with the validation error appended.
- Model IDs, temperatures and token limits live in settings, not in code. Ship sensible defaults per provider and let the user override; treat the default IDs as facts to verify at implementation time against each provider's current official model list, not as constants to trust from memory.
- A provider that lacks vision must still work for DOM-only pages, and must produce a clear error if the task requires OCR-by-vision.
- Timeouts, retry with exponential backoff on 429/5xx, and a hard per-run call cap.

### 6.8 `reasoning/prompts.py`

All prompt text lives here, versioned with a constant like `PROMPT_VERSION = "2026-09-17.1"`, recorded in every run log. There are four prompts:

1. **Observe/decide** — given a `PageView` summary, the goal, and recent history, return the next action (§7).
2. **Answer questions** — given the stored passage(s) and the detected question + options, return the correct option with a citation.
3. **Extract score** — given page text, return the score and its maximum/target if present.
4. **Classify page** — cheap classifier: is this a reading page, a question page, a score page, a navigation page, an error/blocked page?

Prompt rules: state the goal, give the element inventory as a compact numbered list (id, role, name, nearby text — not raw HTML), give the last N actions and their outcomes, and demand a single action back. Instruct explicitly: *answer only from the provided passage text; if the passage does not contain the answer, say so rather than guessing.*

### 6.9 `skills/answering.py`

Detect a question set from the merged `PageView`: a question string plus 2+ mutually exclusive option elements (radio group, list of buttons, `select`, or labelled clickable divs).

For each question:
1. Retrieve relevant passage text from memory.
2. Ask the model, with a schema of `{question, chosen_option_id, confidence, citation, reasoning}`.
3. If `confidence` is below a threshold (**[ASSUMPTION]** 0.6) **and** an OCR re-read has not yet been tried for this question, re-read the source page with OCR forced and retry once. This is the main defence against a DOM extraction that quietly missed part of the passage.
4. Click the chosen option, then **verify** the selection stuck (checked state / class change / ARIA state). An unverified click is a failed click.
5. Record everything in `qa_history`.

Handle multiple questions per page, questions revealed one at a time, and "submit" buttons that appear only once all questions are answered.

**Answer policy:** `answer_policy` is a config enum, but **only `"correct"` is implemented in v1** (the owner chose always-correct). Leave the enum in place so other policies can be added later without restructuring; do not build them now.

### 6.10 `skills/scoring.py` — reading the score and stopping

The stop condition for the whole run.

**Confirmed shape on the owner's site:** the score is a **plain points number with no visible maximum**, and it is **cumulative — it builds up across rounds**. Implement for that case, but parse the other shapes too, because the tool is general-purpose.

- **Detection**, in priority order: (1) a user-configured CSS selector or regex from the task file — always wins when present; (2) the page classifier flags a score page and the extract-score prompt returns a value; (3) a regex sweep for common shapes: `Score: 450`, `450 points`, `12/20`, `60%`, `You scored 12 out of 20`.
- **Parse** into `ScoreReading{raw, value, maximum, is_percentage}`. For the owner's site, `maximum` is `None` and `is_percentage` is `False`. Normalise percentage vs absolute consistently and record which it is — mixing them up is the obvious bug here. **Never** derive a percentage when no maximum was actually displayed; a points score must be compared as points.
- **Compare** against `target` from the task file, with a configurable comparison (`>=` default).
- **Cumulative semantics** (`score.cumulative: true`, the default for this site): the site itself maintains the running total, so Spark compares the **latest reading** against the target — it must **not** sum the readings itself. Summing an already-cumulative score would double-count and stop the run early. Assert this explicitly in a unit test.
  - Set `cumulative: false` for a site that scores each round independently; then the target means "a single round at or above target", and the run continues until one round achieves it.
  - **Sanity check:** in cumulative mode, a reading that is *lower* than the previous one means the site reset, the page was misread, or a new session started. Log it loudly, do not treat it as progress, and count it toward the no-improvement bail-out.
- **On reaching target:** stop the run, mark it successful, write the report, leave the browser open.
- **On not reaching target:** perform the task's `loop_action`. For the owner's site this is **click the continue button on the score page** — exact label unknown, so resolve it as: a task-file `loop_action.button_text` if given; else the highest-scoring interactive element on the score page matching `next|continue|try again|again|retry|carry on|next round` (case-insensitive, accessible name); else ask the model to pick one from the inventory. Cache whichever label worked and reuse it for the rest of the run without a further model call.
- **Guard rails** (mandatory — a fully autonomous loop with no ceiling is a defect): stop with a clear "target not reached" outcome when any of `max_iterations` (**[ASSUMPTION]** default 25), `max_runtime_minutes` (default 60), or the cost budget (§12.1) is hit. Also stop if the score **fails to improve** across N consecutive iterations (**[ASSUMPTION]** N=3) — that means something is wrong, and grinding a broken loop is worse than stopping.

### 6.11 Authentication and session handling

**The site requires a login.** Because Spark uses its own Chrome profile (§2.1), the user's everyday browser session does not carry over — this must be handled deliberately, or the first run will silently land on a login page and the agent will try to "answer questions" on it.

**First-run setup (a required step, not an optional one):**

1. Settings has a **"Set up browser profile"** button. It launches Chrome on the Spark automation profile at the task's `start_url` and shows a modal: *"Sign in to your site in the browser window, then click Done. Spark will remember this login for future runs."*
2. Spark does **not** read, capture, store or transmit the credentials. It waits for the user to click Done, then confirms it can reach an authenticated page.
3. The session persists in the automation profile across runs, because it is a real, persistent Chrome profile.

**Before every run:** navigate to `start_url` and check whether the result is an authenticated page or a login wall. Detect a login wall by: a password input present; a URL matching `login|signin|auth|account`; or a redirect away from the requested path. If a login wall is found, **pause the run** (do not fail it, do not attempt to log in) and prompt: *"Your session has expired — sign in again in the browser window, then click Resume."* The owner has not confirmed how often sessions expire, so treat mid-run expiry as possible: the same check runs whenever a navigation lands somewhere unexpected.

**Hard rules:**

- Spark **never** types credentials, never stores a password, and never offers to. The user signs in by hand, always.
- A page detected as a login page is **never sent to the model** — not its text, not a screenshot. This matters specifically because the default provider is a free tier whose terms permit human review of submitted content (§5.1).
- Credentials, cookies and session tokens are never written to a run log.
- The domain allow-list (§12.2) applies from the moment the browser opens, so an unexpected auth redirect to a third-party identity provider stops the run rather than wandering.

---

## 7. The action schema

Every decision the model makes is one of these, validated by pydantic. This is the contract between reasoning and action.

```jsonc
{
  "thought": "The passage ended and a Next button is visible.",
  "action": {
    "type": "click",              // click | type | select | scroll | navigate | wait
                                  // | read_more (force OCR re-read) | answer_questions
                                  // | report_score | finish | fail
    "element_id": "e12",          // for element-targeted actions
    "text": null,                 // for `type`
    "url": null,                  // for `navigate`
    "direction": null,            // for `scroll`: "down" | "up" | "to_element"
    "reason": "Advance to the questions"
  },
  "expectation": "A question set should appear"   // used for post-action verification
}
```

**Execution rules:**

- Resolve `element_id` against the inventory of the **current** `PageView`. Never against a stale one — re-inventory after every action that could change the DOM.
- **Verify every action.** After acting, re-capture and check whether `expectation` plausibly holds (URL changed, DOM mutated, element state changed). A click that changes nothing must be recorded as a failure and retried differently (scroll into view, click the label instead of the input, dispatch via CDP coordinates), not repeated identically.
- **Scroll into view before clicking**; an element outside the viewport is a common silent failure.
- `finish` and `fail` end the run with an explicit outcome. A run must never end ambiguously.

---

## 8. The orchestrator loop

```
load task → ensure browser → navigate to start URL
repeat:
    page_view = perceive()                       # DOM (+ OCR per §6.5)
    page_type = classify(page_view)              # cheap classifier
    if page_type == reading:
        store passage in memory
        action = decide()                        # usually: click Next
    elif page_type == questions:
        answer_questions()                       # §6.9, grounded in memory
    elif page_type == score:
        reading = extract_score()
        if target reached: finish(success)
        else: perform task.loop_action
    elif page_type in (blocked, error):
        fail with a diagnostic (CAPTCHA, login wall, 500 page)
    else:
        action = decide()                        # general AI step
    execute(action); verify(action)
    check budgets (steps, time, cost), stall detection, loop detection
until finished / failed / budget exhausted
write run report
```

**Stall and loop detection:** hash each `PageView` (URL + normalised text + element names). Three identical consecutive hashes after distinct actions means Spark is stuck — try one recovery (reload, scroll, re-read with OCR), then fail with a clear report. Never loop silently.

---

## 9. Task and script files

### 9.1 Task file (the everyday input) — YAML

```yaml
name: "Reading module — reach 80%"
start_url: "https://example.invalid/module/1"
goal: >
  Read each passage, click Next, and answer the comprehension questions
  correctly. Repeat until the score target is met.

stop:
  score_target: 450           # plain points, not a percentage
  comparison: ">="
  max_iterations: 25
  max_runtime_minutes: 60

score:
  cumulative: true            # site keeps the running total; compare latest reading
  is_percentage: false
  selector: null              # optional hint; null = let Spark find it
  regex: null                 # e.g. "Score:\\s*(\\d+)"

loop_action:                  # when the target is not yet reached
  type: click
  button_text: null           # null = auto-detect (next/continue/try again)

auth:
  requires_login: true        # sign in once by hand in the Spark Chrome profile

perception:
  force_ocr: false
  ocr_read_engine: windows    # free local OCR, escalating to the model when poor

limits:
  max_steps_per_iteration: 60
  max_cost_usd: 2.00
```

### 9.2 Script files (deterministic replay)

A script is an ordered list of actions recorded from a successful AI run, each with the selector used, the expectation, and a **fallback flag**. The runner replays them without calling the model. When any step's expectation fails, the runner **falls back to AI mode from that point** and (if the run then succeeds) offers to re-record the script. This hybrid is the whole value: cheap and deterministic when the site is stable, self-healing when it is not.

`scripts/recorder.py` writes a script automatically at the end of every successful AI run, into `%LOCALAPPDATA%\Spark\scripts\<task-name>.yaml`. Question-answering steps are recorded as `answer_questions` (the skill), never as hard-coded option clicks — the questions will differ between runs.

---

## 10. GUI

One small window. Do not over-build it.

- **Top bar:** task selector (dropdown of saved tasks + "New task…"), Start / Stop, mode toggle (AI / Script / Script-with-AI-fallback).
- **Live log pane:** timestamped, colour-coded, human-readable — `Read passage (1,842 chars, via DOM)`, `Clicked "Next"`, `Answered Q3: "B — the tide table" (confidence 0.91)`, `Score: 65% (target 80%) — iteration 4 of 25`.
- **Status strip:** current URL, iteration, elapsed time, estimated cost so far, last score.
- **Settings dialog:** provider + model + API key (stored via `keyring`), OCR engines, Chrome path/port/profile, budgets, "Set up browser profile" button, log directory.
- **Run history:** list of past runs with outcome, opening the run folder.

**Threading:** the orchestrator runs on a worker thread (or a dedicated asyncio loop thread); the GUI only ever renders events from a queue. **Stop must be responsive** — check a cancellation token between every step and inside every wait, so Stop takes effect within a second or two, not at the end of a long model call.

---

## 11. Testing

### 11.1 Build the fixture site first — this is not optional

Before wiring anything to a real site, create `tests/fixtures/site/`: a small static site served by `python -m http.server` that reproduces the target flow:

- `passage.html` — a long passage (**deliberately taller than a 1080p viewport**), plus a `Next` button at the bottom.
- `passage-canvas.html` — the *same* passage rendered into a `<canvas>` so no DOM text exists. This is the OCR test case.
- `passage-lazy.html` — content that only loads as you scroll. Tests §2.2 step 2.
- `questions.html` — 5 multiple-choice questions answerable only from the passage, with radio inputs whose labels sit in sibling elements.
- `score.html` — shows a **cumulative plain points score** (e.g. `Score: 180`, no maximum shown) that **increases with each completed round**, plus a continue button. Deliberately label that button something other than "Next" (e.g. "Carry on") so auto-detection of the label is genuinely exercised rather than accidentally passing.
- `login.html` — a fake sign-in page that sets a session cookie, with every other page redirecting to it when the cookie is absent. This lets the one-time-sign-in flow, the session-expiry detection and the "never send a login page to the model" redaction rule all be tested without touching the owner's real site.
- A variant inside an `<iframe>`, and one with a sticky header.
- A `?reset=1` control to zero the cumulative score, so tests can run repeatably.

Make the correct answers deterministic and known to the tests, so scoring accuracy can be asserted rather than eyeballed. Award a fixed number of points per correct answer so the expected cumulative total after N rounds is exactly predictable.

**Deliberately include a local-OCR trap:** render one passage variant in a condensed or unusual font, and one in two columns. These are precisely the cases where `Windows.Media.Ocr` degrades *without* lowering its confidence score, and they are what the §6.4.1 escalation rule exists to catch. A test must assert that escalation actually fires on the two-column page.

### 11.2 Test layers

- **Unit:** DOM extraction, element inventory, tile stitching and overlap de-duplication, score parsing (a table of ~20 real-world score strings, including percentage vs absolute and the `12/20` form), action schema validation, budget accounting.
- **Integration (no model):** drive the fixture site with a **stub provider** that returns canned actions. This must pass in CI without any API key.
- **Integration (with model):** marked `@pytest.mark.live`, skipped unless a key is present. Asserts end-to-end that the fixture module reaches its score target.
- **Manual:** the owner's real site, run by the owner.

**Accuracy target for acceptance:** on the fixture question sets, ≥95% correct answers over 20 questions via the DOM path, and ≥90% via the forced-OCR path. Report the measured figure — do not claim a target met without the numbers.

---

## 12. Safety rails (required despite full autonomy)

Full autonomy means *no approval prompts*, not *no limits*. It is a general-purpose tool that will point at other sites later.

### 12.1 Budgets
Per run: max steps, max wall-clock minutes, max model calls, max estimated cost in USD (from token usage × configured per-provider rates). Exceeding any of them stops the run with a clear outcome. Show the running estimate in the status strip.

### 12.2 Scope limits
- **Domain allow-list** per task (default: the start URL's registrable domain plus any explicitly listed). Navigating outside it stops the run. This stops one bad click from wandering off into the user's webmail in the same browser profile.
- **Blocked actions:** never click anything whose accessible name matches a configurable destructive pattern (`delete`, `remove`, `cancel subscription`, `pay`, `purchase`, `confirm payment`, `deactivate`, `close account`). Default-on; the pattern list is editable in settings.
- **Never enter payment details.** No card fields, ever.
- **Stop on CAPTCHA / bot-check / unexpected login wall** and report it. Do not attempt to work around any of them.

### 12.3 Data handling
- API keys in Windows Credential Manager via `keyring`; never in config files, never in logs.
- Screenshots and page text may contain personal data. Keep run artefacts local, under `%LOCALAPPDATA%\Spark\runs\`, and add a "delete runs older than N days" setting (**[ASSUMPTION]** default 30).
- **Redact** password-type input values and anything matching common secret patterns before writing text to a log or sending it to a model.

### 12.4 Kill switch
Stop button, plus a global hotkey (**[ASSUMPTION]** `Ctrl+Alt+Shift+S`). Immediately halts the loop; does not close the browser.

---

## 13. Run artefacts

Each run writes `%LOCALAPPDATA%\Spark\runs\<timestamp>-<task>\`:

- `run.jsonl` — one JSON object per step: step index, page URL, page type, text source, action taken, expectation, verification result, model usage, latency.
- `screenshots/` — **default: question and score pages only** (the owner's choice: the images that actually explain a wrong answer, without hundreds of files per run). Configurable to all steps (useful while debugging the tool itself) or off entirely.
- `memory.json` — final memory store, including every passage and every Q&A with its citation.
- `report.md` — human-readable summary: outcome, iterations, final score vs target, questions answered and accuracy if the site reveals it, total cost, errors.
- `task.yaml`, resolved settings (**keys redacted**), and `PROMPT_VERSION`.

---

## 14. Milestones and acceptance criteria

Work in this order. Each milestone must be independently demonstrable — do not build the whole thing and then debug it.

| # | Milestone | Done when |
| --- | --- | --- |
| **M0** | Scaffolding: repo layout, `pyproject.toml`, config model, logging, `python -m spark` prints version and settings path. | Clean install into a fresh venv works; tests run. |
| **M1** | **Fixture site** (§11.1) served locally. | All pages render, including the canvas, lazy, login and cumulative-score variants. |
| **M2** | Chrome launcher + CDP attach, with the §2.1 health check, **plus the one-time sign-in flow**. | From a cold start, Spark launches Chrome on the automation profile, verifies the port, attaches, and reports the page title. Killing the port produces the named error, not a hang. After signing in once by hand on the fixture login page, a later run finds the session still valid; if it has expired, Spark pauses and says so rather than failing obscurely. |
| **M3** | DOM extraction + element inventory. | On the fixture passage page, full passage text and a correctly-named `Next` button are reported; iframe variant works. |
| **M4** | Capture + OCR: **`windows` engine, `vision` escalation (§6.4.1)**, tall-page tiling and overlap de-dup. | The canvas passage is read with ≥95% word accuracy against the known source text; a >8000 px page is tiled and stitched with no duplicated sentences; the two-column trap page triggers escalation to `vision` and is then read correctly; a missing OCR language pack produces the named warning, not garbage. |
| **M5** | Provider layer with two providers + structured output + usage accounting. | Same task runs end to end on two different providers by changing one setting. |
| **M6** | Action execution + verification + orchestrator loop with stall detection. | Spark navigates the fixture flow from passage to questions unaided. |
| **M7** | Grounded question answering with memory. | ≥95% correct on the fixture set via DOM, ≥90% via forced OCR, with citations recorded. |
| **M8** | Score extraction (cumulative points), target comparison, loop-until-target via the score-page button, all budget guards. | Fixture run stops on the **first** reading at or above target — with a test proving readings are not summed on top of an already-cumulative score; the continue button is found despite its non-obvious label; a deliberately unreachable target stops cleanly at `max_iterations` with "target not reached"; a score that goes *down* is flagged, not counted as progress. |
| **M9** | GUI: start/stop, live log, settings, run history, responsive cancel. | Owner can run a task end to end without touching a terminal; Stop reacts within ~2 s. |
| **M10** | Script record/replay with AI fallback. | A recorded script replays the fixture flow with zero model calls for navigation; breaking a selector triggers fallback and the run still completes. |
| **M11** | Run reports + retention. | `report.md` is readable and accurate. |
| **M12** | PyInstaller build + `docs/USAGE.md`. | A single `.exe` runs on a Windows machine without Python installed; usage doc covers first-run profile setup. |

---

## 15. Things to verify before relying on them

Do not take these on trust from this document — confirm each against a primary source at implementation time, and correct this spec if it is wrong:

1. The exact Chrome version and current behaviour of the `--remote-debugging-port` / default-profile restriction — https://developer.chrome.com/blog/remote-debugging-port.
2. `connect_over_cdp` semantics and limitations in the Playwright Python version you pin — https://playwright.dev/python/docs/api/class-browsertype.
3. Current model IDs, context limits, vision support and per-token pricing for every provider you implement — each provider's own official documentation. Do not hard-code model IDs from memory.
4. `Windows.Media.Ocr` availability via the `winsdk` package on the target Windows build, and whether the required OCR language pack is installed.
5. Chrome's maximum screenshot dimensions for `captureBeyondViewport` on the target version — this sets the tiling threshold in §2.2.

---

## 16. Requirements confirmed by the owner, and what is still open

All of §16's original questions have been answered. Recorded here so the reasoning behind the defaults is not lost:

| Question | Owner's answer | Where it lands |
| --- | --- | --- |
| Score format | **Plain points number**, no visible maximum | §6.10 — compare as points, never derive a percentage |
| Score behaviour | **Cumulative** across rounds | §6.10 — compare the latest reading; do **not** sum |
| Next round | **Click a button on the score page** (label unknown) | §6.10 `loop_action`, with label auto-detection and caching |
| Login | **Yes, account required** | §6.11 — one-time manual sign-in in the Spark profile |
| Provider | **Gemini** — wants free | §5.1, with free-tier caveats |
| OCR | **Windows OCR first, AI as backup** | §6.4 + the escalation rule in §6.4.1 |
| Run artefacts | **Key screenshots + full text log** | §13 — question and score pages only |

### Still genuinely unknown — handle defensively, do not block on them

1. **The continue button's label.** Unknown, so it is auto-detected and cached (§6.10). The fixture site deliberately uses an unobvious label so this path is tested rather than accidentally passing.
2. **How often the login session expires.** Unknown, so mid-run expiry is treated as possible: detect the login wall, pause, prompt, resume (§6.11). Never fail obscurely, never attempt to log in.
3. **The points target value.** Goes in the task file (`stop.score_target`) — the owner sets it per task; no default is meaningful.
4. **Whether the passage is genuinely unreadable from the DOM.** The owner was unsure, which is why the hybrid exists. **Report the measured answer after the first real run** — the `text_source` field in the run log settles it. If the DOM path works, OCR is a safety net that rarely fires and the free-tier arithmetic gets much easier.
5. **Run artefact retention.** **[ASSUMPTION]** 30 days, configurable (§12.3). Not raised with the owner; flag it in the first progress report.
6. **Whether the free tier's limits are sufficient in practice.** Depends on rounds-to-target and escalation rate, both unknown until a real run. Instrument it (§5.1) and report actual call counts rather than predicting them.

### The three places this will most likely go wrong

Stated plainly so they get attention in review, not after a wasted day:

1. **Chrome silently ignoring the debug port** on the default profile (§2.1). Mitigated by the mandatory health check — do not weaken it into a retry loop.
2. **Reading only the visible viewport** of a page that is taller than the screen (§2.2), producing confident answers from a fraction of the passage.
3. **Summing an already-cumulative score** (§6.10), which stops the run early and looks like success.
