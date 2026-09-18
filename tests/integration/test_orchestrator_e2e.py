"""M6/M8 acceptance test (BUILD_SPEC.md milestone table): "Spark navigates
the fixture flow from passage to questions unaided" and "Fixture run stops
exactly when the target is reached" — driven end to end with NO real model
(StubProvider only, per BUILD_SPEC §11.2: "This must pass in CI without any
API key"). The StubProvider's responders parse the actual prompt text built
by reasoning/prompts.py, so this exercises the real prompt→schema contract,
not a bypass of it.

Login is established once, out of band, by driving the real login form
directly (the same one-time-manual-sign-in the product itself requires per
BUILD_SPEC §6.11 — the orchestrator itself never touches credentials).
"""
from __future__ import annotations

import http.server
import json
import re
import socket
import threading
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from spark.browser.launcher import ChromeLauncher, find_chrome_executable
from spark.browser.session import BrowserSession
from spark.config import AppSettings, ChromeSettings
from spark.orchestrator import Orchestrator, RunOutcome
from spark.reasoning.providers.stub import StubProvider
from spark.reasoning.schemas import Action, ActionType, AnsweredQuestion, PageClassification, ScoreExtraction
from spark.scripts.model import AuthConfig, LoopAction, PerceptionConfig, ScoreConfig, StopConfig, Task

SITE_DIR = Path(__file__).parent.parent / "fixtures" / "site"
ANSWER_KEY = json.loads((SITE_DIR / "answer-key.json").read_text())
_SANDBOX_ONLY_ARGS = ["--no-sandbox", "--headless=new"]


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


def _build_classify_responder():
    def responder(messages) -> PageClassification:
        text = messages[0].text or ""
        if re.search(r"submit", text, re.IGNORECASE) and re.search(r"question", text, re.IGNORECASE):
            return PageClassification(page_type="questions", reasoning="submit button + question wording present")
        if re.search(r"\bScore:\s*\d+", text):
            return PageClassification(page_type="score", reasoning="a 'Score: N' line is present")
        if re.search(r"sign in|password", text, re.IGNORECASE):
            return PageClassification(page_type="blocked", reasoning="login form detected")
        return PageClassification(page_type="reading", reasoning="default: a passage with a way to move on")

    return responder


def _build_decide_responder():
    def responder(messages) -> Action:
        text = messages[0].text or ""
        # Element inventory lines look like: "e12: button 'Next'"
        for line in text.splitlines():
            m = re.match(r"(e\d+): \w+ '([^']*)'", line.strip())
            if m and re.search(
                r"next|continue|carry on|onward|try again|retry", m.group(2), re.IGNORECASE
            ):
                return Action(
                    thought="Advancing via the visible continue/next control",
                    type=ActionType.click,
                    element_id=m.group(1),
                    reason="Move to the next step",
                    expectation="the next page loads",
                )
        raise AssertionError(f"decide responder found no next/continue element in prompt:\n{text}")

    return responder


def _build_answer_responder():
    def responder(messages) -> AnsweredQuestion:
        text = messages[0].text or ""
        q_match = re.search(r"Question: (.+)", text)
        options_block = re.findall(r"^(\d+): (.+)$", text, re.MULTILINE)
        assert q_match and options_block, f"could not parse question/options from prompt:\n{text}"
        question_text = q_match.group(1).strip()

        correct_text = None
        for qs in ANSWER_KEY["question_sets"].values():
            for q in qs:
                if q["question"].strip() == question_text or q["question"][:25] in question_text:
                    correct_text = next(o["text"] for o in q["options"] if o["id"] == q["correct_option_id"])
                    break
            if correct_text:
                break
        assert correct_text, f"no answer-key entry matched question: {question_text!r}"

        for idx, option_text in options_block:
            if option_text.strip() == correct_text.strip():
                return AnsweredQuestion(
                    question=question_text,
                    chosen_option_id=idx,
                    confidence=0.99,
                    citation=correct_text,
                    reasoning="Matches the passage fact.",
                )
        raise AssertionError(f"correct option {correct_text!r} not found among parsed options {options_block}")

    return responder


def _build_score_responder():
    def responder(messages) -> ScoreExtraction:
        text = messages[0].text or ""
        # The prompt template itself contains an illustrative "Score: 450"
        # example ahead of the actual embedded page text (see
        # reasoning/prompts.py build_extract_score_prompt) — scope the
        # search to the "Page text:" section only, or this stub matches the
        # prompt's own instructions instead of the real page content.
        page_text_section = text.split("Page text:", 1)[-1]
        m = re.search(r"Score:\s*(\d+)", page_text_section)
        if not m:
            return ScoreExtraction(found=False)
        return ScoreExtraction(found=True, raw_text=m.group(0), value=float(m.group(1)), maximum=None, is_percentage=False)

    return responder


