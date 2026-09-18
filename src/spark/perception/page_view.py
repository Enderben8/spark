"""Merges DOM extraction and (when needed) OCR into one ``PageView`` — the
single object handed to the Reasoner. See BUILD_SPEC.md §6.5 (the merge
policy) and §6.4.1 (the OCR escalation rule).

The core rule, stated in the spec and worth restating here because it is
easy to get subtly wrong: DOM extraction always runs (it's nearly free);
OCR runs only when the DOM result looks insufficient (or OCR was forced);
and when both exist, they are never blindly concatenated — duplicated,
misaligned passage text is worse than picking one source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from PIL import Image
from playwright.async_api import Page
from pydantic import BaseModel, ConfigDict, Field

from spark.config import OcrSettings
from spark.logsetup import get_logger
from spark.perception.capture import capture_page, stitch_tile_texts
from spark.perception.dom import DomExtractionResult, InteractiveElement, TextBlock, extract
from spark.perception.ocr.base import OcrBlock, OcrEngine, OcrEngineUnavailable, OcrResult

log = get_logger("perception.page_view")

# Below this many visible DOM characters, treat the page as needing OCR.
# BUILD_SPEC §6.5 originally assumed 200, but that was wrong: a short QUESTION
# page has only ~140 characters of perfectly good, exact DOM text, and a
# threshold of 200 made Spark discard it in favour of OCR — which then
# misread "Who" as "VVho". Found running button-style question pages on real
# Windows. Genuinely image-based pages (canvas/img/embed) are caught
# separately by MEDIA_DOMINANCE_THRESHOLD below, so this only has to catch
# "essentially no text at all", e.g. nav chrome around an otherwise blank page.
DOM_TEXT_MIN_CHARS = 50
# [ASSUMPTION]: above this canvas/img area ratio, treat the page as
# image-dominant regardless of character count (a page can have a handful
# of DOM chars in nav chrome around an otherwise all-canvas body).
MEDIA_DOMINANCE_THRESHOLD = 0.30


class PageView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    title: str
    text: str
    text_source: Literal["dom", "ocr", "dom+ocr"]
    elements: list[InteractiveElement]
    # Populated from the DOM pass only (never OCR) — used by
    # skills/answering.py to find a question's text by proximity to its
    # option elements. Not merged into `text`; not meant to be read by
    # prompts directly (BUILD_SPEC §6.8 wants a compact element list, not
    # raw block dumps).
    dom_text_blocks: list[TextBlock] = Field(default_factory=list)
    screenshot_paths: list[str] = Field(default_factory=list)
    page_height_css: int
    page_width_css: int
    device_pixel_ratio: float
    captured_at: datetime

    # Diagnostics beyond the spec's bare PageView fields — every OCR
    # decision this build makes is meant to be inspectable in a run log
    # (BUILD_SPEC §13), not just implied by the final `text`.
    media_dominance_ratio: float = 0.0
    ocr_used: bool = False
    ocr_trigger_reason: str | None = None
    ocr_engine_used: str | None = None
    ocr_escalated: bool = False
    ocr_escalation_reasons: list[str] = Field(default_factory=list)
    ocr_text: str | None = None  # raw OCR output, even when not chosen as the primary text
    tiles_captured: int = 1


def _dom_needs_ocr(dom: DomExtractionResult, settings: OcrSettings) -> tuple[bool, str]:
    if settings.force_ocr:
        return True, "ocr.force_ocr is enabled in settings"
    if dom.text_char_count < DOM_TEXT_MIN_CHARS:
        return True, f"DOM visible text below threshold ({dom.text_char_count} < {DOM_TEXT_MIN_CHARS} chars)"
    if dom.media_dominance_ratio > MEDIA_DOMINANCE_THRESHOLD:
        return True, f"page is canvas/img-dominant (ratio={dom.media_dominance_ratio:.2f})"
    return False, ""


_VOWELS = set("aeiouAEIOU")


def _looks_garbled(text: str) -> bool:
    """A cheap, dictionary-free sanity check for OCR garbage: real English
    words overwhelmingly contain a vowel once they're a few letters long.
    This is a coarse heuristic, not a language model — it exists to catch
    obviously-broken OCR output (symbol soup, misrecognised glyphs), not to
    grade prose quality. Calibrate ``0.7`` against real OCR failures once
    the tool has run against the owner's actual site (BUILD_SPEC §16).
    """
    tokens = [t for t in re.findall(r"[A-Za-z]+", text) if len(t) >= 4]
    if len(tokens) < 5:
        return False  # not enough signal either way
    plausible = sum(1 for t in tokens if any(c in _VOWELS for c in t))
    return (plausible / len(tokens)) < 0.7


def _has_sentence_punctuation(text: str) -> bool:
    return bool(re.search(r"[.!?]", text))


def _looks_multi_column(blocks: list[OcrBlock], image_width: float) -> bool:
    """Heuristic for the two-column reading-order trap called out by name in
    BUILD_SPEC §6.4.1: bucket recognised blocks into horizontal bands, and
    check whether most bands split into two x-position clusters with a
    substantial gap between them roughly in the middle of the image. A
    genuine two-column layout produces exactly this signature; a normal
    single-column paragraph does not.
    """
    if not blocks or image_width <= 0:
        return False
    bands: dict[int, list[OcrBlock]] = {}
    for b in blocks:
        band_key = int(b.bbox.get("y", 0) // 20)
        bands.setdefault(band_key, []).append(b)

    considered = 0
    multi_column = 0
    for band_blocks in bands.values():
        if len(band_blocks) < 2:
            continue
        xs = sorted(b.bbox.get("x", 0) for b in band_blocks)
        gaps = [b2 - b1 for b1, b2 in zip(xs, xs[1:])]
        if not gaps:
            continue
        considered += 1
        biggest_gap = max(gaps)
        gap_index = gaps.index(biggest_gap)
        gap_midpoint = (xs[gap_index] + xs[gap_index + 1]) / 2
        if biggest_gap > image_width * 0.15 and image_width * 0.15 < gap_midpoint < image_width * 0.85:
            multi_column += 1

    if considered < 3:
        return False
    return (multi_column / considered) > 0.4


def _image_is_substantially_blank(image: Image.Image) -> bool:
    try:
        grayscale = image.convert("L")
        histogram = grayscale.histogram()
        total = sum(histogram)
        if total == 0:
            return True
        # Fraction of pixels that are near-white or near-black background.
        background = sum(histogram[:10]) + sum(histogram[-10:])
        return (background / total) > 0.98
    except Exception:  # pragma: no cover - defensive; never block on this
        return False


@dataclass
class OcrOutcome:
    text: str
    engine_used: str
    escalated: bool
    reasons: list[str]


async def _run_ocr_on_tile(
    image: Image.Image,
    *,
    read_engine: OcrEngine,
    escalation_engine: OcrEngine | None,
    settings: OcrSettings,
) -> OcrOutcome:
    result = await read_engine.read(image)
    reasons: list[str] = []
    escalated = False
    engine_used = read_engine.name

    trigger = _escalation_reason(result, image, settings)
    if trigger and escalation_engine is not None and escalation_engine.name != read_engine.name:
        reasons.append(trigger)
        log.info("OCR escalating tile from %s to %s: %s", read_engine.name, escalation_engine.name, trigger)
        try:
            escalated_result = await escalation_engine.read(image)
            result = escalated_result
            engine_used = escalation_engine.name
            escalated = True
        except OcrEngineUnavailable as exc:
            log.warning("Escalation engine %s unavailable, keeping %s result: %s", escalation_engine.name, read_engine.name, exc)
    elif trigger:
        reasons.append(f"{trigger} (no escalation engine configured/available)")

    return OcrOutcome(text=result.text, engine_used=engine_used, escalated=escalated, reasons=reasons)


def _escalation_reason(result: OcrResult, image: Image.Image, settings: OcrSettings) -> str | None:
    if result.confidence is not None and result.confidence < settings.escalation_confidence_threshold:
        return f"low engine-reported confidence ({result.confidence:.2f} < {settings.escalation_confidence_threshold})"
    if len(result.text) < settings.escalation_min_chars and not _image_is_substantially_blank(image):
        return f"suspiciously little text ({len(result.text)} chars) from a non-blank image"
    if len(result.text) >= 200 and _looks_garbled(result.text):
        return "output looks garbled (low proportion of vowel-containing word-like tokens)"
    if len(result.text) >= 200 and not _has_sentence_punctuation(result.text):
        return "no sentence-ending punctuation found in a long result"
    if _looks_multi_column(result.blocks, float(image.width)):
        return "detected a likely multi-column layout (naive OCR often interleaves columns without lowering confidence)"
    return None


async def build_page_view(
    page: Page,
    *,
    ocr_settings: OcrSettings,
    read_engine: OcrEngine,
    escalation_engine: OcrEngine | None = None,
    force_ocr: bool = False,
) -> PageView:
    """Produce one merged :class:`PageView` for the current state of
    ``page``: DOM extraction always, OCR only when needed (or forced).
    """
    dom = await extract(page.frames)

    settings = ocr_settings.model_copy(update={"force_ocr": ocr_settings.force_ocr or force_ocr})
    needs_ocr, reason = _dom_needs_ocr(dom, settings)

    text = dom.text
    text_source: Literal["dom", "ocr", "dom+ocr"] = "dom"
    ocr_text: str | None = None
    ocr_engine_used: str | None = None
    ocr_escalated = False
    ocr_escalation_reasons: list[str] = []
    tiles_captured = 1

    if needs_ocr:
        capture = await capture_page(page)
        tiles_captured = len(capture.tiles)
        tile_texts: list[str] = []
        engines_used: set[str] = set()
        for tile in capture.tiles:
            outcome = await _run_ocr_on_tile(
                tile.image,
                read_engine=read_engine,
                escalation_engine=escalation_engine,
                settings=settings,
            )
            tile_texts.append(outcome.text)
            engines_used.add(outcome.engine_used)
            if outcome.escalated:
                ocr_escalated = True
            ocr_escalation_reasons.extend(outcome.reasons)

        ocr_text = stitch_tile_texts(tile_texts)
        ocr_engine_used = "+".join(sorted(engines_used)) if engines_used else None

        dom_already_sufficient = dom.text_char_count >= DOM_TEXT_MIN_CHARS and dom.media_dominance_ratio <= MEDIA_DOMINANCE_THRESHOLD
        if dom_already_sufficient:
            # OCR only ran because it was explicitly forced; DOM text was
            # fine on its own. Keep DOM as the primary text (BUILD_SPEC
            # §6.5: "never concatenate both blindly") but record that OCR
            # also ran, for diagnostics.
            text = dom.text
            text_source = "dom+ocr"
        else:
            text = ocr_text
            text_source = "ocr"

    return PageView(
        url=page.url,
        title=await page.title(),
        text=text,
        text_source=text_source,
        elements=dom.elements,
        dom_text_blocks=dom.text_blocks,
        page_height_css=dom.page_height_css,
        page_width_css=dom.page_width_css,
        device_pixel_ratio=dom.device_pixel_ratio,
        captured_at=datetime.now(timezone.utc),
        media_dominance_ratio=dom.media_dominance_ratio,
        ocr_used=needs_ocr,
        ocr_trigger_reason=reason or None,
        ocr_engine_used=ocr_engine_used,
        ocr_escalated=ocr_escalated,
        ocr_escalation_reasons=ocr_escalation_reasons,
        ocr_text=ocr_text,
        tiles_captured=tiles_captured,
    )
