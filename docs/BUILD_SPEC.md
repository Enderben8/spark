# Spark — Build Specification

**Audience:** the engineer/model implementing this app. Read the whole document before writing code.
**Status:** requirements locked by the product owner (see §1). Anything not locked is marked **[ASSUMPTION]** — implement as written, but flag it in your first progress report.
**Target platform:** Windows 10/11 desktop (x64).

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
| AI model | **Pluggable** — provider chosen in settings. Gemini, Anthropic Claude, OpenAI and a local Ollama option behind one interface. |
| Autonomy | **Fully autonomous.** Given a task, run to completion without prompting. (Safety rails in §12 are still mandatory — they are budget/scope limits, not approval prompts.) |
| Page reading | **Hybrid**: DOM text first, OCR fallback. Never assume one or the other. |
| Answer policy | **Always select the correct answer**, reasoned from the text actually read. |
| Task input | **Both**: a plain-English goal (AI-driven), and optional saved YAML scripts for deterministic, cheap reruns. |
| Stop condition | **Score-target based**: read the score the site displays, stop when it reaches the configured target. |
| Stack | **Python 3.11+** with a small desktop window (start/stop, live log, settings). |
| Packaging | Runnable from source during development; single-file `.exe` via PyInstaller as the final milestone. |

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
│   │   │   ├── vision.py        # LLM-vision OCR (default)
│   │   │   ├── windows.py       # Windows.Media.Ocr via winsdk (offline)
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
| `vision` (**default**) | Send the image(s) to the configured vision model and ask for verbatim text. Best accuracy on real-world layouts, no extra install, handles multi-column and unusual fonts. | **Unreliable.** Do not trust coordinates from a general vision model. Text only. |
| `windows` | `Windows.Media.Ocr` via `winsdk`. Free, offline, already present on Windows 10/11. | **Yes** — per-word bounding boxes. |
| `tesseract` | Optional, needs a separate installer. | Yes. |

**This distinction is load-bearing.** Reading text and locating a click target are different jobs:

- **Reading the passage** → any engine; `vision` by default.
- **Clicking something that exists only in pixels** (canvas-rendered buttons, image maps) → you need bounding boxes, so you need `windows` or `tesseract`. Map image coordinates to page coordinates as:
  `page_x = tile_scroll_x + (image_x / device_pixel_ratio)`, likewise for y. Get the click in via CDP mouse events at those coordinates.
- **[ASSUMPTION]** Default configuration: `ocr.read_engine = "vision"`, `ocr.geometry_engine = "windows"`. The geometry engine is only invoked when a coordinate-click is actually required.

If a required geometry engine is unavailable, Spark must fail the step with a clear message rather than guessing at coordinates.

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

- **Detection**, in priority order: (1) a user-configured CSS selector or regex from the task file — always wins when present; (2) the page classifier flags a score page and the extract-score prompt returns a value; (3) a regex sweep for common shapes: `12/20`, `60%`, `Score: 12`, `You scored 12 out of 20`.
- **Parse** into `ScoreReading{raw, value, maximum, is_percentage}`. Normalise percentage vs absolute consistently and record which it is — mixing them up is the obvious bug here.
- **Compare** against `target` from the task file, with a configurable comparison (`>=` default).
- **On reaching target:** stop the run, mark it successful, write the report, leave the browser open.
- **On not reaching target:** continue the loop (navigate to the next round / restart the module per the task's `loop_action`).
- **Guard rails** (mandatory — a fully autonomous loop with no ceiling is a defect): stop with a clear "target not reached" outcome when any of `max_iterations` (**[ASSUMPTION]** default 25), `max_runtime_minutes` (default 60), or the cost budget (§12.1) is hit. Also stop if the score **fails to improve** across N consecutive iterations (**[ASSUMPTION]** N=3) — that means something is wrong, and grinding a broken loop is worse than stopping.

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
  score_target: 80
  score_is_percentage: true
  comparison: ">="
  max_iterations: 25
  max_runtime_minutes: 60

score:                      # optional hints; omit to let the AI find it
  selector: "#final-score"
  regex: "Score:\\s*(\\d+)\\s*%"

loop_action:                # what to do when the target is not yet reached
  type: navigate
  url: "https://example.invalid/module/1"

perception:
  force_ocr: false
  ocr_read_engine: vision

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
- `score.html` — shows a score; regenerates a new round on "Try again" so the loop and the target stop condition can be exercised end to end.
- A variant inside an `<iframe>`, and one with a sticky header.

Make the correct answers deterministic and known to the tests, so scoring accuracy can be asserted rather than eyeballed.

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
- `screenshots/` — one capture per step (configurable: all steps / question and score pages only / off).
- `memory.json` — final memory store, including every passage and every Q&A with its citation.
- `report.md` — human-readable summary: outcome, iterations, final score vs target, questions answered and accuracy if the site reveals it, total cost, errors.
- `task.yaml`, resolved settings (**keys redacted**), and `PROMPT_VERSION`.

---

## 14. Milestones and acceptance criteria

Work in this order. Each milestone must be independently demonstrable — do not build the whole thing and then debug it.

| # | Milestone | Done when |
| --- | --- | --- |
| **M0** | Scaffolding: repo layout, `pyproject.toml`, config model, logging, `python -m spark` prints version and settings path. | Clean install into a fresh venv works; tests run. |
| **M1** | **Fixture site** (§11.1) served locally. | All pages render, including the canvas and lazy variants. |
| **M2** | Chrome launcher + CDP attach, with the §2.1 health check. | From a cold start, Spark launches Chrome on the automation profile, verifies the port, attaches, and reports the page title. Killing the port produces the named error, not a hang. |
| **M3** | DOM extraction + element inventory. | On the fixture passage page, full passage text and a correctly-named `Next` button are reported; iframe variant works. |
| **M4** | Capture + OCR (vision engine), including tall-page tiling and overlap de-dup. | The canvas passage is read with ≥95% word accuracy against the known source text; a >8000 px page is tiled and stitched with no duplicated sentences. |
| **M5** | Provider layer with two providers + structured output + usage accounting. | Same task runs end to end on two different providers by changing one setting. |
| **M6** | Action execution + verification + orchestrator loop with stall detection. | Spark navigates the fixture flow from passage to questions unaided. |
| **M7** | Grounded question answering with memory. | ≥95% correct on the fixture set via DOM, ≥90% via forced OCR, with citations recorded. |
| **M8** | Score extraction, target comparison, loop-until-target, all budget guards. | Fixture run stops exactly when the target is reached; separately, a deliberately unreachable target stops cleanly at `max_iterations` with "target not reached". |
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

## 16. Open questions for the owner

Ask these at the first progress report; do not block on them — the defaults above are workable.

1. **OCR engine default.** The choice was not made explicitly (the answer raised the tall-page problem instead). This spec assumes `vision` for reading and `windows` for geometry. Confirm, or switch to `windows`-first if API cost matters more than accuracy.
2. **Score semantics.** Is the target a percentage or an absolute number of correct answers, and is the displayed score cumulative across rounds or per round? This changes the comparison logic in §6.10.
3. **What should happen when the target is not reached** — restart the module, click "Try again", or navigate to a specific URL? (`loop_action` in §9.1.)
4. **Does the site have a login?** If so, the one-time sign-in into the Spark profile (§2.1) needs to be part of the first-run flow.
5. **Run artefact retention** — 30 days assumed; screenshots of every step can accumulate quickly.
