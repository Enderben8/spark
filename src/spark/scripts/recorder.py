"""Turns a successful AI-driven run into a replayable script. See
BUILD_SPEC.md §9.2.

Deliberately coarse-grained: one recorded step per orchestrator-level
action (advance to the next screen, perform the loop action, answer the
current question set) rather than every individual click a full DOM replay
might need — this keeps the recorded format stable across minor page
changes and matches what actually saves cost on replay: skipping the
classify+decide model calls for "click Next"-shaped steps, while still
calling the real answering skill (grounded, model-backed) for
``answer_questions``, exactly as BUILD_SPEC §9.2 specifies ("Question-
answering steps are recorded as answer_questions ... never as hard-coded
option clicks — the questions will differ between runs").
"""
from __future__ import annotations

from pathlib import Path

from spark.config import SCRIPTS_DIR
from spark.logsetup import get_logger
from spark.scripts.model import Script, ScriptStep

log = get_logger("scripts.recorder")


class ScriptRecorder:
    def __init__(self, task_name: str):
        self.task_name = task_name
        self.steps: list[ScriptStep] = []

    def record_click(self, *, selector: str, frame_path: str, expectation: str = "") -> None:
        self.steps.append(
            ScriptStep(action_type="click", selector=selector, frame_path=frame_path, expectation=expectation)
        )

    def record_navigate(self, *, url: str) -> None:
        self.steps.append(ScriptStep(action_type="navigate", url=url))

    def record_answer_questions(self) -> None:
        self.steps.append(ScriptStep(action_type="answer_questions"))

    def to_script(self) -> Script:
        return Script(task_name=self.task_name, steps=list(self.steps))

    def save(self, path: Path | None = None) -> Path:
        path = path or (SCRIPTS_DIR / f"{_safe_filename(self.task_name)}.yaml")
        from spark.scripts.model import save_script

        save_script(self.to_script(), path)
        log.info("Recorded %d steps to %s", len(self.steps), path)
        return path


def _safe_filename(name: str, *, max_len: int = 60) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    return safe[:max_len].strip("-") or "task"