@pytest.fixture
async def logged_in_session(site_url, tmp_path):
    settings = ChromeSettings(profile_dir=str(tmp_path / "chrome-profile"), debug_port=0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        settings.debug_port = s.getsockname()[1]

    launcher = ChromeLauncher(settings, extra_args=_SANDBOX_ONLY_ARGS)
    result = await launcher.ensure_running()
    session = await BrowserSession.attach(result.cdp_url)

    # Establish the session the same way a real user would: drive the real
    # login form once. The orchestrator itself never sees a password.
    await session.goto(f"{site_url}/login.html?next=score.html%3Freset%3D1")
    await session.page.fill("input[type=text], input[name=username], input#username", "tester")
    await session.page.fill("input[type=password]", "anything")
    await session.page.click("button[type=submit], input[type=submit], button")
    await session.page.wait_for_load_state("load")
    assert "score.html" in session.current_url

    try:
        yield session
    finally:
        await session.close()
        if result.process is not None:
            result.process.terminate()
            try:
                result.process.wait(timeout=5)
            except Exception:
                result.process.kill()


@pytest.mark.asyncio
async def test_full_run_stops_exactly_when_cumulative_target_reached(site_url, logged_in_session):
    """Two rounds of 5 correct answers = 200 points (100/round). Target 200
    with '>=' must stop after exactly round 2, not one round early or late.
    """
    provider = StubProvider()
    provider.set_responder(PageClassification, _build_classify_responder())
    provider.set_responder(Action, _build_decide_responder())
    provider.set_responder(AnsweredQuestion, _build_answer_responder())
    provider.set_responder(ScoreExtraction, _build_score_responder())

    task = Task(
        name="e2e reach 200",
        start_url=f"{site_url}/passage.html?round=1",
        goal="Read the passage, answer the questions correctly, stop at the target score.",
        stop=StopConfig(score_target=200, comparison=">=", max_iterations=10, max_runtime_minutes=5),
        score=ScoreConfig(cumulative=True, is_percentage=False),
        loop_action=LoopAction(type="click", button_text=None),
        auth=AuthConfig(requires_login=True),
        perception=PerceptionConfig(force_ocr=False, ocr_read_engine="tesseract"),
    )

    settings = AppSettings()
    settings.ocr.escalation_engine = None  # keep this test deterministic; escalation is covered elsewhere

    orchestrator = Orchestrator(task=task, settings=settings, provider=provider, session=logged_in_session)
    result = await orchestrator.run()

    assert result.outcome == RunOutcome.SUCCESS, result.message
    assert orchestrator.memory.latest_score.value == 200
    assert len(orchestrator.memory.score_readings) == 2  # stopped after round 2, not earlier or later
    # The passage was read on a different page than the questions it grounded.
    assert len(orchestrator.memory.passages) >= 2
    assert len(orchestrator.memory.qa_history) == 10  # 5 questions x 2 rounds, all recorded


@pytest.mark.asyncio
async def test_unreachable_target_stops_cleanly_at_max_iterations(site_url, logged_in_session):
    provider = StubProvider()
    provider.set_responder(PageClassification, _build_classify_responder())
    provider.set_responder(Action, _build_decide_responder())
    provider.set_responder(AnsweredQuestion, _build_answer_responder())
    provider.set_responder(ScoreExtraction, _build_score_responder())

    task = Task(
        name="e2e unreachable",
        start_url=f"{site_url}/passage.html?round=1",
        goal="Read the passage, answer the questions correctly, stop at the target score.",
        stop=StopConfig(score_target=100_000, comparison=">=", max_iterations=2, max_runtime_minutes=5),
        score=ScoreConfig(cumulative=True, is_percentage=False),
        perception=PerceptionConfig(force_ocr=False, ocr_read_engine="tesseract"),
    )
    settings = AppSettings()
    settings.ocr.escalation_engine = None

    orchestrator = Orchestrator(task=task, settings=settings, provider=provider, session=logged_in_session)
    result = await orchestrator.run()

    assert result.outcome == RunOutcome.TARGET_NOT_REACHED, result.message
    assert "max_iterations" in result.message
