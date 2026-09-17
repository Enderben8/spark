"""Tesseract OCR engine — optional, offline, geometry-capable.

See BUILD_SPEC.md §6.4. Ships as an install-on-demand extra
(``pip install spark[tesseract]``) because it also needs the separate
Tesseract binary installed on the machine; most users will use the
``windows`` engine instead (BUILD_SPEC §16 — free, already present on
Windows). This engine mainly matters as a geometry-capable fallback for
non-Windows dev/test environments, since it can run in this project's own
Linux sandbox where ``windows`` cannot.
"""
from __future__ import annotations

from PIL import Image

from spark.logsetup import get_logger
from spark.perception.ocr.base import OcrBlock, OcrEngineUnavailable, OcrResult

log = get_logger("perception.ocr.tesseract")


class TesseractOcrEngine:
    name = "tesseract"
    provides_geometry = True

    async def read(self, image: Image.Image) -> OcrResult:
        try:
            import pytesseract
        except ImportError as exc:
            raise OcrEngineUnavailable(
                "pytesseract is not installed. Install with `pip install spark[tesseract]` "
                "and ensure the Tesseract binary itself is on PATH."
            ) from exc

        try:
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrEngineUnavailable(
                "The pytesseract Python package is installed, but the Tesseract binary "
                "itself was not found on PATH. Install it separately (e.g. "
                "`apt install tesseract-ocr` / the Windows installer from the Tesseract "
                "project) and ensure it is on PATH."
            ) from exc

        blocks: list[OcrBlock] = []
        words: list[str] = []
        confidences: list[float] = []
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            conf_raw = data["conf"][i]
            try:
                conf = float(conf_raw)
            except (TypeError, ValueError):
                conf = -1.0
            words.append(text)
            bbox = {
                "x": float(data["left"][i]),
                "y": float(data["top"][i]),
                "w": float(data["width"][i]),
                "h": float(data["height"][i]),
            }
            block_confidence = conf / 100.0 if conf >= 0 else None
            if block_confidence is not None:
                confidences.append(block_confidence)
            blocks.append(OcrBlock(text=text, bbox=bbox, confidence=block_confidence))

        overall_confidence = sum(confidences) / len(confidences) if confidences else None
        return OcrResult(
            text=" ".join(words),
            blocks=blocks,
            engine=self.name,
            confidence=overall_confidence,
        )
