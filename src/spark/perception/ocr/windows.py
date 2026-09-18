"""Windows OCR engine — ``Windows.Media.Ocr`` via the ``winsdk`` package.

See BUILD_SPEC.md §6.4 and §6.4.1. This is the default read/geometry engine
(§16 — free, offline, zero API cost, already present on Windows 10/11) and
the distinction that matters (§6.4): *reading* text and *locating a click
target* are different jobs, and this is the engine relied on for the
latter because it reports per-word bounding boxes. Local OCR is fallible in
ways that do NOT lower a confidence score (garbled multi-column text,
unusual fonts) — the §6.4.1 escalation rule that decides when to fall back
to the ``vision`` engine is implemented by the caller (it needs to see
sanity-check failures and layout hints this module doesn't have), not here.
This module's only job is: read the image, and raise
:class:`~spark.perception.ocr.base.OcrEngineUnavailable` loudly for a
genuine "can't run" condition (package missing, no matching OCR language
pack) rather than let that look like a bad read.

**Honesty / verification status (read this before trusting this file):**
This sandbox has no Windows and no display, so none of this has been run
against a real ``Windows.Media.Ocr`` engine. Every API name and behaviour
below is taken from web research against Microsoft's own API reference and
multiple independent, real, published Python wrappers around this exact
API (cited in ``WINDOWS_OCR_NOTES.md`` next to this file) — not from
memory or guesswork — but it is still best-effort. **A human must run this
on a real Windows 10/11 machine with an OCR language pack installed before
it ships.** ``WINDOWS_OCR_NOTES.md`` lists exactly what is
verified-against-documentation vs. still uncertain, including one
important finding: the ``winsdk`` package this module (and
``pyproject.toml``'s ``windows-ocr`` extra) is built against has not been
updated since August 2023, while the ``pywinrt`` project has since moved
its actively-maintained bindings to a different, modular set of
``winrt-*`` packages. The WinRT namespace and API surface used here
(``windows.media.ocr``, ``windows.graphics.imaging``, ...) is the same
either way, so only the top-level import name would need to change if
``winsdk`` itself turns out to be broken — see the notes file.

Approach:

1. Lazy-import ``winsdk`` only inside :meth:`WindowsOcrEngine.read`,
   mirroring the ``tesseract.py`` engine in this package, so the rest of
   the app stays importable on non-Windows dev machines and on Windows
   machines that have not installed the ``spark[windows-ocr]`` extra.
2. Resolve an engine for the user's profile language(s) via
   ``OcrEngine.try_create_from_user_profile_languages()``. If that returns
   ``None``, no installed OCR language pack matches any of the user's
   display languages — raise ``OcrEngineUnavailable`` naming the Windows
   Settings path to install one, per §6.4.1 ("do not let a missing
   language pack present as bad OCR"). This is deliberately a different
   failure than "the engine ran and found nothing".
3. Convert the incoming ``PIL.Image.Image`` to a WinRT ``SoftwareBitmap``
   by writing its raw RGBA bytes into an in-memory WinRT buffer
   (``DataWriter.write_bytes`` → ``detach_buffer``) and handing that buffer
   to ``SoftwareBitmap.create_copy_from_buffer(..., BitmapPixelFormat.RGBA8,
   width, height)``. This is the path used by the real, published
   ``winocr`` PyPI package and by a widely-referenced community gist for
   this exact API (both cited in the notes file) — it is simpler and
   better-evidenced than encoding to PNG and round-tripping through
   ``BitmapDecoder``, which is why this module does not do that, even
   though a PNG+``BitmapDecoder`` round trip is also a documented way to
   get a ``SoftwareBitmap`` and is a reasonable fallback for a human to try
   if the direct-buffer route ever misbehaves on a real machine.
4. ``await engine.recognize_async(bitmap)`` — WinRT async operations are
   directly awaitable from ``winsdk``/``pywinrt`` Python code, no adapter
   needed (per ``pywinrt``'s own docs and every example found).
5. Windows OCR does **not** expose a numeric confidence score anywhere in
   ``OcrResult``/``OcrLine``/``OcrWord`` (confirmed by the official API
   reference and by every third-party wrapper surveyed — none of them
   expose or even mention one). ``OcrBlock.confidence`` and
   ``OcrResult.confidence`` are therefore always ``None`` here — never
   fabricated.
6. Bounding boxes: ``OcrWord.bounding_rect`` is documented as pixels from
   the top-left corner of the *image that was passed in*, so no DPI/unit
   conversion is needed — ``OcrBlock.bbox`` is populated directly from it
   and is already in the image-pixel coordinate space the ``OcrEngine``
   protocol requires. The one documented wrinkle: this mapping is exact
   only while ``OcrResult.text_angle`` is 0 (untilted text, i.e. every
   normal screenshot this app captures); Microsoft's own docs note the
   rect is computed differently once the recognizer detects a rotated
   block of text. This module does not currently special-case a nonzero
   ``text_angle`` — see the notes file.
"""
from __future__ import annotations

