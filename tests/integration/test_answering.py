"""M7 acceptance tests (BUILD_SPEC.md milestone table): questions detected
and grounded-answered correctly using memory of a passage read on a
different page, with citations, and the click verified for real.
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
from spark.config import OcrSettings
from spark.memory import RunMemory
from conftest import platform_ocr_engine
from spark.perception.page_view import build_page_view
from spark.reasoning.providers.stub import StubProvider
from spark.reasoning.schemas import AnsweredQuestion
from spark.skills.answering import (
    answer_question_group,
    click_and_verify_answer,
    detect_question_groups,
    option_id_to_element,
    option_label,
)
from spark.browser.actions import ActionExecutionError

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
            headless=True, executable_path=str(find_chrome_executable()), args=["--no-sandbox"]
        )
        try:
            yield b
        finally:
            await b.close()


@pytest.mark.asyncio
async def test_detect_question_groups_finds_all_five_with_text(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/questions.html?set=1&round=1&nologin=1")
    view = await build_page_view(page, ocr_settings=OcrSettings(), read_engine=platform_ocr_engine())

    groups = detect_question_groups(view)
    assert len(groups) == 5
    for group in groups:
        assert len(group.options) == 4
        assert group.question_text  # recovered from the preceding text block
    await page.close()


@pytest.mark.asyncio
async def test_answer_grounded_in_remembered_passage_not_current_page(site_url, browser):
    """The core scenario: the passage was read on a DIFFERENT page load than
    the one the question appears on. Memory must supply it.
    """
    page = await browser.new_page()
    await page.goto(f"{site_url}/questions.html?set=1&round=1&nologin=1")
    view = await build_page_view(page, ocr_settings=OcrSettings(), read_engine=platform_ocr_engine())
    groups = detect_question_groups(view)

    memory = RunMemory()
    full_passage = " ".join(ANSWER_KEY["passage"]["paragraphs"])
    memory.remember_passage(step_index=0, url=f"{site_url}/passage.html", text=full_passage, source="dom")

    q1 = ANSWER_KEY["question_sets"]["1"][0]
    group = next(g for g in groups if q1["question"][:20] in g.question_text)

    correct_text = next(o["text"] for o in q1["options"] if o["id"] == q1["correct_option_id"])

    def responder(messages):
        # Simulate a real model correctly identifying the right option by
        # matching against the (grounded, memory-supplied) passage content.
        for i, option in enumerate(group.options):
            if option_label(option).strip() == correct_text.strip():
                return AnsweredQuestion(
                    question=group.question_text,
                    chosen_option_id=str(i),
                    confidence=0.95,
                    citation=full_passage[:60],
                    reasoning="Matches the passage.",
                )
        raise AssertionError("responder could not find the correct option among group.options")

    provider = StubProvider()
    provider.set_responder(AnsweredQuestion, responder)

    answer = await answer_question_group(provider=provider, group=group, memory=memory)
    chosen = option_id_to_element(group, answer.chosen_option_id)
    assert option_label(chosen).strip() == correct_text.strip()

    # And the prompt actually sent to the model contained the remembered
    # passage text, not the (visually passage-free) questions page.
    sent_messages = provider.calls[0]
    assert any(full_passage[:40] in (m.text or "") for m in sent_messages)
    await page.close()


@pytest.mark.asyncio
async def test_click_and_verify_answer_checks_the_real_radio(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/questions.html?set=1&round=1&nologin=1")
    view = await build_page_view(page, ocr_settings=OcrSettings(), read_engine=platform_ocr_engine())
    groups = detect_question_groups(view)
    group = groups[0]

    await click_and_verify_answer(page.frames, group, "0")

    is_checked = await page.eval_on_selector(group.options[0].selector, "el => el.checked")
    assert is_checked is True
    await page.close()


@pytest.mark.asyncio
async def test_low_confidence_triggers_forced_reread_then_retries(site_url, browser):
    page = await browser.new_page()
    await page.goto(f"{site_url}/questions.html?set=1&round=1&nologin=1")
    view = await build_page_view(page, ocr_settings=OcrSettings(), read_engine=platform_ocr_engine())
    groups = detect_question_groups(view)
    group = groups[0]

    memory = RunMemory()
    memory.remember_passage(step_index=0, url="x", text="Not much to go on.", source="dom")

    call_count = {"n": 0}

    def responder(messages):
        call_count["n"] += 1
        confidence = 0.3 if call_count["n"] == 1 else 0.9
        return AnsweredQuestion(
            question=group.question_text,
            chosen_option_id="0",
            confidence=confidence,
            citation="n/a",
            reasoning="testing re-read trigger",
        )

    provider = StubProvider()
    provider.set_responder(AnsweredQuestion, responder)

    reread_calls = {"n": 0}

    async def fake_reread():
        reread_calls["n"] += 1
        return await build_page_view(page, ocr_settings=OcrSettings(force_ocr=True), read_engine=platform_ocr_engine())

    answer = await answer_question_group(
        provider=provider, group=group, memory=memory, force_low_confidence_reread=fake_reread
    )

    assert call_count["n"] == 2  # asked, low confidence, forced re-read, asked again
    assert reread_calls["n"] == 1
    assert answer.confidence == 0.9
    await page.close()


def test_option_id_to_element_rejects_non_numeric():
    from spark.skills.answering import QuestionGroup

    group = QuestionGroup(group_key="k", question_text="q", options=[])
    with pytest.raises(ActionExecutionError):
        option_id_to_element(group, "not-a-number")


def test_option_id_to_element_rejects_out_of_range():
    from spark.perception.dom import BBox, ElementState, InteractiveElement
    from spark.skills.answering import QuestionGroup

    opt = InteractiveElement(
        id="e1", frame_path="main", tag="input", role="radio", name="", group_name="q0", text="",
        value=None, state=ElementState(), bbox=BBox(x=0, y=0, w=1, h=1), in_viewport=True,
        selector="#e1", nearby_text="A",
    )
    group = QuestionGroup(group_key="k", question_text="q", options=[opt])
    with pytest.raises(ActionExecutionError):
        option_id_to_element(group, "5")
