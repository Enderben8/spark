"""Deterministic replay of a recorded :class:`~spark.scripts.model.Script`.
See BUILD_SPEC.md §9.2.

Click and navigate steps are replayed directly against their recorded
selector/URL — no classify or decide model call, which is the whole point
("cheap and deterministic when the site is stable"). ``answer_questions``
steps still call the real, grounded answering skill (a model call per
question), exactly as BUILD_SPEC §9.2 requires — the questions differ
between runs, so there is nothing sensible to hard-code there.

When a step's expectation isn't met — the recorded selector no longer
resolves to anything clickable — replay stops at that step rather than
guessing. The caller is expected to fall back to a full AI-driven
:class:`~spark.orchestrator.Orchestrator` run from wherever the browser
currently sits (BUILD_SPEC §9.2: "self-healing when it is not" [stable]).
This module doesn't perform that fallback itself, to keep it decoupled from
the orchestrator; see ``run_script_with_fallback`` for the combined
convenience path.
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.async_api import Frame

from spark.browser.actions import ActionExecutionError, click_element
from spark.browser.session import BrowserSession
from spark.config import AppSettings
from spark.logsetup import get_logger
from spark.memory import RunMemory
from spark.perception.dom import InteractiveElement, resolve_frame
from spark.perception.page_view import build_page_view
from spark.reasoning.provider import LLMProvider
from spark.scripts.model import Script, ScriptStep
from spark.skills.answering import answer_question_group, click_and_verify_answer, detect_question_groups

log = get_logger("scripts.runner")


@dataclass
class ReplayResult:
    completed: bool
    stopped_at_index: int | None
    reason: str | None
    steps_completed: int


async def replay_script(
    script: Script,
    *,
    session: BrowserSession,
    settings: AppSettings,
    provider: LLMProvider,
    memory: RunMemory | None = None,
    timeout_ms: int = 5000,
) -> ReplayResult:
    memory = memory if memory is not None else RunMemory()

    for i, step in enumerate(script.steps):
        try:
            if step.action_type == "click":
                await _replay_click(session, step, timeout_ms=timeout_ms)
            elif step.action_type == "navigate":
                if not step.url:
                    raise ActionExecutionError(f"Script step {i} is a navigate step with no url")
                await session.goto(step.url)
            elif step.action_type == "answer_questions":
                await _replay_answer_questions(session, settings, provider, memory)
            else:
                raise ActionExecutionError(f"Unknown script step action_type: {step.action_type!r}")
        except Exception as exc:
            log.info("Script replay stopped at step %d (%s): %s", i, step.action_type, exc)
            return ReplayResult(completed=False, stopped_at_index=i, reason=str(exc), steps_completed=i)

    return ReplayResult(completed=True, stopped_at_index=None, reason=None, steps_completed=len(script.steps))


async def _replay_click(session: BrowserSession, step: ScriptStep, *, timeout_ms: int) -> None:
    if not step.selector:
        raise ActionExecutionError("Script click step has no selector")
    frames = session.frames()
    frame: Frame | None = None
    if step.frame_path:
        frame = resolve_frame(frames, step.frame_path)
    if frame is None:
        frame = frames[0] if frames else None
    if frame is None:
        raise ActionExecutionError("No frame available to replay a click against")

    locator = frame.locator(step.selector)
    await locator.scroll_into_view_if_needed(timeout=timeout_ms)
    await locator.click(timeout=timeout_ms)


async def run_script_with_fallback(
    script: Script,
    *,
    task,  # spark.scripts.model.Task — typed loosely to avoid a circular import with orchestrator.py
    session: BrowserSession,
    settings: AppSettings,
    provider: LLMProvider,
    recorder=None,
):
    """Replay ``script`` deterministically; if it stops partway through,
    hand off to a full AI-driven Orchestrator from wherever the browser
    currently sits, rather than restarting the task from scratch
    (BUILD_SPEC §9.2). Returns the Orchestrator's final ``RunResult`` either
    way — imported lazily to avoid a circular import (orchestrator.py
    itself imports this module for the recording side).
    """
    from spark.orchestrator import Orchestrator, RunOutcome, RunResult

    memory = RunMemory()
    replay_result = await replay_script(script, session=session, settings=settings, provider=provider, memory=memory)

    if replay_result.completed:
        log.info("Script replay completed all %d steps with no model calls for navigation", replay_result.steps_completed)
        return RunResult(RunOutcome.SUCCESS, "Completed via recorded script replay", 0, replay_result.steps_completed)

    log.info(
        "Falling back to AI mode after script step %d failed (%s); continuing from the browser's current state",
        replay_result.stopped_at_index,
        replay_result.reason,
    )
    orchestrator = Orchestrator(
        task=task,
        settings=settings,
        provider=provider,
        session=session,
        recorder=recorder,
        skip_initial_navigation=True,
    )
    orchestrator.memory = memory  # carry over whatever the script replay already read/answered
    return await orchestrator.run()


async def _replay_answer_questions(
    session: BrowserSession, settings: AppSettings, provider: LLMProvider, memory: RunMemory
) -> None:
    from spark.perception.ocr.registry import build_engine

    read_engine = build_engine(
        settings.ocr.read_engine, read_image_text=provider.read_image_text if settings.ocr.read_engine == "vision" else None
    )
    view = await build_page_view(session.page, ocr_settings=settings.ocr, read_engine=read_engine)
    groups = detect_question_groups(view)
    if not groups:
        raise ActionExecutionError("Script expected a question set on this page but none was detected")

    for group in groups:
        answer = await answer_question_group(provider=provider, group=group, memory=memory)
        await click_and_verify_answer(session.frames(), group, answer.chosen_option_id)
        memory.remember_answer(
            step_index=0,
            question=group.question_text,
            options=[o.name or o.nearby_text for o in group.options],
            chosen_option_id=answer.chosen_option_id,
            reasoning=answer.reasoning,
            citation=answer.citation,
            confidence=answer.confidence,
        )

    fresh = await build_page_view(session.page, ocr_settings=settings.ocr, read_engine=read_engine)
    submit = next(
        (e for e in fresh.elements if "submit" in e.name.lower() and not e.state.disabled),
        None,
    )
    if submit is not None:
        await click_element(session.frames(), submit)
