"""Pydantic models for every structured LLM output. See BUILD_SPEC.md §6.7
("Structured output is mandatory for every decision. Never parse free-form
prose into an action.") and §7 (the action schema).
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    click = "click"
    type = "type"
    select = "select"
    scroll = "scroll"
    navigate = "navigate"
    wait = "wait"
    read_more = "read_more"  # force an OCR re-read of the current page
    answer_questions = "answer_questions"
    report_score = "report_score"
    finish = "finish"
    fail = "fail"


class Action(BaseModel):
    """The next-step decision from the observe/decide prompt (BUILD_SPEC §7)."""

    thought: str
    type: ActionType
    element_id: str | None = None
    text: str | None = None
    url: str | None = None
    direction: Literal["down", "up", "to_element"] | None = None
    reason: str
    expectation: str = ""


class AnsweredQuestion(BaseModel):
    """One question's answer from the grounded-answering prompt (BUILD_SPEC §6.9)."""

    question: str
    chosen_option_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    citation: str  # the span of passage text the answer came from
    reasoning: str


class ScoreExtraction(BaseModel):
    """Output of the extract-score prompt (BUILD_SPEC §6.10)."""

    found: bool
    raw_text: str | None = None
    value: float | None = None
    maximum: float | None = None
    is_percentage: bool = False


class PageClassification(BaseModel):
    """Output of the classify-page prompt (BUILD_SPEC §6.8)."""

    page_type: Literal["reading", "questions", "score", "navigation", "blocked", "other"]
    reasoning: str
