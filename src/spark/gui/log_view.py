"""A ``logging.Handler`` that forwards records into the GUI's live log pane
from any thread. See BUILD_SPEC.md §10.

Qt widgets may only be touched from the GUI thread, but the orchestrator
runs on a background thread (main_window.py's ``OrchestratorWorker``) and
logs through the ordinary stdlib ``logging`` module, which has no notion of
"the Qt thread". A ``Signal`` emitted from a worker thread is automatically
queued and delivered on the receiver's thread (Qt's
``Qt.QueuedConnection``, the default across threads) — this is the one
thread-safe bridge point.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class QtLogSignal(QObject):
    message = Signal(str, str)  # (level name, formatted message)


class QtLogHandler(logging.Handler):
    def __init__(self, signal_obj: QtLogSignal):
        super().__init__()
        self._signal_obj = signal_obj
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._signal_obj.message.emit(record.levelname, msg)
