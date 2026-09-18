"""M6 acceptance tests for the action-execution primitives (BUILD_SPEC.md
§7): real clicks, verified radio state, typing, and scroll-into-view against
real Chromium.
"""
from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from spark.browser.actions import (
    ActionExecutionError,
    click_element,
    scroll_page,
    scroll_to_element,
    type_text,
    verify_checked,
)
from spark.browser.launcher import find_chrome_executable
from spark.perception.dom import extract

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
async def test_click_radio_and_verify_checked_state(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/questions.html?set=1&round=1&nologin=1")
    dom = await extract(page.frames)

    radios = [e for e in dom.elements if e.role == "radio"]
    assert radios, "fixture questions page should have radio inputs"
    target = radios[0]

    assert (await verify_checked(page.frames, target)) is False
    await click_element(page.frames, target)
    assert (await verify_checked(page.frames, target)) is True
    await page.close()


@pytest.mark.asyncio
async def test_click_next_button_navigates(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage.html?nologin=1")
    dom = await extract(page.frames)

    next_buttons = [e for e in dom.elements if "next" in e.name.strip().lower()]
    assert next_buttons
    await click_element(page.frames, next_buttons[0])
    await page.wait_for_load_state("load")
    assert "questions.html" in page.url
    await page.close()


@pytest.mark.asyncio
async def test_type_into_login_form_field(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/login.html")
    dom = await extract(page.frames)

    username_field = next(e for e in dom.elements if e.tag == "input" and "user" in (e.name + e.selector).lower())
    await type_text(page.frames, username_field, "spark-test-user")

    value = await page.eval_on_selector(username_field.selector, "el => el.value")
    assert value == "spark-test-user"
    await page.close()


@pytest.mark.asyncio
async def test_scroll_to_element_brings_offscreen_button_into_view(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 600})
    await page.goto(f"{site_url}/passage.html?nologin=1")
    dom = await extract(page.frames)

    next_buttons = [e for e in dom.elements if "next" in e.name.strip().lower()]
    assert next_buttons
    target = next_buttons[0]
    assert target.in_viewport is False  # bottom-of-page button, page taller than viewport

    await scroll_to_element(page.frames, target)
    in_view = await page.eval_on_selector(
        target.selector,
        "el => { const r = el.getBoundingClientRect(); "
        "return r.top < window.innerHeight && r.bottom > 0; }",
    )
    assert in_view is True
    await page.close()


@pytest.mark.asyncio
async def test_scroll_page_down_changes_scroll_position(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 600})
    await page.goto(f"{site_url}/passage.html?nologin=1")

    before = await page.evaluate("() => window.scrollY")
    await scroll_page(page, direction="down", amount_px=400)
    after = await page.evaluate("() => window.scrollY")
    assert after > before
    await page.close()


@pytest.mark.asyncio
async def test_click_element_with_stale_selector_raises_clear_error(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage.html?nologin=1")
    dom = await extract(page.frames)
    next_button = next(e for e in dom.elements if "next" in e.name.strip().lower())

    # Simulate a stale PageView: the element no longer matches anything real.
    stale = next_button.model_copy(update={"selector": "#this-does-not-exist-anywhere"})
    with pytest.raises(ActionExecutionError):
        await click_element(page.frames, stale, timeout_ms=1000)
    await page.close()
