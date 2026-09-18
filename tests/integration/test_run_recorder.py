"""M11 acceptance test (BUILD_SPEC.md milestone table): report.md is
readable and accurate — driven through the real orchestrator loop, not a
synthetic call to RunRecorder alone, so it proves the actual wiring.
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
from spark.reasoning.schemas import Action, ActionType, AnsweredQuestion, PageClassification, ScoreExtraction
from spark.runlog.recorder import RunRecorder, purge_old_runs
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


@pytest.mark.asyncio
async def test_run_recorder_writes_real_artefacts(site_url, logged_in_session, tmp_path):
    provider = StubProvider()
    provider.set_responder(PageClassification, _classify_responder)
    provider.set_responder(Action, _decide_responder)
    provider.set_responder(AnsweredQuestion, _answer_responder)
    provider.set_responder(ScoreExtraction, _score_responder)

    task = Task(
        name="run recorder test!!",
        start_url=f"{site_url}/passage.html?round=1",
        goal="Read, answer, stop at target.",
        stop=StopConfig(score_target=100, comparison=">=", max_iterations=5, max_runtime_minutes=5),
        score=ScoreConfig(cumulative=True),
        loop_action=LoopAction(type="click"),
        auth=AuthConfig(requires_login=True),
        perception=PerceptionConfig(ocr_read_engine=platform_ocr_engine_name()),
    )
    settings = AppSettings()
    settings.ocr.escalation_engine = None
    settings.retention.screenshot_policy = "question_and_score"

    runs_base = tmp_path / "runs"
    recorder = RunRecorder(task=task, settings=settings, base_dir=runs_base)

    orchestrator = Orchestrator(task=task, settings=settings, provider=provider, session=logged_in_session, recorder=recorder)
    result = await orchestrator.run()

    assert result.outcome == RunOutcome.SUCCESS

    run_dir = recorder.run_dir
    assert run_dir.exists()
    assert (run_dir / "run.jsonl").exists()
    assert (run_dir / "memory.json").exists()
    assert (run_dir / "task.json").exists()
    assert (run_dir / "settings.json").exists()
    report_text = (run_dir / "report.md").read_text()
    assert "run recorder test" in report_text
    assert "SUCCESS".lower() in report_text.lower() or "success" in report_text

    jsonl_lines = (run_dir / "run.jsonl").read_text().strip().splitlines()
    assert len(jsonl_lines) >= 3  # at least reading, questions, score steps
    parsed = [json.loads(line) for line in jsonl_lines]
    page_types_seen = {p["page_type"] for p in parsed}
    assert {"reading", "questions", "score"} <= page_types_seen

    # Screenshots taken for question/score pages per the default policy, not for reading.
    screenshots = list((run_dir / "screenshots").glob("*.png"))
    assert len(screenshots) >= 2
    labeled_questions = [p for p in screenshots if "questions" in p.name]
    labeled_score = [p for p in screenshots if "score" in p.name]
    assert labeled_questions and labeled_score

    memory_data = json.loads((run_dir / "memory.json").read_text())
    assert len(memory_data["qa_history"]) == 5
    assert len(memory_data["passages"]) >= 1


def test_purge_old_runs_removes_stale_directories(tmp_path):
    from spark.config import RetentionSettings

    base = tmp_path / "runs"
    base.mkdir()
    old_dir = base / "old-run"
    old_dir.mkdir()
    new_dir = base / "new-run"
    new_dir.mkdir()

    import os
    import time

    old_time = time.time() - 40 * 86400  # 40 days old
    os.utime(old_dir, (old_time, old_time))

    removed = purge_old_runs(RetentionSettings(keep_runs_days=30), base_dir=base)
    assert removed == 1
    assert not old_dir.exists()
    assert new_dir.exists()
