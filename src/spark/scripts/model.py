"""The task file schema. See BUILD_SPEC.md §9.1 — this is the everyday,
plain-YAML input the owner writes ("read each passage, click Next, answer
correctly, stop at 80%"), as opposed to the recorded replay scripts in
scripts/runner.py / scripts/recorder.py (§9.2), which are a different,
denser format this module does not handle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class StopConfig(BaseModel):
    score_target: float
    score_is_percentage: bool = False  # legacy alias accepted; prefer score.is_percentage below
    comparison: Literal[">=", ">", "=="] = ">="
    max_iterations: int = 25
    max_runtime_minutes: int = 60


class ScoreConfig(BaseModel):
    cumulative: bool = True
    is_percentage: bool = False
    selector: str | None = None
    regex: str | None = None


class LoopAction(BaseModel):
    type: Literal["click", "navigate"] = "click"
    button_text: str | None = None  # None = auto-detect (BUILD_SPEC §6.10)
    url: str | None = None


class AuthConfig(BaseModel):
    requires_login: bool = False


class PerceptionConfig(BaseModel):
    force_ocr: bool = False
    ocr_read_engine: Literal["windows", "vision", "tesseract"] = "windows"


class LimitsConfig(BaseModel):
    max_steps_per_iteration: int = 60
    max_cost_usd: float = 2.00


class Task(BaseModel):
    name: str
    start_url: str
    goal: str
    stop: StopConfig
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    loop_action: LoopAction = Field(default_factory=LoopAction)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)


def load_task(path: Path) -> Task:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Task.model_validate(data)


def save_task(task: Task, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(task.model_dump(), sort_keys=False), encoding="utf-8")
