"""Proves OrchestratorWorker's real async lifecycle (BUILD_SPEC §10): launch
Chrome, attach over CDP, dispatch to the configured provider, run, and clean
up — all on a background QThread with its own asyncio loop, communicating
back to the Qt thread via signals. Uses the unscripted "stub" provider
(build_provider("stub", ...) returns a bare StubProvider with no responses
queued), so the very first model call raises — which is fine here: the
point is proving the thread/asyncio/Chrome/teardown plumbing runs
end-to-end and reports failure through the `failed` signal rather than
hanging or crashing the thread silently.
"""
from __future__ import annotations

import os
import socket

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication

from spark.config import AppSettings, ChromeSettings
from spark.scripts.model import PerceptionConfig, StopConfig, Task


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_and_wait(thread: QThread, loop: QEventLoop, *, timeout_ms: int) -> None:
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)
    timeout_timer.start(timeout_ms)
    loop.exec()
    thread.wait(5000)


def test_worker_runs_full_lifecycle_and_emits_failed_on_unscripted_stub(qapp, tmp_path):
    from spark.gui.main_window import OrchestratorWorker

    settings = AppSettings(active_provider="stub")
    settings.chrome = ChromeSettings(profile_dir=str(tmp_path / "chrome-profile"), debug_port=_free_port())
    settings.ocr.escalation_engine = None

    task = Task(
        name="gui worker smoke test",
        start_url="data:text/html,<h1>hello</h1>",
        goal="irrelevant",
        stop=StopConfig(score_target=1, max_iterations=1, max_runtime_minutes=1),
        perception=PerceptionConfig(ocr_read_engine="tesseract"),
    )

    worker = OrchestratorWorker(task, settings)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    loop = QEventLoop()
    results = {}

    def on_finished(outcome, message):
        results["outcome"] = outcome
        results["message"] = message
        loop.quit()

    def on_failed(error):
        results["error"] = error
        loop.quit()

    worker.finished.connect(on_finished)
    worker.failed.connect(on_failed)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)

    # Chrome needs real headless/no-sandbox flags in THIS sandbox (see
    # launcher.py's extra_args docstring) — main_window.py's `_run_async`
    # does `from spark.browser.launcher import ChromeLauncher` as a LOCAL
    # import at call time, so patching the attribute on that module before
    # the worker thread runs is enough to redirect it, with no change to
    # production code.
    import spark.browser.launcher as launcher_module

    class _SandboxChromeLauncher(launcher_module.ChromeLauncher):
        def __init__(self, chrome_settings, extra_args=None):
            super().__init__(chrome_settings, extra_args=["--no-sandbox", "--headless=new"])

    original_launcher = launcher_module.ChromeLauncher
    launcher_module.ChromeLauncher = _SandboxChromeLauncher
    try:
        thread.start()
        _run_and_wait(thread, loop, timeout_ms=30_000)
    finally:
        launcher_module.ChromeLauncher = original_launcher

    assert "error" in results, f"expected the unscripted stub to fail the run; got {results}"
    assert "PageClassification" in results["error"] or "no queued response" in results["error"]
