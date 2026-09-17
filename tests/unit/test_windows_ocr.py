"""Unit tests for WindowsOcrEngine (spark.perception.ocr.windows).

This sandbox has no Windows and no real ``winsdk``/``Windows.Media.Ocr``, so
these tests mock the entire ``winsdk`` surface that ``windows.py`` imports
(``winsdk.windows.{globalization,graphics.imaging,media.ocr,storage.streams}``)
by injecting fake modules into ``sys.modules`` before calling ``read()`` —
the import happens lazily inside ``read()`` (see ``windows.py``'s
``_import_winsdk``), which is exactly what makes this injection point work
without needing to patch ``windows.py`` itself.

See ``spark/perception/ocr/WINDOWS_OCR_NOTES.md`` for what is and is not
verified against a real Windows machine — these tests only prove the
module's own logic (control flow, bbox/text assembly, error handling)
behaves as intended against a plausible mock of the documented API surface,
not that the real winsdk API actually looks like this mock.
"""
from __future__ import annotations

import sys
import types

import pytest
from PIL import Image

from spark.perception.ocr.base import OcrEngineUnavailable
from spark.perception.ocr.windows import WindowsOcrEngine


class _Rect:
    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _Word:
    def __init__(self, text: str, rect: _Rect) -> None:
        self.text = text
        self.bounding_rect = rect


class _Line:
    def __init__(self, text: str, words: list[_Word]) -> None:
        self.text = text
        self.words = words


class _OcrResult:
    def __init__(self, text: str, lines: list[_Line]) -> None:
        self.text = text
        self.lines = lines
        self.text_angle = 0.0


class _Language:
    def __init__(self, tag: str) -> None:
        self.language_tag = tag


