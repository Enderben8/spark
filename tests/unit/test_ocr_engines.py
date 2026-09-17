import sys

import pytest
from PIL import Image

from spark.perception.ocr.base import OcrEngineUnavailable
from spark.perception.ocr.vision import VisionOcrEngine, build_read_image_text_prompt


@pytest.mark.asyncio
async def test_vision_engine_wraps_callable_result():
    async def fake_read(image: Image.Image) -> str:
        assert isinstance(image, Image.Image)
        return "  Some transcribed text.  "

    engine = VisionOcrEngine(fake_read)
    result = await engine.read(Image.new("RGB", (10, 10)))
    assert result.text == "Some transcribed text."
    assert result.engine == "vision"
    assert result.blocks == []
    assert engine.provides_geometry is False


@pytest.mark.asyncio
async def test_vision_engine_wraps_callable_failure():
    async def failing_read(image: Image.Image) -> str:
        raise RuntimeError("provider timed out")

    engine = VisionOcrEngine(failing_read)
    with pytest.raises(OcrEngineUnavailable, match="provider timed out"):
        await engine.read(Image.new("RGB", (10, 10)))


def test_transcribe_prompt_is_nonempty_and_forbids_commentary():
    prompt = build_read_image_text_prompt()
    assert "verbatim" in prompt.lower()
    assert len(prompt) > 20


@pytest.mark.asyncio
async def test_tesseract_engine_reports_unavailable_when_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    from spark.perception.ocr.tesseract import TesseractOcrEngine

    engine = TesseractOcrEngine()
    with pytest.raises(OcrEngineUnavailable, match="pytesseract is not installed"):
        await engine.read(Image.new("RGB", (10, 10)))
