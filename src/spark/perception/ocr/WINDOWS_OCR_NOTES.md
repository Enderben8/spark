> ## Verified on real Windows 11 (supersedes the "unverified" items below)
>
> Run on Windows 11 / Python 3.12 with `winsdk` 1.0.0b10, en-GB + en-US OCR
> language packs installed:
>
> - `winsdk` **still installs and imports** on Python 3.12 despite being
>   frozen since 2023-08; migrating to `winrt-*` was not needed.
> - The **RGBA8 `SoftwareBitmap.create_copy_from_buffer` route is accepted**
>   directly by `recognize_async` — no BGRA8/premultiplied conversion needed.
> - Recognition of a rendered two-line sentence: **23/23 words**, per-word
>   bounding boxes populated in image-pixel coordinates, confidence `None`
>   (no score is exposed, as researched).
> - `try_create_from_user_profile_languages()` returned an engine;
>   `max_image_dimension` is 10000.
> - Against the fixture site, the **single-column and two-column canvas
>   passages read correctly with no escalation** (the two-column reading-order
>   trap BUILD_SPEC §6.4.1 worried about did not occur with this engine), and
>   the full orchestrator loop and script replay pass using it.
> - Still unverified: rotated text (`text_angle != 0`), and behaviour on a
>   machine with no OCR language pack (covered only by mocked tests).

# Windows OCR (`Windows.Media.Ocr` via `winsdk`) — research notes

Written while implementing `windows.py` (BUILD_SPEC §6.4 / §6.4.1). This
sandbox is Linux with no display, so **none of this was run against a real
Windows machine** — everything below is from web research (Microsoft's own
API reference, plus several independent, real, published Python wrappers
that use this exact API), cross-checked against multiple sources, not from
memory. Anything I could not cross-check is flagged explicitly as
"unverified" below. A human must confirm on a real Windows 10/11 box with
an OCR language pack installed before this ships.

## 1. `winsdk` vs `winrt` — an important, non-obvious finding

The task framing (and this repo's `pyproject.toml`, which already declares
`winsdk>=1.0.0b10; platform_system == 'Windows'` as the `windows-ocr` extra)
treats `winsdk` as "the actively maintained package" and `winrt` as an
older, superseded name. **My research found the opposite is now true, and
I want to flag it clearly rather than quietly go along with the premise:**

- The original `winrt` PyPI package (pre-2022) was a monolithic package,
  later described by its own maintainers as unmaintained.
