"""Questions whose answers are big text buttons: one question per page, no
Submit, clicking an answer moves straight on (?style=buttons on the fixture).

The radio-group detector finds nothing on such pages, so the orchestrator
asks the model to pick the answer element itself. The scripted responders
parse the REAL prompt text, so the prompt -> schema -> click contract is
exercised, not bypassed.
"""
from __future__ import annotations

import http.server
import json
import re
import socket
import threading
from pathlib import Path

import pytest

from conftest import platform_ocr_engine_name
from spark.browser.launcher import ChromeLauncher
from spark.browser.session import BrowserSession
from spark.config import AppSettings, ChromeSettings
from spark.orchestrator import Orchestrator, RunOutcome
from spark.reasoning.providers.stub import StubProvider
from spark.reasoning.schemas import Action, ActionType, ButtonAnswer, PageClassification, ScoreExtraction
from spark.scripts.model import PerceptionConfig, StopConfig, Task

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


def _classify(messages) -> PageClassification:
    text = messages[0].text or ""
    if re.search(r"Question \d+ of \d+", text):
        return PageClassification(page_type="questions", reasoning="a 'Question N of M' heading")
    if re.search(r"\bScore:\s*\d+", text):
        return PageClassification(page_type="score", reasoning="a 'Score: N' line")
    return PageClassification(page_type="reading", reasoning="default")


def _decide(messages) -> Action:
    for line in (messages[0].text or "").splitlines():
        m = re.match(r"(e\d+): \w+ '([^']*)'", line.strip())
        if m and re.search(r"next|continue|carry on", m.group(2), re.IGNORECASE):
            return Action(thought="advance", type=ActionType.click, element_id=m.group(1), reason="advance", expectation="")
    raise AssertionError("no next/continue element in prompt")


def _answer(messages) -> ButtonAnswer:
    prompt = messages[0].text or ""
    on_page = prompt.split("Text currently on the page", 1)[-1]
    correct = None
    for qs in ANSWER_KEY["question_sets"].values():
        for q in qs:
            if q["question"] in on_page:
                correct = next(o["text"] for o in q["options"] if o["id"] == q["correct_option_id"])
    assert correct, f"no answer-key question matched the page:\n{on_page}"
    for line in prompt.splitlines():
        m = re.match(r"(e\d+): button '([^']*)'", line.strip())
        if m and m.group(2).strip() == correct.strip():
            return ButtonAnswer(question="q", chosen_element_id=m.group(1), confidence=0.99,
                                citation=correct, reasoning="matches the passage")
    raise AssertionError(f"button {correct!r} not in the element list:\n{prompt}")


def _score(messages) -> ScoreExtraction:
    section = (messages[0].text or "").split("Page text:", 1)[-1]
    m = re.search(r"Score:\s*(\d+)", section)
    if not m:
        return ScoreExtraction(found=False)
    return ScoreExtraction(found=True, raw_text=m.group(0), value=float(m.group(1)), maximum=None, is_percentage=False)


@pytest.fixture
async def logged_in_session(site_url, tmp_path):
    settings = ChromeSettings(profile_dir=str(tmp_path / "chrome-profile"), debug_port=_free_port())
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


@pytest.mark.asyncio
async def test_button_questions_run_to_cumulative_target(site_url, logged_in_session):
    provider = StubProvider()
    provider.set_responder(PageClassification, _classify)
    provider.set_responder(Action, _decide)
    provider.set_responder(ButtonAnswer, _answer)
    provider.set_responder(ScoreExtraction, _score)

    task = Task(
        name="button questions reach 200",
        start_url=f"{site_url}/passage.html?round=1&style=buttons",
        goal="Read, click the correct answer button for each question, repeat until the target.",
        stop=StopConfig(score_target=200, comparison=">=", max_iterations=10, max_runtime_minutes=5),
        perception=PerceptionConfig(ocr_read_engine=platform_ocr_engine_name()),
    )
    settings = AppSettings()
    settings.ocr.escalation_engine = None

    orch = Orchestrator(task=task, settings=settings, provider=provider, session=logged_in_session)
    result = await orch.run()

    assert result.outcome == RunOutcome.SUCCESS, result.message
    assert orch.memory.latest_score.value == 200  # two rounds x 5 correct x 20 points
    assert len(orch.memory.qa_history) == 10  # every question answered exactly once
    # A round is counted when its score page appears, not once per question.
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_choosing_an_element_not_on_the_page_fails_loudly(site_url, logged_in_session):
    provider = StubProvider()
    provider.set_responder(PageClassification, _classify)
    provider.set_responder(Action, _decide)
    provider.set_responder(
        ButtonAnswer,
        lambda m: ButtonAnswer(question="q", chosen_element_id="e999", confidence=0.9, citation="x", reasoning="bad"),
    )
    task = Task(
        name="bad element",
        start_url=f"{site_url}/passage.html?round=1&style=buttons",
        goal="g",
        stop=StopConfig(score_target=100, max_iterations=3, max_runtime_minutes=5),
        perception=PerceptionConfig(ocr_read_engine=platform_ocr_engine_name()),
    )
    settings = AppSettings()
    settings.ocr.escalation_engine = None
    orch = Orchestrator(task=task, settings=settings, provider=provider, session=logged_in_session)

    from spark.browser.actions import ActionExecutionError

    with pytest.raises(ActionExecutionError, match="not on the page"):
        await orch.run()
