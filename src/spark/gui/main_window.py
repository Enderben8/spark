"""The Spark desktop GUI. See BUILD_SPEC.md §10.

Keep this thin: the orchestrator does the real work on a background thread
with its own asyncio event loop (Qt's own event loop is not
asyncio-compatible, and the orchestrator is async throughout — Playwright,
provider calls). This module only renders state and forwards user actions;
all cross-thread communication goes through Qt signals, the only safe way
to touch widgets from a non-GUI thread.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spark.config import AppSettings, TASKS_DIR, ensure_data_dirs, load_settings, save_settings
from spark.gui.log_view import QtLogHandler, QtLogSignal
from spark.gui.settings_dialog import SettingsDialog
from spark.logsetup import get_logger
from spark.orchestrator import Orchestrator, RunOutcome
from spark.scripts.model import Task, load_task

log = get_logger("gui.main_window")

_LEVEL_COLORS = {
    "ERROR": "#d33",
    "WARNING": "#b8860b",
    "INFO": "#222",
    "DEBUG": "#888",
}


class OrchestratorWorker(QObject):
    """Owns the full async lifecycle of one run — launching Chrome,
    attaching over CDP, building the configured provider, and running the
    Orchestrator — on a dedicated background thread.
    """

    finished = Signal(str, str)  # RunOutcome.value, message
    failed = Signal(str)

    def __init__(self, task: Task, settings: AppSettings):
        super().__init__()
        self.task = task
        self.settings = settings
        self._orchestrator: Orchestrator | None = None
        self._cancel_requested = False

    @Slot()
    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_async())
        except Exception as exc:
            log.exception("Run failed with an unexpected exception")
            self.failed.emit(str(exc))
        finally:
            loop.close()

    async def _run_async(self) -> None:
        from spark.browser.launcher import ChromeLauncher
        from spark.browser.session import BrowserSession
        from spark.reasoning.provider import build_provider

        launcher = ChromeLauncher(self.settings.chrome)
        launch_result = await launcher.ensure_running()
        session = await BrowserSession.attach(launch_result.cdp_url, prefer_url_substring=self.task.start_url)
        try:
            provider = build_provider(self.settings.active_provider, self.settings.provider_settings())
            self._orchestrator = Orchestrator(
                task=self.task, settings=self.settings, provider=provider, session=session
            )
            if self._cancel_requested:
                self._orchestrator.cancel()
            result = await self._orchestrator.run()
            self.finished.emit(result.outcome.value, result.message)
        finally:
            await session.close()
            launcher.shutdown(launch_result)

    def request_cancel(self) -> None:
        """Called from the GUI thread. ``Orchestrator.cancel()`` just flips
        a plain bool checked between steps (BUILD_SPEC §10: Stop must react
        within a second or two, not at the end of a long call) — safe to
        set cross-thread without extra locking.
        """
        self._cancel_requested = True
        if self._orchestrator is not None:
            self._orchestrator.cancel()

    @property
    def orchestrator(self) -> Orchestrator | None:
        return self._orchestrator


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Spark")
        self.resize(880, 600)

        ensure_data_dirs()
        self.settings = load_settings()

        self._thread: QThread | None = None
        self._worker: OrchestratorWorker | None = None
        self._run_start_time: float | None = None

        self._log_signal = QtLogSignal()
        self._log_signal.message.connect(self._append_log_line)
        import logging

        self._log_handler = QtLogHandler(self._log_signal)
        logging.getLogger("spark").addHandler(self._log_handler)

        self._build_ui()

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(500)

    # -- UI construction -----------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        top_bar = QHBoxLayout()
        self.task_combo = QComboBox()
        self._reload_task_list()
        top_bar.addWidget(QLabel("Task:"))
        top_bar.addWidget(self.task_combo, stretch=1)

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._on_start)
        top_bar.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        top_bar.addWidget(self.stop_btn)

        settings_btn = QPushButton("Settings...")
        settings_btn.clicked.connect(self._on_open_settings)
        top_bar.addWidget(settings_btn)

        layout.addLayout(top_bar)

        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: #555;")
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        layout.addWidget(self.log_view, stretch=1)

        self.setCentralWidget(central)

    def _reload_task_list(self) -> None:
        self.task_combo.clear()
        task_files = sorted(TASKS_DIR.glob("*.yaml")) if TASKS_DIR.exists() else []
        for f in task_files:
            self.task_combo.addItem(f.stem, userData=str(f))
        self.task_combo.addItem("New task...", userData=None)

    # -- log pane --------------------------------------------------------

    @Slot(str, str)
    def _append_log_line(self, level: str, message: str) -> None:
        color = _LEVEL_COLORS.get(level, "#222")
        self.log_view.appendHtml(f'<span style="color:{color}">{_escape_html(message)}</span>')
        self.log_view.moveCursor(QTextCursor.End)

    # -- task selection ----------------------------------------------------

    def _current_task_path(self) -> Path | None:
        data = self.task_combo.currentData()
        if data is not None:
            return Path(data)
        # "New task..." selected — browse for a file.
        path_str, _ = QFileDialog.getOpenFileName(self, "Open task file", str(TASKS_DIR), "Task files (*.yaml)")
        return Path(path_str) if path_str else None

    # -- start/stop ----------------------------------------------------

    def _on_start(self) -> None:
        path = self._current_task_path()
        if path is None:
            return
        try:
            task = load_task(path)
        except Exception as exc:
            QMessageBox.warning(self, "Could not load task", str(exc))
            return

        self.status_label.setText(f"Starting: {task.name}")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._run_start_time = time.monotonic()

        self._thread = QThread()
        self._worker = OrchestratorWorker(task, self.settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_run_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
        self.status_label.setText("Stopping...")

    @Slot(str, str)
    def _on_run_finished(self, outcome: str, message: str) -> None:
        self.status_label.setText(f"Finished: {outcome} — {message}")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    @Slot(str)
    def _on_run_failed(self, error: str) -> None:
        self.status_label.setText(f"Error: {error}")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        QMessageBox.critical(self, "Run failed", error)

    def _refresh_status(self) -> None:
        if self._worker is None or self._worker.orchestrator is None or self._run_start_time is None:
            return
        orch = self._worker.orchestrator
        elapsed = time.monotonic() - self._run_start_time
        latest_score = orch.memory.latest_score
        score_text = latest_score.raw_text if latest_score else "—"
        cost = orch.provider.tracker.estimate_cost_usd(self.settings.provider_settings())
        cost_text = f"${cost:.4f}" if cost is not None else "unknown"
        self.status_label.setText(
            f"URL: {orch.session.current_url} | Elapsed: {elapsed:0.0f}s | "
            f"Model calls: {orch.provider.tracker.calls_made} | Est. cost: {cost_text} | "
            f"Last score: {score_text}"
        )

    # -- settings ----------------------------------------------------------

    def _on_open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.result_settings()
            save_settings(self.settings)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._worker is not None:
            self._worker.request_cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        super().closeEvent(event)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run_gui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