- `winsdk` (PyPI, [pywinrt/python-winsdk](https://github.com/pywinrt/python-winsdk))
  was published Jan 2022 – Aug 2023 as its community-supported replacement.
  **Its last release is `1.0.0b10`, dated 2023-08-11** — see the version
  history on [PyPI](https://pypi.org/project/winsdk/#history). It has not
  been updated since (over 3 years, as of this writing in Sept 2026).
- Starting **September 2023**, the `pywinrt` project moved to a different,
  *modular* set of packages, one per Windows SDK namespace, confusingly
  reusing the `winrt` name again: `winrt-runtime` plus e.g.
  [`winrt-Windows.Media.Ocr`](https://pypi.org/project/winrt-Windows.Media.Ocr/)
  (latest release **3.2.1, June 2025** — actively maintained, unlike
  `winsdk`), imported as `winrt.windows.media.ocr` (see
  [pywinrt/pywinrt](https://github.com/pywinrt/pywinrt) README's own
  timeline, and [winrt-Windows.Media.Ocr on PyPI](https://pypi.org/project/winrt-Windows.Media.Ocr/)).

So, as of today, the genuinely-current `pywinrt` distribution is the
modular `winrt-*` packages, not `winsdk`. **I implemented against `winsdk`
anyway**, because (a) the task explicitly asked for it, (b) this repo's
`pyproject.toml` already pins to it, and (c) the actual WinRT namespace and
API surface (`windows.media.ocr.OcrEngine`, `windows.graphics.imaging.*`,
etc.) is identical between `winsdk.windows.*` and `winrt.windows.*` — only
the top-level package name differs. **If a human finds `winsdk` doesn't
install or work on a current Windows/Python combination, the fix is almost
certainly: install the modular `winrt-Windows.Media.Ocr` +
`winrt-Windows.Graphics.Imaging` + `winrt-Windows.Storage.Streams` +
`winrt-Windows.Globalization` + `winrt-runtime` packages instead, update
`pyproject.toml`'s `windows-ocr` extra, and change the four `import
winsdk.windows....` lines in `windows.py` to `import winrt.windows....`.**
Everything else in this module should keep working unchanged.

Sources:
- https://pypi.org/project/winsdk/ and https://pypi.org/project/winsdk/#history
- https://github.com/pywinrt/python-winsdk (README/deprecation notice)
- https://github.com/pywinrt/pywinrt (README timeline)
- https://pypi.org/project/winrt-Windows.Media.Ocr/

## 2. Import paths and class names — verified against multiple sources

- `winsdk.windows.media.ocr.OcrEngine`, `OcrResult`, `OcrLine`, `OcrWord`
- `winsdk.windows.globalization.Language`
- `winsdk.windows.graphics.imaging.SoftwareBitmap`, `BitmapPixelFormat`,
  `BitmapAlphaMode`
- `winsdk.windows.storage.streams.DataWriter`

These match the (identical, modulo top package name) `winrt.windows.*`
paths used consistently across:
- The `winocr` PyPI package ([PyPI page](https://pypi.org/project/winocr/),
  [GitHub30/winocr/winocr.py](https://github.com/GitHub30/winocr/blob/main/winocr.py))
- A widely-cited community gist,
  ["WinRT OcrEngine from Python"](https://gist.github.com/dantmnf/23f060278585d6243ffd9b0c538beab2)
- Snyk's per-function usage pages for the `winrt` package, e.g.
  [`OcrEngine.try_create_from_language`](https://snyk.io/advisor/python/winrt/functions/winrt.windows.media.ocr.OcrEngine.try_create_from_language)
  and [`OcrEngine.is_language_supported`](https://snyk.io/advisor/python/winrt/functions/winrt.windows.media.ocr.OcrEngine.is_language_supported)

pywinrt's Python projection consistently turns PascalCase WinRT
methods/properties into `snake_case` (`TryCreateFromLanguage` →
`try_create_from_language`, `RecognizeAsync` → `recognize_async`,
`AvailableRecognizerLanguages` → `available_recognizer_languages`) while
leaving enum members as-is (`BitmapPixelFormat.RGBA8`,
`BitmapAlphaMode.STRAIGHT`) — confirmed across all of the above sources
independently using the same convention.

## 3. Language selection / missing language pack detection

`OcrEngine.try_create_from_user_profile_languages()` (static) returns an
`OcrEngine` if any of the user's Windows display languages resolves to an
installed OCR language pack, or `None` if none do. `windows.py` treats a
`None` return as the §6.4.1 "missing language pack" case and raises
`OcrEngineUnavailable` naming the Settings path, rather than trying to
plough on. It also enumerates `OcrEngine.available_recognizer_languages`
(a list of `Language` objects, each with a `.language_tag` string, e.g.
`en-US`) purely for a better error message, when possible.

Source (independent confirmation of both the method and the property, with
the exact snake_case names): search results summarizing
https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.trycreatefromuserprofilelanguages
and https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.availablerecognizerlanguages,
plus the `winrt`/Snyk pages above. (Direct `learn.microsoft.com` fetches
were blocked by this sandbox's network egress proxy — see §6 below — so
this is via search-result summaries of those pages, not a direct read of
the primary source. Treat as good-confidence but not 100%-verified.)

**Not implemented**: BUILD_SPEC §6.4.1 also asks for a one-time startup
enumeration/warning independent of any particular read. This module only
exposes the failure at `read()` time (per the `OcrEngine` protocol in
`base.py`, which has no separate "startup check" method) — the one-time,
"warn once and remember" behaviour belongs in whatever orchestration layer
constructs/uses `WindowsOcrEngine` across a whole run, not in this
stateless read-only class. Flagging so the human wiring that layer knows
this file does not do it.

## 4. PIL image → `SoftwareBitmap`

Chosen approach: `image.convert("RGBA").tobytes()` → `DataWriter().write_bytes(raw)`
→ `.detach_buffer()` → `SoftwareBitmap.create_copy_from_buffer(buffer,
BitmapPixelFormat.RGBA8, width, height)`.

This is the exact approach used by the real, installable `winocr` PyPI
package (see `GitHub30/winocr/winocr.py`, which the PyPI package is based
on) and independently by the `dantmnf` gist above (which uses `RGBA8` +
`BitmapAlphaMode.STRAIGHT` explicitly). I chose this over the
PNG-encode-then-`BitmapDecoder.create_async`-from-an-
`InMemoryRandomAccessStream` route the task description suggested as "the
realistic path", because:

- it's simpler (no PNG encode/decode round trip, no extra async step), and
- it's the approach actually used in a real, apparently-working,
  general-purpose OCR package with real users, which is stronger evidence
  than my own reasoning about what "should" work.

**Uncertain / worth a human double-check:** Microsoft's own official
sample app for OCR
([`OcrFileImage.xaml.cs`](https://github.com/microsoft/Windows-universal-samples/blob/main/Samples/OCR/cs/OcrFileImage.xaml.cs))
decodes images via `BitmapDecoder.GetSoftwareBitmapAsync(BitmapPixelFormat.Bgra8,
BitmapAlphaMode.Premultiplied)` and passes that straight to
`RecognizeAsync` with no further conversion — i.e. the pixel format
Microsoft's own sample uses is `Bgra8`/`Premultiplied`, not the
`Rgba8`/`Straight` combination `winocr` and this module use. Search results
paraphrasing the API docs said the engine "supports anything convertible
to Gray8", which suggests `Rgba8` should be an accepted input the engine
converts internally, and `winocr` (a real package with real users) uses it
successfully — but I could not fetch the primary
`Windows.Media.Ocr.OcrEngine.RecognizeAsync` remarks page directly (blocked,
§6) to confirm there is no format restriction. **If a human runs this on
real Windows and `recognize_async` throws an argument/format-related
exception, the fix is almost certainly to insert an explicit
`SoftwareBitmap.convert(bitmap, BitmapPixelFormat.BGRA8,
BitmapAlphaMode.PREMULTIPLIED)` call before `recognize_async`, matching the
official sample.** I did not add this pre-emptively because I could not
verify the exact Python method signature for `SoftwareBitmap.convert`'s
overloads with confidence, and did not want to guess at an untested API
call when a simpler, evidence-backed path was available.

## 5. Async calling convention

`winsdk`/`pywinrt` WinRT async operations (`IAsyncOperation<T>` /
`IAsyncAction`) are directly awaitable from an `async def` — no adapter or
wrapper needed: `result = await engine.recognize_async(bitmap)`. Confirmed
by:
- `pywinrt`'s own "WinRT type system" docs (via search-result summary —
  direct fetch of `pywinrt.readthedocs.io` was blocked by this sandbox's
  proxy), which give `await synth.synthesize_text_to_stream_async(...)` as
  the pattern, and mention `.completed` (callback) and `.wait()` (blocking)
  as the non-asyncio alternatives.
- The real `winocr` package, whose public API is `async def
  recognize_pil(img, lang)` that internally awaits `recognize_async`.

**Unverified**: whether any COM apartment / event-loop initialization is
needed beyond a normal `asyncio` event loop (e.g. does the first WinRT call
on a thread need to happen a particular way). I found no source suggesting
extra setup is required for `winsdk` specifically (unlike some older
COM/pythonnet-based approaches), and the real packages above call it from
plain `asyncio.run()`/`await` with no visible extra init — but this is the
single area I'd most want a human to watch for at runtime (e.g. a run
raised on the very first call rather than every call would point here).

## 6. `OcrResult` / `OcrLine` / `OcrWord` fields, units, and confidence

- `OcrResult.text` — the full recognized text (already newline/space
  merged by the engine).
- `OcrResult.lines` — list of `OcrLine`.
- `OcrResult.text_angle` — detected rotation of the text block (not
  currently used by `windows.py`; see caveat below).
- `OcrLine.text`, `OcrLine.words` — list of `OcrWord`.
- `OcrWord.text`, `OcrWord.bounding_rect` — a `Windows.Foundation.Rect`
  (`.x`, `.y`, `.width`, `.height`).

**Units**: `OcrWord.BoundingRect` is documented as "the position and size,
**in pixels**, of the recognized word from the top-left corner of the
image" when `TextAngle` is 0 — i.e. it is already in the pixel coordinate
space of the `SoftwareBitmap`/`PIL.Image.Image` passed in, with no DPI or
device-pixel-ratio conversion needed. `windows.py` copies `x`/`y`/`width`/
`height` straight into `OcrBlock.bbox`. **Caveat, not currently handled**:
per the same documentation, when `TextAngle` is non-zero the rect's
`left`/`top` are "calculated from the rotated image", which the docs don't
fully spell out in the search-result summaries I could get — this could
mean the bbox is no longer a simple axis-aligned box in the *original*
image's coordinate space for skewed text. Ordinary browser screenshots
should essentially always have `text_angle == 0`, so this is a low-risk
edge case, but it is not verified and is worth a human's attention if this
engine is ever pointed at rotated/skewed captures.

(This "pixels, top-left origin, when TextAngle==0" fact is via
search-result summaries of
https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrword.boundingrect
— direct fetch was blocked, see below — so treat as good-confidence, not
verbatim-quoted.)

**Confidence — the important negative finding, stated plainly as the task
asked**: I found **no confidence score anywhere** in
`OcrResult`/`OcrLine`/`OcrWord` in any source consulted — not in the
Microsoft API reference summaries, not in the `winocr` package's
documented field list, not in the `dantmnf` gist's field list, not in the
`GitHub30/winocr` source. Unlike Tesseract, Windows OCR does not report a
numeric confidence at all. Accordingly `windows.py` always sets both
`OcrBlock.confidence` and `OcrResult.confidence` to `None` — it does not
invent a number. This matters for BUILD_SPEC §6.4.1 point 1 (the
confidence-threshold escalation trigger): that trigger can never fire for
the `windows` engine, so the caller implementing §6.4.1 must rely on the
other triggers (sanity check, multi-column detection, unanswerable
question) for this engine, exactly as §6.4.1 point 3 already anticipates
("it does so *without* lowering its confidence, which is why this is a
separate trigger").

## 7. Network access limitations hit while researching

`learn.microsoft.com` and `pywinrt.readthedocs.io` were both blocked by
this sandbox's egress proxy ("Access to ... is blocked by the network
egress proxy"), which is why several citations above are to search-result
summaries of those pages (via `WebSearch`) rather than to a direct
`WebFetch` read of the primary source. Everything load-bearing was
cross-checked against at least one independently-written, real, working
third-party package (`winocr`, the `dantmnf` gist, or `GitHub30/winocr`)
rather than relied on from a single summarized page, but a human with
actual `learn.microsoft.com` access should skim the primary
`OcrEngine`/`OcrResult`/`OcrWord`/`SoftwareBitmap` pages before shipping.

## Summary: what still needs verification on real Windows

1. **Whether `winsdk` (frozen since Aug 2023) still installs/works at all**
   on a current Windows + Python combination, vs. needing the migration to
   the modular `winrt-*` packages described in §1.
2. Whether `SoftwareBitmap.create_copy_from_buffer(..., RGBA8, ...)` is
   accepted directly by `recognize_async`, or whether an explicit
   `SoftwareBitmap.convert(..., BGRA8, PREMULTIPLIED)` step (matching
   Microsoft's own sample) is required — §4.
3. Whether any extra async/COM setup is needed beyond plain `asyncio`
   `await` for the very first WinRT call in a process — §5.
4. The exact behaviour of `OcrWord.bounding_rect` when `OcrResult.text_angle
   != 0` — §6.
5. The one-time-per-run "warn about missing language pack" behaviour
   BUILD_SPEC §6.4.1 asks for is NOT implemented in this stateless
   `read()`-only class — needs to be added by whatever layer constructs it
   for a whole run — §3.
