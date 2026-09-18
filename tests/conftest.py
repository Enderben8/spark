"""Shared test helpers.

The integration tests were first written in a Linux sandbox, where the only
geometry-capable OCR engine available was Tesseract, so they hardcoded it.
The engine the product actually ships with is ``windows`` (BUILD_SPEC.md
§16), so on Windows the same scenarios must run against THAT engine —
otherwise the suite passes while never exercising the real default.

``platform_ocr_engine_name()`` returns whichever engine is genuinely usable
here; tests that measure Tesseract-specific behaviour use
``requires_tesseract`` instead and skip when it isn't installed.
"""
from __future__ import annotations

import platform
import shutil

import pytest


def _tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


TESSERACT_AVAILABLE = _tesseract_available()

requires_tesseract = pytest.mark.skipif(
    not TESSERACT_AVAILABLE, reason="needs pytesseract plus the Tesseract binary on PATH"
)


def platform_ocr_engine_name() -> str:
    """``windows`` on Windows (the shipped default), else ``tesseract``."""
    return "windows" if platform.system() == "Windows" else "tesseract"


def platform_ocr_engine():
    from spark.perception.ocr.registry import build_engine

    return build_engine(platform_ocr_engine_name())
