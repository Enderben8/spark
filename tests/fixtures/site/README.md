# Spark fixture site

A small static site used by Spark's tests (see `docs/BUILD_SPEC.md` §11.1).
It reproduces, with entirely fictional content, the shape of the real
target site: a login wall, a long reading passage, comprehension questions,
and a cumulative points score with a "continue" loop.

No build step, no dependencies, no external requests. Plain HTML/CSS and a
little inline/`<script src="...">` vanilla JavaScript. Session state lives
in `sessionStorage`, a `document.cookie`, and query-string parameters —
there is no real backend.

## Serving it

From the repository root:

```
python -m http.server 8000 --directory tests/fixtures/site
```

or, from inside `tests/fixtures/site/`:

```
python -m http.server 8000
```

Then open `http://localhost:8000/`.

## The `?nologin=1` bypass

Every passage/question/score page is guarded by `auth-guard.js`: if the
`spark_fixture_session` cookie is absent, the page redirects to
`login.html?next=<original path and query>`.

For tests that don't want to drive the login form first, append
`?nologin=1` to any guarded page's URL (or add `&nologin=1` alongside other
query params) to skip the redirect for that request. This is a
**test-only convenience** — it does not exist on the real target site and
should not be relied on to model real auth behaviour; use it to reach a
page directly when the test isn't specifically exercising the login flow.

`login.html` itself accepts any non-empty username and password — nothing
is actually validated — and on submit sets
`document.cookie = "spark_fixture_session=1; path=/"`, then redirects to
`?next=` (default `passage.html`).

## The cumulative score

`questions.html` scores each round client-side: **20 points per correct
answer** (5 correct = 100), and **adds** that to a single running total
kept in `sessionStorage["spark_fixture_score"]`. The total is never reset
between rounds automatically, so `score.html` always shows the cumulative
sum across every round played in the current browser session/tab, e.g.
`Score: 180`. It is a plain points number with **no visible maximum** —
never a percentage, never `x/y`.

To reset the score for a repeatable test run:

- visit `score.html?reset=1`, or
- click the visible "Reset score" button on `score.html`.

The continue button on `score.html` is deliberately **not** labeled "Next"
or "Continue" — its label is **"Carry on"** — so that button-label
detection is genuinely exercised rather than trivially matched. It links to
`passage.html?round=<next round>`, so the flow repeats indefinitely for
loop-until-target testing.

## Page map

| Page | Purpose |
| --- | --- |
| `index.html` | Links to every page below, for manual/browser exploration. |
| `login.html` | Fake sign-in form; accepts anything non-empty; sets the session cookie. |
| `passage.html` | The reading passage as plain DOM text ("The Founding of Millbrook"), genuinely taller than a full viewport. `Next` button to `questions.html?set=1`. |
| `passage-canvas.html` | Same passage, drawn entirely onto a `<canvas>` with `fillText` — no selectable DOM text for the passage body. The OCR test case. |
| `passage-canvas-condensed.html` | Same passage on `<canvas>`, in a small/condensed font with tight letter-spacing — an OCR trap for naive engines. |
| `passage-canvas-columns.html` | Same passage on `<canvas>`, laid out as two newspaper-style columns — the reading-order OCR trap called out in `BUILD_SPEC.md` §6.4.1 (naive OCR interleaves columns without lowering confidence). |
| `passage-lazy.html` | Same passage, but each paragraph is only inserted into the DOM when scrolled near it (`IntersectionObserver`). An unscrolled capture misses later paragraphs. |
| `passage-iframe.html` | Embeds `passage.html` in a same-origin `<iframe>` instead of rendering the passage directly. |
| `passage-sticky.html` | Same passage with a `position: sticky` header and footer, both showing repeated "Reading Module — Section 1" text, to exercise de-duplication of fixed/sticky chrome across tiled captures. |
| `questions.html?set=1\|2&round=N` | Five multiple-choice questions about the passage. Each option's `<label>` is a sibling of its `<input type="radio">`, not a wrapper around it. `Submit` enables only once all five are answered; submitting adds points to the cumulative score and goes to `score.html`. |
| `score.html?round=N&reset=1` | Shows `Score: <cumulative total>`. `?reset=1` (or the "Reset score" button) zeroes it. "Carry on" continues to `passage.html?round=N+1`. |
| `answer-key.json` | Machine-readable copy of the passage text, both question sets with correct answers, the points-per-correct constant, and the continue-button label — for the test suite to import directly. |

## Shared scripts

- `auth-guard.js` — the login-wall redirect described above.
- `round-nav.js` — computes the current `round` from the query string and
  builds the `questions.html?set=...&round=...` / `passage.html?round=...`
  links (question sets alternate 1, 2, 1, 2, ... by round).
- `passage-data.js` — the passage title/paragraphs as a JS array, shared by
  the plain, lazy, and canvas-rendered passage pages.
- `questions-data.js` — both question sets and `POINTS_PER_CORRECT`, used
  by `questions.html`.
- `canvas-passage.js` — text-wrapping and drawing helpers used by the three
  canvas passage pages (single column, condensed, two columns).
- `style.css` — shared styling, including the sticky header/footer rules.
