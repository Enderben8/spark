"""DOM text extraction and the interactive-element inventory.

See BUILD_SPEC.md §6.3. Two outputs, produced together in one pass per
frame (the actual extraction logic lives in ``_dom_extract.js``, injected via
``frame.evaluate``):

* readable text, paragraph-ish granularity, in document order
* every interactive element, with an accessible name, geometry, and (for
  the common unlabelled-radio case) the nearby text an answer option's
  wording actually lives in

Frames are walked explicitly (``session.frames()``) so content inside an
``<iframe>`` is not silently missed — see the iframe fixture in
tests/fixtures/site.
"""
from __future__ import annotations

from pathlib import Path

from playwright.async_api import Frame
from pydantic import BaseModel

from spark.logsetup import get_logger

log = get_logger("perception.dom")

_EXTRACT_SCRIPT = (Path(__file__).parent / "_dom_extract.js").read_text(encoding="utf-8")


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class TextBlock(BaseModel):
    text: str
    tag: str
    frame_path: str
    bbox: BBox


class ElementState(BaseModel):
    checked: bool = False
    disabled: bool = False
    selected: bool = False


class InteractiveElement(BaseModel):
    id: str  # Spark-assigned, stable within one page view (e.g. "e12")
    frame_path: str
    tag: str
    role: str
    name: str
    group_name: str | None = None  # HTML `name` attribute — groups radio/checkbox options into one question
    text: str
    value: str | None
    state: ElementState
    bbox: BBox
    in_viewport: bool
    selector: str
    nearby_text: str


class DomExtractionResult(BaseModel):
    text: str
    text_char_count: int
    text_blocks: list[TextBlock]
    elements: list[InteractiveElement]
    media_dominance_ratio: float  # 0..1, fraction of visible page area that is canvas/img/embed/object
    page_height_css: int
    page_width_css: int
    device_pixel_ratio: float
    frame_urls: dict[str, str]  # frame_path -> url, for diagnostics


def resolve_frame(frames: list[Frame], path: str) -> Frame | None:
    """Inverse of ``_frame_path``: given a live frame list and a path string
    recorded on an :class:`InteractiveElement`/:class:`TextBlock`, find the
    matching frame. Used by browser/actions.py to dispatch an action to the
    right frame — action execution must re-resolve by path rather than
    caching a Frame object, since a stored path is only meaningful against
    the frame structure at the moment the owning PageView was captured
    (BUILD_SPEC §7: "Resolve element_id against the inventory of the
    CURRENT PageView. Never against a stale one").
    """
    if not frames:
        return None
    main = frames[0]
    for frame in frames:
        if _frame_path(frame, main) == path:
            return frame
    return None


def _frame_path(frame: Frame, main: Frame) -> str:
    if frame == main:
        return "main"
    # Build a path of child indices from the main frame down to this frame.
    chain: list[Frame] = []
    node: Frame | None = frame
    while node is not None and node != main:
        chain.append(node)
        node = node.parent_frame
    chain.reverse()
    parts = []
    parent = main
    for f in chain:
        siblings = parent.child_frames
        try:
            idx = siblings.index(f)
        except ValueError:
            idx = 0
        parts.append(f"iframe[{idx}]")
        parent = f
    return "main>" + ">".join(parts) if parts else "main"


async def extract(frames: list[Frame]) -> DomExtractionResult:
    """Run the extraction script across every frame and merge the results
    into one :class:`DomExtractionResult`.
    """
    if not frames:
        raise ValueError("extract() requires at least the main frame")
    main = frames[0]

    text_blocks: list[TextBlock] = []
    elements: list[InteractiveElement] = []
    frame_urls: dict[str, str] = {}
    total_media_area = 0.0
    total_area = 0.0
    page_height = 0
    page_width = 0
    dpr = 1.0
    next_id = 1

    for frame in frames:
        path = _frame_path(frame, main)
        try:
            raw = await frame.evaluate(_EXTRACT_SCRIPT)
        except Exception as exc:
            # A detached/cross-origin/navigating frame is not fatal to the
            # whole extraction — log and skip it.
            log.debug("Skipping frame %s (%s): %s", path, frame.url, exc)
            continue
        if raw is None:
            continue

        frame_urls[path] = raw.get("url", frame.url)

        for block in raw.get("text_blocks", []):
            text_blocks.append(
                TextBlock(text=block["text"], tag=block["tag"], frame_path=path, bbox=BBox(**block["bbox"]))
            )

        for el in raw.get("elements", []):
            elements.append(
                InteractiveElement(
                    id=f"e{next_id}",
                    frame_path=path,
                    tag=el["tag"],
                    role=el["role"],
                    name=el["name"],
                    group_name=el.get("group_name"),
                    text=el["text"],
                    value=el.get("value"),
                    state=ElementState(**el["state"]),
                    bbox=BBox(**el["bbox"]),
                    in_viewport=el["in_viewport"],
                    selector=el["selector"],
                    nearby_text=el["nearby_text"],
                )
            )
            next_id += 1

        media = raw.get("media", {})
        total_media_area += media.get("media_area", 0.0)
        total_area += media.get("total_area", 0.0)

        if frame == main:
            page_height = int(raw.get("page_height", 0))
            page_width = int(raw.get("page_width", 0))
            dpr = float(raw.get("dpr", 1.0))

    merged_text = "\n\n".join(b.text for b in text_blocks)
    media_ratio = (total_media_area / total_area) if total_area > 0 else 0.0

    return DomExtractionResult(
        text=merged_text,
        text_char_count=len(merged_text),
        text_blocks=text_blocks,
        elements=elements,
        media_dominance_ratio=media_ratio,
        page_height_css=page_height,
        page_width_css=page_width,
        device_pixel_ratio=dpr,
        frame_urls=frame_urls,
    )
