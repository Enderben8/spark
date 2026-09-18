"""Full-page and tiled screenshot capture.

See BUILD_SPEC.md §2.2 and §6.4. Two hazards this module exists to handle:

1. A page taller than the viewport: a naive viewport-only screenshot reads a
   fraction of the content. Every capture scrolls the full page once first
   (triggering lazy-loaded content), then either takes one full-page
   screenshot or, past a height threshold, tiles the page with overlap and
   stitches the results.
2. Sticky/fixed headers and footers duplicating into every tile, which both
   wastes OCR/vision budget and can confuse text-based de-duplication.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image
from playwright.async_api import Page

from spark.logsetup import get_logger

log = get_logger("perception.capture")

# [ASSUMPTION] BUILD_SPEC §2.2 point 4: past this height, tile-and-stitch
# instead of one giant screenshot (very tall images degrade OCR/vision
# accuracy, and Chrome has practical limits on captureBeyondViewport size —
# verify that ceiling on the target Chrome version per BUILD_SPEC §15).
MAX_FULL_PAGE_HEIGHT_CSS = 8000
TILE_OVERLAP_FRACTION = 0.15


@dataclass
class Tile:
    image: Image.Image
    scroll_x: float
    scroll_y: float
    device_pixel_ratio: float


@dataclass
class CaptureResult:
    tiles: list[Tile] = field(default_factory=list)
    page_height_css: int = 0
    page_width_css: int = 0
    device_pixel_ratio: float = 1.0
    is_tiled: bool = False


_STICKY_HIDE_SCRIPT = """
() => {
  const fixed = [];
  document.querySelectorAll('*').forEach(el => {
    const style = getComputedStyle(el);
    if (style.position === 'fixed' || style.position === 'sticky') {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        fixed.push(el);
      }
    }
  });
  window.__sparkHiddenSticky = fixed;
  fixed.forEach(el => {
    el.dataset.sparkPrevVisibility = el.style.visibility;
    el.style.visibility = 'hidden';
  });
  return fixed.length;
}
"""

_STICKY_RESTORE_SCRIPT = """
() => {
  const fixed = window.__sparkHiddenSticky || [];
  fixed.forEach(el => {
    el.style.visibility = el.dataset.sparkPrevVisibility || '';
    delete el.dataset.sparkPrevVisibility;
  });
  window.__sparkHiddenSticky = null;
}
"""


async def _scroll_full_page(page: Page) -> None:
    """Scroll to the bottom in viewport-sized steps (BUILD_SPEC §2.2 point 2
    — this is what actually triggers IntersectionObserver-based lazy
    loading), then back to the top.
    """
    await page.evaluate(
        """
        async () => {
          const step = Math.max(window.innerHeight, 200);
          const maxHeight = document.documentElement.scrollHeight;
          for (let y = 0; y < maxHeight; y += step) {
            window.scrollTo(0, y);
            await new Promise(r => setTimeout(r, 120));
          }
          window.scrollTo(0, document.documentElement.scrollHeight);
          await new Promise(r => setTimeout(r, 150));
          window.scrollTo(0, 0);
        }
        """
    )


async def _hide_sticky_elements(page: Page) -> int:
    return await page.evaluate(_STICKY_HIDE_SCRIPT)


async def _restore_sticky_elements(page: Page) -> None:
    await page.evaluate(_STICKY_RESTORE_SCRIPT)


async def capture_page(
    page: Page, *, max_full_page_height: int = MAX_FULL_PAGE_HEIGHT_CSS
) -> CaptureResult:
    """Capture ``page`` as either one full-page screenshot or, past
    ``max_full_page_height``, a series of overlapping viewport tiles.
    Sticky/fixed chrome is hidden for the duration of capture so it doesn't
    duplicate into every tile.
    """
    await _scroll_full_page(page)

    page_height = int(await page.evaluate("() => document.documentElement.scrollHeight"))
    page_width = int(await page.evaluate("() => document.documentElement.scrollWidth"))
    dpr = float(await page.evaluate("() => window.devicePixelRatio || 1"))
    viewport = page.viewport_size or {"width": 1280, "height": 800}

    if page_height <= max_full_page_height:
        hidden_count = await _hide_sticky_elements(page)
        try:
            png_bytes = await page.screenshot(full_page=True)
        finally:
            if hidden_count:
                await _restore_sticky_elements(page)
        image = Image.open(io.BytesIO(png_bytes))
        image.load()
        tile = Tile(image=image, scroll_x=0.0, scroll_y=0.0, device_pixel_ratio=dpr)
        return CaptureResult(
            tiles=[tile],
            page_height_css=page_height,
            page_width_css=page_width,
            device_pixel_ratio=dpr,
            is_tiled=False,
        )

    log.info(
        "Page height %dpx exceeds %dpx; using scroll-and-stitch tiling",
        page_height,
        max_full_page_height,
    )
    hidden_count = await _hide_sticky_elements(page)
    tiles: list[Tile] = []
    try:
        viewport_height = viewport["height"]
        step = max(int(viewport_height * (1 - TILE_OVERLAP_FRACTION)), 1)
        y = 0
        last_y = -1
        while True:
            await page.evaluate("(y) => window.scrollTo(0, y)", y)
            await page.wait_for_timeout(80)
            actual_y = float(await page.evaluate("() => window.scrollY"))
            if actual_y == last_y:
                break  # scrolled as far as the page allows; avoid an infinite loop
            png_bytes = await page.screenshot(full_page=False)
            image = Image.open(io.BytesIO(png_bytes))
            image.load()
            tiles.append(Tile(image=image, scroll_x=0.0, scroll_y=actual_y, device_pixel_ratio=dpr))
            last_y = actual_y
            if actual_y + viewport_height >= page_height:
                break
            y += step
    finally:
        if hidden_count:
            await _restore_sticky_elements(page)
        await page.evaluate("() => window.scrollTo(0, 0)")

    return CaptureResult(
        tiles=tiles,
        page_height_css=page_height,
        page_width_css=page_width,
        device_pixel_ratio=dpr,
        is_tiled=True,
    )


def _longest_boundary_overlap(a: str, b: str, max_check: int = 400) -> int:
    """Length of the longest suffix of ``a`` that matches a prefix of ``b``
    (whitespace-normalised), checked up to ``max_check`` characters each
    side. Used to find how much two overlapping tiles' text has in common at
    the seam, so it isn't duplicated when stitched.
    """
    a_tail = a[-max_check:] if max_check else a
    b_head = b[:max_check] if max_check else b
    for length in range(min(len(a_tail), len(b_head)), 0, -1):
        if a_tail[-length:].strip() == b_head[:length].strip() and a_tail[-length:].strip():
            return length
    return 0


def stitch_tile_texts(texts: list[str]) -> str:
    """Merge text read from consecutive overlapping tiles (BUILD_SPEC §2.2
    point 4), removing the duplicated seam text rather than concatenating
    blindly. De-duplication is text-based, not pixel-based, by design.
    """
    texts = [t for t in texts if t]
    if not texts:
        return ""
    merged = texts[0]
    for nxt in texts[1:]:
        overlap = _longest_boundary_overlap(merged, nxt)
        merged = merged + nxt[overlap:]
    return merged
