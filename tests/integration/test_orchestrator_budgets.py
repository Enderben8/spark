"""BUILD_SPEC.md §12.1 guard rails, exercised against real Chromium with
StubProvider — confirms max_model_calls actually stops a run rather than
grinding forever.
"""
from __future__ import annotations

import http.server
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
from spark.reasoning.schemas import Action, ActionType, PageClassification
from spark.scripts.model import PerceptionConfig, StopConfig, Task

SITE_DIR = Path(__file__).parent.parent / "fixtures" / "site"
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


@pytest.fixture
async def session(site_url, tmp_path):
    settings = ChromeSettings(profile_dir=str(tmp_path / "chrome-profile"), debug_port=0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        settings.debug_port = s.getsockname()[1]
    launcher = ChromeLauncher(settings, extra_args=_SANDBOX_ONLY_ARGS)
    result = await launcher.ensure_running()
    sess = await BrowserSession.attach(result.cdp_url)
    try:
        yield sess
    finally:
        await sess.close()
        if result.process is not None:
            result.process.terminate()
            try:
                result.process.wait(timeout=5)
            except Exception:
                result.process.kill()


@pytest.mark.asyncio
async def test_max_model_calls_stops_the_run(site_url, session):
    """A page that never resolves to reading/questions/score (always
    'reading', with a bare passage.html?nologin=1 that has no Next-labelled
    element ever found by the decide-responder, per the fixture's actual
    button naming) forces repeated observe/decide model calls — proves the
    call-count budget actually halts a run rather than spinning forever.
    """
    provider = StubProvider()
    provider.set_responder(PageClassification, lambda messages: PageClassification(page_type="reading", reasoning="always reading, to force repeated decide() calls"))

    def decide_responder(messages):
        # Deliberately never finds a real "next" element — always emits a
        # harmless "wait" action, so the loop spins purely on model calls.
        return Action(thought="looking", type=ActionType.wait, reason="stalling on purpose", expectation="")

    provider.set_responder(Action, decide_responder)

    task = Task(
        name="budget test",
        start_url=f"{site_url}/passage.html?nologin=1",
        goal="irrelevant for this test",
        stop=StopConfig(score_target=999, max_iterations=1000, max_runtime_minutes=5),
        perception=PerceptionConfig(ocr_read_engine=platform_ocr_engine_name()),
    )
    settings = AppSettings()
    # Low enough to trip before stall-detection's own 3-identical-visits
    # threshold would otherwise fire first (both are legitimate stops; this
    # test isolates the call-count budget specifically). One iteration
    # spends exactly 2 calls (classify + decide), so this fires at the top
    # of the second iteration.
    settings.budgets.max_model_calls = 2
    settings.ocr.escalation_engine = None

    orchestrator = Orchestrator(task=task, settings=settings, provider=provider, session=session)
    result = await orchestrator.run()

    assert result.outcome == RunOutcome.BUDGET_EXCEEDED
    assert "max_model_calls" in result.message
    assert orchestrator.provider.tracker.calls_made >= 2
