"""Process-wide logging configuration.

Two handlers: a human-readable console stream (used by the CLI and mirrored
into the GUI's live log pane via a Qt-friendly handler added separately by
gui/main_window.py), and nothing else at this layer — per-run structured
JSONL logs are written by runlog/recorder.py, not by the stdlib logging
module, because they need a stable schema independent of log level/format.
"""
from __future__ import annotations

import logging
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s'\"]{6,})", re.IGNORECASE),
    re.compile(r"(authorization:\s*bearer\s+)([^\s'\"]+)", re.IGNORECASE),
    re.compile(r"(password\s*[:=]\s*)([^\s'\"]+)", re.IGNORECASE),
]


def redact(text: str) -> str:
    """Best-effort redaction of secret-shaped substrings before they hit a
    log line or a run artefact. See BUILD_SPEC.md §12.3 — this is mandatory,
    not optional, because run artefacts and prompts sent to a free-tier
    model must never carry credentials.
    """
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + "***REDACTED***", out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("spark")
    root.setLevel(level)
    if root.handlers:
        return  # already configured (e.g. re-entered from GUI + CLI)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"spark.{name}")