from PIL import Image

from spark.logsetup import get_logger
from spark.perception.ocr.base import OcrBlock, OcrEngineUnavailable, OcrResult

log = get_logger("perception.ocr.windows")

_LANGUAGE_PACK_HELP = (
    "Settings > Time & Language > Language & region > Add a language (or "
    "open an already-added language) > Options > install its "
    "'Optical character recognition' feature."
)


class WindowsOcrEngine:
    name = "windows"
    provides_geometry = True

    async def read(self, image: Image.Image) -> OcrResult:
        globalization, imaging, ocr, streams = self._import_winsdk()

        engine = ocr.OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            available = self._describe_available_languages(ocr)
            raise OcrEngineUnavailable(
                "No installed Windows OCR language pack matches any of "
                "this machine's display languages, so Windows.Media.Ocr "
                "cannot run. "
                + (
                    f"Installed OCR language pack(s): {available}. "
                    if available
                    else "No OCR language packs appear to be installed at all. "
                )
                + f"Install one via {_LANGUAGE_PACK_HELP}"
            )

        bitmap = self._to_software_bitmap(image, imaging, streams)

        max_dim = getattr(engine, "max_image_dimension", None)
        if max_dim and max(image.width, image.height) > max_dim:
            log.warning(
                "Image %sx%s exceeds this OcrEngine's max_image_dimension "
                "(%s px); Windows OCR may reject it. Capture-time tiling "
                "(BUILD_SPEC §6.4) should prevent this — this indicates an "
                "oversized capture reached the OCR layer directly.",
                image.width,
                image.height,
                max_dim,
            )

        try:
            result = await engine.recognize_async(bitmap)
        except OcrEngineUnavailable:
            raise
        except Exception as exc:  # genuine engine failure, not "found nothing"
            raise OcrEngineUnavailable(f"Windows.Media.Ocr recognition failed: {exc}") from exc

        return self._to_ocr_result(result)

    @staticmethod
    def _import_winsdk():
        """Import the winsdk submodules this engine needs, lazily.

        Kept as a single seam so tests can inject fakes via ``sys.modules``
        without needing to know the internal control flow of ``read``.
        """
        try:
            import winsdk.windows.globalization as globalization
            import winsdk.windows.graphics.imaging as imaging
            import winsdk.windows.media.ocr as ocr
            import winsdk.windows.storage.streams as streams
        except ImportError as exc:
            raise OcrEngineUnavailable(
                "The 'winsdk' package is not installed, so Windows.Media.Ocr "
                "is unavailable. Install it with `pip install spark[windows-ocr]` "
                "(Windows only). See WINDOWS_OCR_NOTES.md for the current "
                "status of that package."
            ) from exc
        return globalization, imaging, ocr, streams

    @staticmethod
    def _describe_available_languages(ocr) -> str:
        try:
            langs = list(ocr.OcrEngine.available_recognizer_languages)
        except Exception:  # best-effort diagnostic only, never fatal
            return ""
        tags = []
        for lang in langs:
            tag = getattr(lang, "language_tag", None) or str(lang)
            tags.append(tag)
        return ", ".join(tags)

    @staticmethod
    def _to_software_bitmap(image: Image.Image, imaging, streams):
        """PIL image -> WinRT SoftwareBitmap, via a raw RGBA buffer.

        See the module docstring (point 3) for why this route was chosen
        over PNG-encode + BitmapDecoder.
        """
        rgba = image.convert("RGBA")
        width, height = rgba.size
        raw = rgba.tobytes()

        writer = streams.DataWriter()
        writer.write_bytes(raw)
        buffer = writer.detach_buffer()

        return imaging.SoftwareBitmap.create_copy_from_buffer(
            buffer, imaging.BitmapPixelFormat.RGBA8, width, height
        )

    def _to_ocr_result(self, result) -> OcrResult:
        blocks: list[OcrBlock] = []
        lines_text: list[str] = []

        lines = list(getattr(result, "lines", None) or [])
        for line in lines:
            line_text = getattr(line, "text", "") or ""
            if line_text:
                lines_text.append(line_text)
            for word in list(getattr(line, "words", None) or []):
                text = (getattr(word, "text", "") or "").strip()
                if not text:
                    continue
                rect = word.bounding_rect
                bbox = {
                    "x": float(rect.x),
                    "y": float(rect.y),
                    "w": float(rect.width),
                    "h": float(rect.height),
                }
                # Windows.Media.Ocr does not expose a confidence score on
                # OcrWord/OcrLine/OcrResult (see module docstring, point 5)
                # — never fabricate one.
                blocks.append(OcrBlock(text=text, bbox=bbox, confidence=None))

        text = getattr(result, "text", None)
        if not text:
            text = "\n".join(lines_text)

        return OcrResult(text=text or "", blocks=blocks, engine=self.name, confidence=None)