def _install_fake_winsdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    engine_factory=None,
    available_languages=None,
    recognize_result: _OcrResult | None = None,
    recognize_error: Exception | None = None,
):
    """Inject a fake winsdk module tree into sys.modules.

    ``engine_factory`` is called with no arguments by the fake
    ``OcrEngine.try_create_from_user_profile_languages()`` — return ``None``
    from it to simulate the "no matching language pack" case. Defaults to a
    factory that returns a working fake engine.
    """
    available_languages = available_languages or [_Language("en-US")]

    class _FakeEngine:
        max_image_dimension = 4096

        async def recognize_async(self, bitmap):
            if recognize_error is not None:
                raise recognize_error
            assert bitmap is not None
            return recognize_result if recognize_result is not None else _OcrResult("", [])

    if engine_factory is None:
        engine_factory = lambda: _FakeEngine()  # noqa: E731

    class _FakeOcrEngineClass:
        @staticmethod
        def try_create_from_user_profile_languages():
            return engine_factory()

        available_recognizer_languages = available_languages

    ocr_mod = types.ModuleType("winsdk.windows.media.ocr")
    ocr_mod.OcrEngine = _FakeOcrEngineClass

    class _FakeSoftwareBitmap:
        @staticmethod
        def create_copy_from_buffer(buffer, pixel_format, width, height):
            return ("bitmap", buffer, pixel_format, width, height)

    class _FakeBitmapPixelFormat:
        RGBA8 = "RGBA8"

    imaging_mod = types.ModuleType("winsdk.windows.graphics.imaging")
    imaging_mod.SoftwareBitmap = _FakeSoftwareBitmap
    imaging_mod.BitmapPixelFormat = _FakeBitmapPixelFormat

    class _FakeDataWriter:
        def __init__(self) -> None:
            self._buf = b""

        def write_bytes(self, raw: bytes) -> None:
            self._buf = bytes(raw)

        def detach_buffer(self):
            return self._buf

    streams_mod = types.ModuleType("winsdk.windows.storage.streams")
    streams_mod.DataWriter = _FakeDataWriter

    globalization_mod = types.ModuleType("winsdk.windows.globalization")

    modules = {
        "winsdk": types.ModuleType("winsdk"),
        "winsdk.windows": types.ModuleType("winsdk.windows"),
        "winsdk.windows.globalization": globalization_mod,
        "winsdk.windows.graphics": types.ModuleType("winsdk.windows.graphics"),
        "winsdk.windows.graphics.imaging": imaging_mod,
        "winsdk.windows.media": types.ModuleType("winsdk.windows.media"),
        "winsdk.windows.media.ocr": ocr_mod,
        "winsdk.windows.storage": types.ModuleType("winsdk.windows.storage"),
        "winsdk.windows.storage.streams": streams_mod,
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.mark.asyncio
async def test_successful_recognition_returns_merged_text_and_blocks(monkeypatch):
    lines = [
        _Line("Hello world", [_Word("Hello", _Rect(1, 2, 30, 10)), _Word("world", _Rect(35, 2, 30, 10))]),
        _Line("second line", [_Word("second", _Rect(1, 15, 40, 10)), _Word("line", _Rect(45, 15, 20, 10))]),
    ]
    result = _OcrResult("Hello world\nsecond line", lines)
    _install_fake_winsdk(monkeypatch, recognize_result=result)

    engine = WindowsOcrEngine()
    image = Image.new("RGB", (100, 40), color="white")
    ocr_result = await engine.read(image)

    assert ocr_result.engine == "windows"
    assert ocr_result.text == "Hello world\nsecond line"
    assert ocr_result.confidence is None
    assert [b.text for b in ocr_result.blocks] == ["Hello", "world", "second", "line"]
    first = ocr_result.blocks[0]
    assert first.bbox == {"x": 1.0, "y": 2.0, "w": 30.0, "h": 10.0}
    assert first.confidence is None


@pytest.mark.asyncio
async def test_missing_language_pack_raises_ocr_engine_unavailable(monkeypatch):
    _install_fake_winsdk(
        monkeypatch,
        engine_factory=lambda: None,
        available_languages=[_Language("ja-JP")],
    )

    engine = WindowsOcrEngine()
    image = Image.new("RGB", (50, 50))

    with pytest.raises(OcrEngineUnavailable) as excinfo:
        await engine.read(image)

    message = str(excinfo.value)
    assert "language" in message.lower()
    assert "ja-JP" in message
    assert "Settings" in message  # names the Windows Settings path, per §6.4.1


@pytest.mark.asyncio
async def test_winsdk_not_importable_raises_ocr_engine_unavailable(monkeypatch):
    # Simulate winsdk genuinely not being installed: setting a name to None
    # in sys.modules makes any `import <name>...` raise ImportError.
    monkeypatch.setitem(sys.modules, "winsdk", None)
    for name in list(sys.modules):
        if name == "winsdk" or name.startswith("winsdk."):
            if name != "winsdk":
                monkeypatch.delitem(sys.modules, name, raising=False)

    engine = WindowsOcrEngine()
    image = Image.new("RGB", (10, 10))

    with pytest.raises(OcrEngineUnavailable) as excinfo:
        await engine.read(image)

    assert "winsdk" in str(excinfo.value).lower()
    assert not isinstance(excinfo.value, ImportError)


@pytest.mark.asyncio
async def test_blank_image_with_no_text_returns_empty_result_without_raising(monkeypatch):
    _install_fake_winsdk(monkeypatch, recognize_result=_OcrResult("", []))

    engine = WindowsOcrEngine()
    image = Image.new("RGB", (20, 20), color="white")

    ocr_result = await engine.read(image)

    assert ocr_result.text == ""
    assert ocr_result.blocks == []
    assert ocr_result.engine == "windows"
    assert ocr_result.confidence is None


@pytest.mark.asyncio
async def test_recognize_async_failure_is_wrapped_as_ocr_engine_unavailable(monkeypatch):
    _install_fake_winsdk(monkeypatch, recognize_error=RuntimeError("boom"))

    engine = WindowsOcrEngine()
    image = Image.new("RGB", (10, 10))

    with pytest.raises(OcrEngineUnavailable) as excinfo:
        await engine.read(image)

    assert "boom" in str(excinfo.value)
