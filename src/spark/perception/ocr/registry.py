"""Builds an :class:`~spark.perception.ocr.base.OcrEngine` from the engine
name strings used in :class:`spark.config.OcrSettings` (BUILD_SPEC.md §6.4).

The ``vision`` engine needs a ``read_image_text`` callable wired to the
active :mod:`spark.reasoning` provider — this module doesn't import the
provider layer itself (see perception/ocr/vision.py's docstring for why),
so the caller (the orchestrator, once M5/M6 exist) supplies it.
"""
from __future__ import annotations

from spark.perception.ocr.base import OcrEngine
from spark.perception.ocr.tesseract import TesseractOcrEngine
from spark.perception.ocr.vision import ReadImageText, VisionOcrEngine
from spark.perception.ocr.windows import WindowsOcrEngine


def build_engine(name: str, *, read_image_text: ReadImageText | None = None) -> OcrEngine:
    if name == "windows":
        return WindowsOcrEngine()
    if name == "tesseract":
        return TesseractOcrEngine()
    if name == "vision":
        if read_image_text is None:
            raise ValueError(
                "the 'vision' OCR engine requires a read_image_text callable "
                "(wire it to the active LLMProvider's image-transcription method)"
            )
        return VisionOcrEngine(read_image_text)
    raise ValueError(f"Unknown OCR engine: {name!r}")
