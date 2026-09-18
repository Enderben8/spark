# Using Spark

This is the end-user/operator manual. For the design and implementation
rationale, see [`BUILD_SPEC.md`](BUILD_SPEC.md) — this document assumes
that build is complete and working; it tells you how to run it, not how it
was built.

**Platform:** Windows 10/11. Everything below assumes the packaged
`Spark.exe` (see [Installing](#installing)) or a source checkout run with
`python -m spark` on Windows. Developing or testing on Linux/macOS is
possible (see `docs/BUILD_SPEC.md` §2.1's dev-fallback notes) but is not
the shipped product.

## Contents

- [Installing](#installing)
- [First run: setting up your browser profile](#first-run-setting-up-your-browser-profile)
- [Settings](#settings)
- [Writing a task](#writing-a-task)
- [Running a task](#running-a-task)
- [Reading a run's results](#reading-a-runs-results)
- [Recorded scripts (faster reruns)](#recorded-scripts-faster-reruns)
- [Safety limits](#safety-limits)
- [Troubleshooting](#troubleshooting)

## Installing

Two ways to get Spark running:

**From a built `.exe`** (once one exists — see `packaging/spark.spec` and
its own honesty note: it has been built and smoke-tested only on Linux in
this project's development sandbox, never on a real Windows machine).
Unzip the `Spark` folder anywhere and run `Spark.exe`. There is no
installer; it's a self-contained folder.

**From source**, for development or if no `.exe` has been built yet:

```powershell
git clone <this repo>
cd spark
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[gemini,anthropic,openai,windows-ocr]"
playwright install chromium   # only needed if you want Playwright's own
                               # bundled browser as a fallback; the normal
                               # path finds your system Chrome automatically
python -m spark
```

Pick whichever provider extras you actually plan to use — `gemini`,
`anthropic`, `openai`, `ollama` (no extra needed, it only uses `httpx`),
`windows-ocr` (Windows only), `tesseract`. You need at least one working
LLM provider and Google Chrome installed.

## First run: setting up your browser profile

**Do this before running any task against a site that requires login.**

Spark does not use your everyday Chrome. Since Chrome 136, the debugging
connection Spark needs is silently disabled on your default profile — see
`BUILD_SPEC.md` §2.1 for why — so Spark keeps its own separate,
persistent Chrome profile at `%LOCALAPPDATA%\Spark\chrome-profile`.

1. Open Spark, click **Settings → Browser**, and click
   **"Set up browser profile (sign in)"**.
2. A real, visible Chrome window opens on Spark's own profile.
3. Sign in to your site in that window, normally.
4. Close the dialog. That's it — the session is saved in the profile and
   reused on every future run, exactly like a normal browser remembering
   you're logged in.

Spark never reads, stores, or transmits anything you type into that
window. If your session later expires mid-run, Spark pauses and asks you
to sign in again rather than guessing or failing silently.

## Settings

**AI Provider** — pick Gemini, Anthropic, OpenAI, or Ollama (a local
model, no API key), enter a model name, and save your API key. The key is
stored in the Windows Credential Manager, never in a plain settings file.

> **Model names in this build are placeholders, not current facts.**
> `config.py`'s shipped defaults were deliberately left conservative or
> unverified — see `docs/USAGE.md`'s own `Troubleshooting` section and each
> provider module's docstring in `src/spark/reasoning/providers/`. Before
> your first real run, open your provider's own current model list and set
> the exact model name in Settings. This is not a one-time detail: model
> lineups change every few months.

**Reading (OCR)** — which engine reads text that isn't in the page's HTML
(scanned/canvas-drawn content). Default: `windows` (free, built into
Windows, no API cost), escalating to `vision` (sends the image to your AI
provider) only when the local read looks unreliable — low confidence,
garbled output, or a detected multi-column layout. See `BUILD_SPEC.md`
§6.4.1 for exactly when that escalation fires.

**Browser** — Chrome's location (auto-detected normally), the debug port,
the automation profile folder, and the "Set up browser profile" button
from the previous section.

**Budgets & Safety** — hard ceilings on a run: max iterations, max
runtime, max model calls, max cost. A fully autonomous run still needs
limits — these stop a broken loop from running forever, they are not
approval prompts.

## Writing a task

A task is a small YAML file. Example — reading modules with comprehension
questions, stopping once a cumulative points score reaches a target:

```yaml
name: "Reading module - reach 450"
start_url: "https://example.invalid/module/1"
goal: >
  Read each passage, click Next, and answer the comprehension questions
  correctly. Repeat until the score target is met.

stop:
  score_target: 450
  comparison: ">="
  max_iterations: 25
  max_runtime_minutes: 60

score:
  cumulative: true       # does the site's score add up across rounds?
  is_percentage: false   # a plain points number, not a percentage
  selector: null         # optional: a CSS selector that always contains the score
  regex: null            # optional: a regex to pull the number out of the page

loop_action:
  type: click
  button_text: null      # null = auto-detect ("Next", "Continue", "Carry on", ...)

auth:
  requires_login: true

perception:
  force_ocr: false
  ocr_read_engine: windows

limits:
  max_steps_per_iteration: 60
  max_cost_usd: 2.00
```

Save it under `%LOCALAPPDATA%\Spark\tasks\<name>.yaml` (the GUI's task
dropdown looks there) or anywhere else and open it via "New task..." in
the dropdown.

**The three things worth getting right first:**

- `score.cumulative` — does the number on the score page keep growing
  round after round, or reset each time? Get this backwards and the run
  either stops after one round or never stops at all. See `BUILD_SPEC.md`
  §6.10.
- `stop.max_iterations` / `stop.max_runtime_minutes` — set these to
  something you're comfortable leaving unattended; a target that's never
  actually reachable (a typo, a site change) will otherwise run until one
  of these limits kicks in.
- `start_url` — Spark refuses to navigate outside this URL's domain
  (`BUILD_SPEC.md` §12.2), so make sure the whole flow (passage →
  questions → score → next round) stays on one site.

## Running a task

**GUI:** pick the task from the dropdown, click **Start**. The status
strip shows the current URL, elapsed time, model calls made, estimated
cost, and the last score reading. Click **Stop** to cancel — it reacts
within a second or two, not at the end of whatever step is in flight.

**Command line**, for scripting or a terminal-only workflow:

```powershell
python -m spark run path\to\task.yaml
```

Exits 0 on success, 1 otherwise, and logs the outcome plus where the run's
artefacts were written.

## Reading a run's results

Every run writes to `%LOCALAPPDATA%\Spark\runs\<timestamp>-<task name>\`:

| File | What it is |
| --- | --- |
| `report.md` | Start here — outcome, score history, every question answered with its chosen option and citation, every passage read. |
| `run.jsonl` | One line per page perceived: URL, page type, whether OCR ran, screenshot path if one was taken. |
| `memory.json` | The full structured record behind `report.md`. |
| `screenshots/` | By default, only question and score pages (configurable in Settings → Retention). |
| `task.json`, `settings.json` | Exactly what was run, for reproducing or debugging later. API keys are never in here — see Settings above. |

Runs older than the configured retention window (30 days by default) are
deleted automatically at the start of the next run.

## Recorded scripts (faster reruns)

Every **successful** run automatically saves a replay script to
`%LOCALAPPDATA%\Spark\scripts\<task name>.yaml`. Nothing else needs to
happen for this to work — but understanding what it buys you:

A script replay skips the "what should I click?" model calls for
navigation (clicking Next, clicking the continue button) and only calls
the model for the part that genuinely needs it each time: answering
whatever questions actually appear. If the site changes enough that a
recorded click no longer resolves to anything, Spark automatically falls
back to full AI-driven navigation from wherever it got stuck, rather than
failing outright. There's no separate "replay mode" to turn on today —
this happens automatically the next time the same task runs against a
site Spark has already successfully completed once.

## Safety limits

These apply on every run, always, regardless of settings:

- **Domain lock:** navigating outside the task's starting domain stops
  the run.
- **Blocked actions:** Spark refuses to click anything whose visible
  label matches a destructive pattern — delete, remove, pay, purchase,
  confirm payment, close account, and so on (editable in Settings, default
  list in `BUILD_SPEC.md` §12.2).
- **No payment details, ever.**
- **CAPTCHA / bot-check / unexpected login wall:** the run stops and
  reports it rather than attempting to work around any of them.
- **Budgets:** iterations, runtime, model calls, and cost all have
  configurable ceilings (Settings → Budgets & Safety).

## Troubleshooting

**"Chrome's DevTools port never became available"** — almost always
means Chrome launched on its *default* profile instead of Spark's
automation profile (see [First run](#first-run-setting-up-your-browser-profile)
above and `BUILD_SPEC.md` §2.1). Check Settings → Browser → profile
directory isn't accidentally pointed at your normal Chrome data folder.

**A run stops immediately with "Blocked: login wall..."** — your session
on Spark's profile has expired or was never set up. Redo the
[browser profile setup](#first-run-setting-up-your-browser-profile).

**The score never seems to trigger a stop** — check `score.cumulative`
in your task file matches how the site actually behaves (see
[Writing a task](#writing-a-task)), and check `report.md`'s score history
to see exactly what was read each round.

**A provider call fails with "no API key configured"** — set one in
Settings → AI Provider and click "Save API key". If you're on a fresh
machine with no OS keyring backend available (some bare Linux/CI setups;
not expected on a real Windows install), key storage will fail — this is
a real, sandbox-specific finding recorded in `src/spark/secrets.py`'s own
docstring, not expected in normal use.

**Answers seem wrong / OCR looks garbled** — check `report.md`'s citation
for each answer against the actual passage; if the citation looks
unrelated, the passage may not have been read correctly. Check
`run.jsonl` for `"ocr_used": true` entries and consider setting
`perception.force_ocr: true` temporarily to see if OCR does better than
the DOM read, or vice versa.

**A recorded script keeps falling back to AI mode** — the site changed
enough that recorded selectors stopped matching. This is expected
behaviour, not a bug (see [Recorded scripts](#recorded-scripts-faster-reruns))
— a fresh script is saved automatically the next time a run succeeds.
