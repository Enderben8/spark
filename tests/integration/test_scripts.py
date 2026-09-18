"""M10 acceptance test (BUILD_SPEC.md milestone table): a recorded script
replays the fixture flow with zero model calls for navigation; breaking a
selector triggers fallback and the run still completes.
"""
from __future__ import annotations

import http.server
import json
import re
import socket
import threading
from pathlib import Path

import pytest

from spark.browser.launcher import ChromeLauncher
from spark.browser.session import BrowserSession
from spark.config import AppSettings, ChromeSettings
from spark.memory import RunMemory
from spark.orchestrator import Orchestrator, RunOutcome
from spark.reasoning.providers.stub import StubProvider
from spark.reasoning.schemas import Action, ActionType, AnsweredQuestion, PageClassification, ScoreExtraction
from spark.scripts.model import AuthConfig, LoopAction, PerceptionConfig, ScoreConfig, StopConfig, Task
from spark.scripts.recorder import ScriptRecorder
from spark.scripts.runner import replay_script, run_script_with_fallback

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


def _classify_responder(messages):
    text = messages[0].text or ""
    if re.search(r"submit", text, re.IGNORECASE) and re.search(r"question", text, re.IGNORECASE):
        return PageClassification(page_type="questions", reasoning="")
    if re.search(r"\bScore:\s*\d+", text):
        return PageClassification(page_type="score", reasoning="")
    return PageClassification(page_type="reading", reasoning="")


def _decide_responder(messages):
    text = messages[0].text or ""
    for line in text.splitlines():
        m = re.match(r"(e\d+): \w+ '([^']*)'", line.strip())
        if m and re.search(r"next|continue|carry on|onward", m.group(2), re.IGNORECASE):
            return Action(thought="advance", type=ActionType.click, element_id=m.group(1), reason="advance", expectation="")
    raise AssertionError("no next/continue element found")


def _answer_responder(messages):
    text = messages[0].text or ""
    q_match = re.search(r"Question: (.+)", text)
    options_block = re.findall(r"^(\d+): (.+)$", text, re.MULTILINE)
    question_text = q_match.group(1).strip()
    correct_text = None
    for qs in ANSWER_KEY["question_sets"].values():
        for q in qs:
            if q["question"][:25] in question_text:
                correct_text = next(o["text"] for o in q["options"] if o["id"] == q["correct_option_id"])
                break
        if correct_text:
            break
    for idx, option_text in options_block:
        if option_text.strip() == correct_text.strip():
            return AnsweredQuestion(question=question_text, chosen_option_id=idx, confidence=0.99, citation=correct_text, reasoning="matches")
    raise AssertionError("correct option not found")


def _score_responder(messages):
    text = messages[0].text or ""
    page_text_section = text.split("Page text:", 1)[-1]
    m = re.search(r"Score:\s*(\d+)", page_text_section)
    if not m:
        return ScoreExtraction(found=False)
    return ScoreExtraction(found=True, raw_text=m.group(0), value=float(m.group(1)), maximum=None, is_percentage=False)


def _scripted_provider() -> StubProvider:
    provider = StubProvider()
    provider.set_responder(PageClassification, _classify_responder)
    provider.set_responder(Action, _decide_responder)
    provider.set_responder(AnsweredQuestion, _answer_responder)
    provider.set_responder(ScoreExtraction, _score_responder)
    return provider


@pytest.fixture
async def logged_in_session(site_url, tmp_path):
    settings = ChromeSettings(profile_dir=str(tmp_path / "chrome-profile"), debug_port=0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        settings.debug_port = s.getsockname()[1]
    launcher = ChromeLauncher(settings, extra_args=_SANDBOX_ONLY_ARGS)
    result = await launcher.ensure_running()
    session = await BrowserSession.attach(result.cdp_url)
    await session.goto(f"{site_url}/login.html?next=score.html%3Freset%3D1")
    await session.page.fill("input[type=text], input[name=username], input#username", "tester")
    await session.page.fill("input[type=password]", "anything")
    await session.page.click("button[type=submit], input[type=submit], button")
    await session.page.wait_for_load_state("load")
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


def _make_task(site_url: str) -> Task:
    return Task(
        name="script test",
        start_url=f"{site_url}/passage.html?round=1",
        goal="Read, answer, stop at target.",
        stop=StopConfig(score_target=100, comparison=">=", max_iterations=5, max_runtime_minutes=5),
        score=ScoreConfig(cumulative=True),
        loop_action=LoopAction(type="click"),
        auth=AuthConfig(requires_login=True),
        perception=PerceptionConfig(ocr_read_engine="tesseract"),
    )


@pytest.mark.asyncio
async def test_record_then_replay_reaches_same_outcome_with_no_navigation_model_calls(site_url, logged_in_session):
    task = _make_task(site_url)
    settings = AppSettings()
    settings.ocr.escalation_engine = None

    # --- Phase 1: record a script from a real AI-driven run -----------
    ai_provider = _scripted_provider()
    script_recorder = ScriptRecorder(task.name)
    orchestrator = Orchestrator(
        task=task, settings=settings, provider=ai_provider, session=logged_in_session, script_recorder=script_recorder
    )
    result = await orchestrator.run()
    assert result.outcome == RunOutcome.SUCCESS
    script = script_recorder.to_script()
    assert len(script.steps) >= 2  # at least one "advance" click and one "answer_questions"
    action_types = [s.action_type for s in script.steps]
    assert "answer_questions" in action_types

    # --- Phase 2: reset the site's score and replay the script ---------
    await logged_in_session.goto(f"{site_url}/score.html?reset=1")
    await logged_in_session.goto(f"{site_url}/passage.html?round=1")

    replay_provider = StubProvider()  # deliberately NOT given classify/decide responders
    replay_provider.set_responder(AnsweredQuestion, _answer_responder)
    replay_result = await replay_script(
        script, session=logged_in_session, settings=settings, provider=replay_provider
    )

    # If replay had needed a PageClassification or Action call (i.e. had
    # fallen back to AI-driven navigation), it would have raised
    # AssertionError from StubProvider's unscripted-schema guard and
    # replay_result.completed would be False — completing here IS the
    # proof that zero navigation model calls were made.
    assert replay_result.completed, replay_result.reason

    body_text = await logged_in_session.evaluate("() => document.body.innerText")
    assert "Score: 100" in body_text


@pytest.mark.asyncio
async def test_broken_selector_falls_back_to_ai_and_still_completes(site_url, logged_in_session):
    task = _make_task(site_url)
    settings = AppSettings()
    settings.ocr.escalation_engine = None

    ai_provider = _scripted_provider()
    script_recorder = ScriptRecorder(task.name)
    orchestrator = Orchestrator(
        task=task, settings=settings, provider=ai_provider, session=logged_in_session, script_recorder=script_recorder
    )
    result = await orchestrator.run()
    assert result.outcome == RunOutcome.SUCCESS
    script = script_recorder.to_script()

    # Deliberately break the first click step's selector to force a fallback.
    first_click_index = next(i for i, s in enumerate(script.steps) if s.action_type == "click")
    script.steps[first_click_index].selector = "#this-selector-does-not-exist-anywhere"

    await logged_in_session.goto(f"{site_url}/score.html?reset=1")
    await logged_in_session.goto(f"{site_url}/passage.html?round=1")

    fallback_provider = _scripted_provider()
    final_result = await run_script_with_fallback(
        script, task=task, session=logged_in_session, settings=settings, provider=fallback_provider
    )

    assert final_result.outcome == RunOutcome.SUCCESS
    body_text = await logged_in_session.evaluate("() => document.body.innerText")
    assert "Score: 100" in body_text
