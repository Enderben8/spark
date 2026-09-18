"""The pluggable OCR contract. See BUILD_SPEC.md §6.4.

Reading text and locating a click target are different jobs (this is the
load-bearing distinction in §6.4): any engine can do the former, but only an
engine that reports per-word/per-line bounding boxes (``provides_geometry``)
can be used for a coordinate-based click on canvas-rendered content.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from PIL import Image
from pydantic import BaseModel


class OcrBlock(BaseModel):
    """One recognised span of text, with its bounding box in IMAGE pixel
    coordinates (origin at the top-left of the image passed to ``read``).
    Callers convert to page coordinates using the capture's recorded scroll
    offset and device pixel ratio — see perception/capture.py.
    """

    text: str
    bbox: dict  # {"x": float, "y": float, "w": float, "h": float}
    confidence: float | None = None


class OcrResult(BaseModel):
    text: str
    blocks: list[OcrBlock] = []
    engine: str
    confidence: float | None = None  # overall/mean, when the engine exposes one


@runtime_checkable
class OcrEngine(Protocol):
    name: str
    provides_geometry: bool

    async def read(self, image: Image.Image) -> OcrResult:
        """Read all text from ``image``. Must not raise for an image with no
        text — return an ``OcrResult`` with empty ``text`` instead. Raise
        only for a genuine engine failure (missing language pack, engine
        unavailable, etc.) so callers can distinguish "found nothing" from
        "could not run".
        """
        ...


class OcrEngineUnavailable(RuntimeError):
    """Raised when an engine cannot run at all on this machine (e.g. the
    winsdk package is missing, or no OCR language pack is installed). Per
    BUILD_SPEC §6.4, callers must surface this clearly rather than silently
    falling back to guessed coordinates.
    """
