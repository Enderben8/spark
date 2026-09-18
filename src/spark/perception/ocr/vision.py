"""OCR by asking the configured vision-capable LLM to transcribe an image.

See BUILD_SPEC.md §6.4. This is the escalation engine, not the default read
engine (§16 — owner's choice: free local Windows OCR first, this only when
that looks poor) — but it's also the *only* option for anyone without
Windows OCR available (dev machines, other platforms, no language pack).

Deliberately takes a plain async callable rather than importing
``spark.reasoning`` directly: the provider layer (BUILD_SPEC §6.7) owns
prompting and model selection, this module only needs "give me the text in
this image" and stays usable/testable before or independently of that layer.
General vision models do not reliably report per-word geometry, so this
engine never claims to provide it (``provides_geometry = False``) — a
click that must be aimed at pixel coordinates always needs a geometry
engine (BUILD_SPEC §6.4).
"""
from __future__ import annotations

from typing import Awaitable, Callable

from PIL import Image

from spark.logsetup import get_logger
from spark.perception.ocr.base import OcrEngineUnavailable, OcrResult

log = get_logger("perception.ocr.vision")

ReadImageText = Callable[[Image.Image], Awaitable[str]]

_TRANSCRIBE_INSTRUCTION = (
    "Transcribe ALL text visible in this image, verbatim, in reading order. "
    "Preserve paragraph breaks. Do not summarise, translate, or add any "
    "commentary — output only the transcribed text, nothing else. If the "
    "image contains no readable text, output nothing."
)


class VisionOcrEngine:
    name = "vision"
    provides_geometry = False

    def __init__(self, read_image_text: ReadImageText):
        """``read_image_text`` is supplied by the caller (normally the
        orchestrator, wired to the active LLMProvider's image-transcription
        method) rather than constructed here — see module docstring.
        """
        self._read_image_text = read_image_text

    async def read(self, image: Image.Image) -> OcrResult:
        try:
            text = await self._read_image_text(image)
        except Exception as exc:
            raise OcrEngineUnavailable(f"Vision-model OCR call failed: {exc}") from exc
        text = (text or "").strip()
        return OcrResult(text=text, blocks=[], engine=self.name, confidence=None)


def build_read_image_text_prompt() -> str:
    """The instruction the caller's LLMProvider should use as the prompt
    text alongside the image when implementing the callable passed to
    :class:`VisionOcrEngine`. Exposed as a function (not a bare module
    constant re-export) so provider code can import it without also
    importing PIL-specific typing it doesn't need.
    """
    return _TRANSCRIBE_INSTRUCTION
