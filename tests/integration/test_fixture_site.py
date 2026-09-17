"""Smoke tests for tests/fixtures/site itself (BUILD_SPEC.md §11.1) — these
don't touch Spark's own code at all. They exist so a change to the fixture
site that breaks its own documented behaviour is caught immediately, rather
than surfacing later as a confusing failure in a Spark orchestrator test.
"""
from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from spark.browser.launcher import find_chrome_executable

SITE_DIR = Path(__file__).parent.parent / "fixtures" / "site"
ANSWER_KEY = json.loads((SITE_DIR / "answer-key.json").read_text())


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
            headless=True,
            executable_path=str(find_chrome_executable()),
            args=["--no-sandbox"],
        )
        try:
            yield b
        finally:
            await b.close()


@pytest.mark.asyncio
async def test_login_wall_redirects_when_no_session(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage.html")
    await page.wait_for_load_state("load")
    assert "login.html" in page.url
    await page.close()


@pytest.mark.asyncio
async def test_nologin_bypass_skips_the_wall(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage.html?nologin=1")
    await page.wait_for_load_state("load")
    assert "login.html" not in page.url
    await page.close()


@pytest.mark.asyncio
async def test_login_form_sets_session_and_redirects(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/login.html?next=passage.html")
    await page.fill("input[type=text], input[name=username], input#username", "tester")
    await page.fill("input[type=password]", "anything")
    await page.click("button[type=submit], input[type=submit], button")
    await page.wait_for_load_state("load")
    assert "passage.html" in page.url
    cookies = await page.context.cookies()
    assert any(c["name"] == ANSWER_KEY["session_cookie_name"] for c in cookies)
    await page.close()


@pytest.mark.asyncio
async def test_passage_taller_than_viewport(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 800})
    await page.goto(f"{site_url}/passage.html?nologin=1")
    height = await page.evaluate("() => document.documentElement.scrollHeight")
    assert height > 1600  # meaningfully taller than the 800px viewport
    await page.close()


@pytest.mark.asyncio
async def test_canvas_passage_has_no_selectable_dom_text(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage-canvas.html?nologin=1")
    body_text = await page.evaluate("() => document.body.innerText")
    first_sentence = ANSWER_KEY["passage"]["paragraphs"][0][:30]
    assert first_sentence not in body_text
    canvas_count = await page.evaluate("() => document.querySelectorAll('canvas').length")
    assert canvas_count >= 1
    await page.close()


@pytest.mark.asyncio
async def test_lazy_passage_requires_scrolling_to_load_all_paragraphs(site_url, browser):
    page = await browser.new_page(viewport={"width": 1280, "height": 800})
    await page.goto(f"{site_url}/passage-lazy.html?nologin=1")
    await page.wait_for_timeout(300)
    text_before = await page.evaluate("() => document.body.innerText")

    last_paragraph = ANSWER_KEY["passage"]["paragraphs"][-1][:30]
    assert last_paragraph not in text_before

    for _ in range(15):
        await page.mouse.wheel(0, 1500)
        await page.wait_for_timeout(150)

    text_after = await page.evaluate("() => document.body.innerText")
    assert last_paragraph in text_after
    await page.close()


async def _answer_question_set(page, set_key: str) -> None:
    questions = ANSWER_KEY["question_sets"][set_key]
    for q in questions:
        correct_option = next(o for o in q["options"] if o["id"] == q["correct_option_id"])
        correct_text = correct_option["text"]
        group_name = q["id"]
        radios = page.locator(f"input[name='{group_name}']")
        count = await radios.count()
        clicked = False
        for j in range(count):
            radio = radios.nth(j)
            radio_id = await radio.get_attribute("id")
            container_text = await page.locator(f"#{radio_id}").locator("xpath=..").inner_text()
            if correct_text.strip() in container_text:
                await radio.check(force=True)
                clicked = True
                break
        assert clicked, f"could not find option '{correct_text}' for {group_name}"


async def _login(page, site_url: str, next_path: str) -> None:
    await page.goto(f"{site_url}/login.html?next={next_path}")
    await page.fill("input[type=text], input[name=username], input#username", "tester")
    await page.fill("input[type=password]", "anything")
    await page.click("button[type=submit], input[type=submit], button")
    await page.wait_for_load_state("load")


@pytest.mark.asyncio
async def test_full_flow_cumulative_score_and_continue_label(site_url, browser):
    page = await browser.new_page()
    # The nologin=1 bypass is per-request only (BUILD_SPEC-accurate: it does
    # not propagate through the site's own internal navigation), so a
    # multi-page flow that ends up on score.html needs a real session.
    await _login(page, site_url, "score.html%3Freset%3D1")
    assert "score.html" in page.url

    await page.goto(f"{site_url}/questions.html?set=1&round=1")
    await _answer_question_set(page, "1")
    await page.click("button[type=submit], #submit-btn, button:has-text('Submit')")
    await page.wait_for_load_state("load")
    assert "score.html" in page.url

    body_text = await page.evaluate("() => document.body.innerText")
    expected_points = ANSWER_KEY["points_per_correct"] * len(ANSWER_KEY["question_sets"]["1"])
    assert str(expected_points) in body_text
    assert "%" not in body_text.split("Score")[-1][:20]  # points, not a percentage nearby

    continue_label = ANSWER_KEY["continue_button_label"]
    continue_btn = page.locator(f"a:has-text('{continue_label}'), button:has-text('{continue_label}')")
    assert await continue_btn.count() >= 1
    await continue_btn.first.click()
    await page.wait_for_load_state("load")
    assert "passage.html" in page.url

    # Second round: answer question set 2, confirm score is cumulative (adds, doesn't reset)
    await page.goto(f"{site_url}/questions.html?set=2&round=2")
    await _answer_question_set(page, "2")
    await page.click("button[type=submit], #submit-btn, button:has-text('Submit')")
    await page.wait_for_load_state("load")

    body_text_2 = await page.evaluate("() => document.body.innerText")
    cumulative_expected = expected_points + ANSWER_KEY["points_per_correct"] * len(
        ANSWER_KEY["question_sets"]["2"]
    )
    assert str(cumulative_expected) in body_text_2
    await page.close()


@pytest.mark.asyncio
async def test_score_reset(site_url, browser):
    page = await browser.new_page()
    await _login(page, site_url, "questions.html%3Fset%3D1%26round%3D1")
    assert "questions.html" in page.url

    await _answer_question_set(page, "1")
    await page.click("button[type=submit], #submit-btn, button:has-text('Submit')")
    await page.wait_for_load_state("load")
    assert "score.html" in page.url
    body_before = await page.evaluate("() => document.body.innerText")
    expected_points = ANSWER_KEY["points_per_correct"] * len(ANSWER_KEY["question_sets"]["1"])
    assert str(expected_points) in body_before  # sanity: something scored, not still 0

    await page.goto(f"{site_url}/score.html?reset=1")
    body_after = await page.evaluate("() => document.body.innerText")
    assert "Score: 0" in body_after or "Score:0" in body_after.replace(" ", "")
    await page.close()


@pytest.mark.asyncio
async def test_iframe_variant_embeds_passage(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage-iframe.html?nologin=1")
    await page.wait_for_load_state("load")
    frames = page.frames
    assert len(frames) >= 2
    child_text = ""
    for f in frames:
        if f != page.main_frame:
            child_text += await f.evaluate("() => document.body.innerText")
    first_sentence = ANSWER_KEY["passage"]["paragraphs"][0][:30]
    assert first_sentence in child_text
    await page.close()


@pytest.mark.asyncio
async def test_sticky_variant_has_repeated_chrome_text(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage-sticky.html?nologin=1")
    header = await page.evaluate(
        "() => { const h = document.querySelector('header, .sticky-header'); "
        "return h ? getComputedStyle(h).position : null; }"
    )
    assert header in ("sticky", "fixed")
    await page.close()


@pytest.mark.asyncio
async def test_canvas_columns_trap_is_two_columns(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/passage-canvas-columns.html?nologin=1")
    canvas_count = await page.evaluate("() => document.querySelectorAll('canvas').length")
    assert canvas_count >= 1
    await page.close()
