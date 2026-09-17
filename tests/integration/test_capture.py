"""M4 acceptance tests for the capture half of the pipeline (BUILD_SPEC.md
milestone table): tall-page tiling, sticky-chrome hiding, and text-based
seam de-duplication.
"""
from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from spark.browser.launcher import find_chrome_executable
from spark.perception.capture import (
    MAX_FULL_PAGE_HEIGHT_CSS,
    capture_page,
    stitch_tile_texts,
)

SITE_DIR = Path(__file__).parent.parent / "fixtures" / "site"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site_url():
    port = _free_port()
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(SITE_DIR), **kw
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
async def browser():
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True, executable_path=str(find_chrome_executable()), args=["--no-sandbox"]
        )
        try:
            yield b
        finally:
            await b.close()


@pytest.mark.asyncio
async def test_short_page_is_single_untiled_capture(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 2000})
    await page.goto(f"{site_url}/login.html")  # short page

    result = await capture_page(page)

    assert result.is_tiled is False
    assert len(result.tiles) == 1
    assert result.tiles[0].image.width > 0
    await page.close()


@pytest.mark.asyncio
async def test_tall_page_below_threshold_is_full_page_not_tiled(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 800})
    await page.goto(f"{site_url}/passage.html?nologin=1")

    result = await capture_page(page)

    assert result.page_height_css > 1600  # genuinely taller than viewport
    if result.page_height_css <= MAX_FULL_PAGE_HEIGHT_CSS:
        assert result.is_tiled is False
        assert len(result.tiles) == 1
        # full-page screenshot height should roughly match page height (allow DPR scaling)
        assert result.tiles[0].image.height >= result.page_height_css * 0.9
    await page.close()


@pytest.mark.asyncio
async def test_artificially_low_threshold_forces_tiling_with_overlap(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 400})
    await page.goto(f"{site_url}/passage.html?nologin=1")

    result = await capture_page(page, max_full_page_height=900)

    assert result.is_tiled is True
    assert len(result.tiles) >= 3
    # Consecutive tiles must overlap (not simply abut) per the 15% overlap rule.
    ys = [t.scroll_y for t in result.tiles]
    assert ys == sorted(ys)
    for a, b in zip(ys, ys[1:]):
        step = b - a
        assert step < 400  # less than the full viewport height => overlap exists
    await page.close()


@pytest.mark.asyncio
async def test_sticky_chrome_is_hidden_during_capture(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 400})
    await page.goto(f"{site_url}/passage-sticky.html?nologin=1")

    # Sanity: the sticky header is visible before capture starts.
    visible_before = await page.evaluate(
        "() => { const h = document.querySelector('header, .sticky-header'); "
        "return h ? getComputedStyle(h).visibility : null; }"
    )
    assert visible_before != "hidden"

    result = await capture_page(page, max_full_page_height=900)
    assert result.is_tiled is True

    # After capture, visibility must be restored (not left hidden).
    visible_after = await page.evaluate(
        "() => { const h = document.querySelector('header, .sticky-header'); "
        "return h ? getComputedStyle(h).visibility : null; }"
    )
    assert visible_after != "hidden"
    await page.close()


@pytest.mark.asyncio
async def test_lazy_page_is_fully_loaded_before_capture(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 800})
    await page.goto(f"{site_url}/passage-lazy.html?nologin=1")

    result = await capture_page(page)

    # After capture_page's scroll-to-bottom-then-top routine, ALL lazy
    # paragraph slots must have been loaded (checked via the live page,
    # since the capture itself only returns images at this layer).
    total_slots = await page.evaluate("() => document.querySelectorAll('.lazy-slot').length")
    loaded_slots = await page.evaluate("() => document.querySelectorAll('.lazy-slot.loaded').length")
    assert total_slots >= 3
    assert loaded_slots == total_slots
    await page.close()


def test_stitch_tile_texts_removes_overlap():
    a = "The quick brown fox jumps over the lazy dog. Ferns reproduce by spores."
    b = "Ferns reproduce by spores. Not seeds, which is a common misconception."
    merged = stitch_tile_texts([a, b])
    assert merged.count("Ferns reproduce by spores") == 1
    assert "quick brown fox" in merged
    assert "common misconception" in merged


def test_stitch_tile_texts_handles_no_overlap_gracefully():
    a = "First tile text with nothing in common."
    b = "Second tile, totally unrelated content."
    merged = stitch_tile_texts([a, b])
    assert a in merged
    assert b in merged


def test_stitch_tile_texts_single_and_empty():
    assert stitch_tile_texts([]) == ""
    assert stitch_tile_texts(["only one"]) == "only one"
    assert stitch_tile_texts(["", "text", ""]) == "text"
