import pytest

from spark.perception.ocr.registry import build_engine
from spark.perception.ocr.tesseract import TesseractOcrEngine
from spark.perception.ocr.vision import VisionOcrEngine
from spark.perception.ocr.windows import WindowsOcrEngine


def test_build_windows_engine():
    assert isinstance(build_engine("windows"), WindowsOcrEngine)


def test_build_tesseract_engine():
    assert isinstance(build_engine("tesseract"), TesseractOcrEngine)


def test_build_vision_engine_requires_callable():
    with pytest.raises(ValueError, match="read_image_text"):
        build_engine("vision")


async def _stub(image):
    return "text"


def test_build_vision_engine_with_callable():
    engine = build_engine("vision", read_image_text=_stub)
    assert isinstance(engine, VisionOcrEngine)


def test_build_unknown_engine_raises():
    with pytest.raises(ValueError, match="Unknown OCR engine"):
        build_engine("carrier-pigeon")
