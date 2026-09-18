"""The run loop. See BUILD_SPEC.md §8 (the loop itself), §12.1 (budgets),
§12.2 (scope limits).

Perceive → classify → act, once per iteration of the while-loop below,
until the score target is reached, a budget is exhausted, or something
requires stopping and saying so loudly (BUILD_SPEC §7: "A run must never end
ambiguously.").
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from spark.browser.actions import ActionExecutionError, click_element, scroll_page
from spark.browser.session import BrowserSession
from spark.config import AppSettings
from spark.logsetup import get_logger
from spark.memory import RunMemory
from spark.perception.ocr.base import OcrEngine
from spark.perception.ocr.registry import build_engine
from spark.perception.page_view import PageView, build_page_view
from spark.reasoning.budget import BudgetTrackingProvider
from spark.reasoning.prompts import build_classify_page_prompt, build_observe_decide_prompt
from spark.reasoning.provider import LLMProvider, Message, MessageRole
from spark.reasoning.schemas import Action, ActionType, PageClassification
from spark.scripts.model import Task
from spark.skills.answering import answer_question_group, click_and_verify_answer, detect_question_groups
from spark.skills.scoring import ScoreComparison, extract_score, no_improvement_streak

log = get_logger("orchestrator")

_CONTINUE_BUTTON_PATTERN = re.compile(
    r"next|continue|try again|retry|carry on|again|next round|onward", re.IGNORECASE
)
_SUBMIT_BUTTON_PATTERN = re.compile(r"submit", re.IGNORECASE)


class RunOutcome(str, Enum):
    SUCCESS = "success"
    TARGET_NOT_REACHED = "target_not_reached"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass
class RunResult:
    outcome: RunOutcome
    message: str = ""
    iterations: int = 0
    steps: int = 0


def _registrable_domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _page_signature(view: PageView) -> str:
    """Identifies "the same page state" for stall/loop detection (BUILD_SPEC
    §8: "hash each PageView (URL + normalised text + element names)").
    """
    element_names = "|".join(sorted(e.name for e in view.elements if e.name))
    normalized_text = " ".join(view.text.split())[:2000]
    raw = f"{view.url}::{normalized_text}::{element_names}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Orchestrator:
    def __init__(
        self,
        *,
        task: Task,
        settings: AppSettings,
        provider: LLMProvider,
        session: BrowserSession,
    ):
        self.task = task
        self.settings = settings
        self.provider = BudgetTrackingProvider(provider)
        self.session = session
        self.memory = RunMemory()
        self.recent_actions: list[str] = []
        self._start_time = time.monotonic()
        self._allowed_domain = _registrable_domain(task.start_url)
        self._cancelled = False
        self._cached_continue_selector: str | None = None
        self._recovery_attempted = False

        self._read_engine: OcrEngine = build_engine(
            task.perception.ocr_read_engine,
            read_image_text=self.provider.read_image_text if task.perception.ocr_read_engine == "vision" else None,
        )
        self._escalation_engine: OcrEngine | None = None
        escalation_name = settings.ocr.escalation_engine
        if escalation_name and escalation_name != task.perception.ocr_read_engine:
            try:
                self._escalation_engine = build_engine(
                    escalation_name,
                    read_image_text=self.provider.read_image_text if escalation_name == "vision" else None,
                )
            except ValueError:
                self._escalation_engine = None

    def cancel(self) -> None:
        """Cooperative cancellation — checked between every step (BUILD_SPEC
        §10: "Stop must be responsive ... within a second or two").
        """
        self._cancelled = True

    async def _perceive(self, *, force_ocr: bool = False) -> PageView:
        await self.session.wait_for_settle()
        return await build_page_view(
            self.session.page,
            ocr_settings=self.settings.ocr,
            read_engine=self._read_engine,
            escalation_engine=self._escalation_engine,
            force_ocr=self.task.perception.force_ocr or force_ocr,
        )

    async def run(self) -> RunResult:
        await self.session.goto(self.task.start_url)

        iteration = 0
        step = 0
        steps_this_iteration = 0
        comparison = ScoreComparison(
            target=self.task.stop.score_target,
            cumulative=self.task.score.cumulative,
            comparison=self.task.stop.comparison,
        )

        while True:
            if self._cancelled:
                return RunResult(RunOutcome.FAILED, "Cancelled by user", iteration, step)

            elapsed_minutes = (time.monotonic() - self._start_time) / 60
            if elapsed_minutes > self.task.stop.max_runtime_minutes:
                return RunResult(
                    RunOutcome.BUDGET_EXCEEDED,
                    f"Exceeded max_runtime_minutes ({self.task.stop.max_runtime_minutes})",
                    iteration,
                    step,
                )
            if iteration >= self.task.stop.max_iterations:
                return RunResult(
                    RunOutcome.TARGET_NOT_REACHED,
                    f"Reached max_iterations ({self.task.stop.max_iterations}) without hitting the target",
                    iteration,
                    step,
                )

            if self.provider.tracker.calls_made >= self.settings.budgets.max_model_calls:
                return RunResult(
                    RunOutcome.BUDGET_EXCEEDED,
                    f"Exceeded max_model_calls ({self.settings.budgets.max_model_calls})",
                    iteration,
                    step,
                )
            estimated_cost = self.provider.tracker.estimate_cost_usd(self.settings.provider_settings())
            cost_ceiling = min(self.settings.budgets.max_cost_usd, self.task.limits.max_cost_usd)
            if estimated_cost is not None and estimated_cost > cost_ceiling:
                return RunResult(
                    RunOutcome.BUDGET_EXCEEDED,
                    f"Estimated cost ${estimated_cost:.4f} exceeded the ${cost_ceiling:.2f} budget",
                    iteration,
                    step,
                )
            if steps_this_iteration >= self.task.limits.max_steps_per_iteration:
                return RunResult(
                    RunOutcome.BUDGET_EXCEEDED,
                    f"Exceeded max_steps_per_iteration ({self.task.limits.max_steps_per_iteration}) "
                    f"without completing this round — likely stuck dithering on one screen",
                    iteration,
                    step,
                )

            current_domain = _registrable_domain(self.session.current_url)
            if current_domain and current_domain != self._allowed_domain:
                return RunResult(
                    RunOutcome.FAILED,
                    f"Navigated outside the allowed domain: {current_domain} (allowed: {self._allowed_domain})",
                    iteration,
                    step,
                )

            view = await self._perceive()

            signature = _page_signature(view)
            self.memory.mark_visited(signature)
            if self.memory.recent_visit_count(signature, last_n=3) >= 3:
                if self._recovery_attempted:
                    return RunResult(
                        RunOutcome.FAILED,
                        "Stalled: identical page state repeated after a recovery attempt",
                        iteration,
                        step,
                    )
                log.warning("Stall detected (same page state 3 times); attempting one recovery (reload)")
                self._recovery_attempted = True
                await self.session.goto(self.session.current_url)
                step += 1
                steps_this_iteration += 1
                continue

            page_type = await self._classify(view)
            log.info("Step %d: classified as %s (%s)", step, page_type, view.url)

            if page_type == "reading":
                self.memory.remember_passage(step_index=step, url=view.url, text=view.text, source=view.text_source)
                await self._advance(view)
            elif page_type == "questions":
                await self._answer_all_questions(view, step)
                iteration += 1
                steps_this_iteration = 0
            elif page_type == "score":
                outcome = await self._handle_score_page(view, comparison, iteration, step)
                if outcome is not None:
                    return outcome
            elif page_type == "blocked":
                return RunResult(
                    RunOutcome.FAILED, "Blocked: login wall, CAPTCHA, or error page detected", iteration, step
                )
            else:
                await self._advance(view)

            step += 1
            steps_this_iteration += 1

    async def _handle_score_page(
        self, view: PageView, comparison: ScoreComparison, iteration: int, step: int
    ) -> RunResult | None:
        reading = await extract_score(
            provider=self.provider,
            page_text=view.text,
            hint_regex=self.task.score.regex,
        )
        if reading is None:
            return RunResult(RunOutcome.FAILED, "On a score page but could not extract a score", iteration, step)

        warning = comparison.check_regression(reading, self.memory)
        if warning:
            log.warning(warning)

        self.memory.remember_score(
            step_index=step,
            raw_text=reading.raw,
            value=reading.value,
            maximum=reading.maximum,
            is_percentage=reading.is_percentage,
            target=self.task.stop.score_target,
        )
        log.info("Score: %s (target %s %s)", reading.raw, comparison.comparison, comparison.target)

        if comparison.target_reached(reading):
            return RunResult(RunOutcome.SUCCESS, f"Target reached: {reading.raw}", iteration, step)

        if no_improvement_streak(self.memory, self.settings.budgets.no_improvement_limit):
            return RunResult(
                RunOutcome.TARGET_NOT_REACHED,
                "Score has not improved across consecutive iterations; stopping rather than "
                "grinding a broken loop",
                iteration,
                step,
            )

        await self._perform_loop_action(view)
        return None

    async def _classify(self, view: PageView) -> str:
        message = Message(
            role=MessageRole.user,
            text=build_classify_page_prompt(page_text_excerpt=view.text[:4000], elements=view.elements),
        )
        response = await self.provider.complete([message], schema=PageClassification, max_tokens=256, temperature=0.0)
        classification: PageClassification = response.parsed
        return classification.page_type

    async def _advance(self, view: PageView) -> None:
        message = Message(
            role=MessageRole.user,
            text=build_observe_decide_prompt(
                goal=self.task.goal,
                page_text_excerpt=view.text[:4000],
                elements=view.elements,
                recent_actions=self.recent_actions,
            ),
        )
        response = await self.provider.complete([message], schema=Action, max_tokens=512, temperature=0.0)
        action: Action = response.parsed
        await self._execute(action, view)

    async def _execute(self, action: Action, view: PageView) -> None:
        blocked_patterns = self.settings.safety.blocked_action_patterns
        if action.element_id:
            element = next((e for e in view.elements if e.id == action.element_id), None)
            if element and any(p.lower() in element.name.lower() for p in blocked_patterns):
                raise ActionExecutionError(
                    f"Refusing to {action.type.value} element {element.id} ('{element.name}'): "
                    f"matches a blocked-action pattern (BUILD_SPEC §12.2)"
                )
        else:
            element = None

        if action.type == ActionType.click and element is not None:
            await click_element(self.session.frames(), element)
        elif action.type == ActionType.navigate and action.url:
            await self.session.goto(action.url)
        elif action.type == ActionType.scroll:
            await scroll_page(self.session.page, direction=action.direction or "down")
        elif action.type in (ActionType.wait, ActionType.read_more):
            pass  # handled by the caller re-perceiving with force_ocr on the next loop

        self.recent_actions.append(f"{action.type.value}: {action.reason}")

    async def _answer_all_questions(self, view: PageView, step: int) -> None:
        groups = detect_question_groups(view)
        for group in groups:

            async def _reread() -> PageView:
                return await self._perceive(force_ocr=True)

            answer = await answer_question_group(
                provider=self.provider, group=group, memory=self.memory, force_low_confidence_reread=_reread
            )
            await click_and_verify_answer(self.session.frames(), group, answer.chosen_option_id)
            self.memory.remember_answer(
                step_index=step,
                question=group.question_text,
                options=[o.name or o.nearby_text for o in group.options],
                chosen_option_id=answer.chosen_option_id,
                reasoning=answer.reasoning,
                citation=answer.citation,
                confidence=answer.confidence,
            )

        # Always re-perceive before looking for Submit: clicking each answer
        # option can enable a previously-disabled submit button, so the
        # `view` snapshot from before answering (which still shows it
        # disabled) must never be used for this check. Using the stale view
        # here previously meant Submit was never found on the productive
        # pass, and the run only "succeeded" by accident on a later outer-
        # loop iteration that re-answered the same (already-checked, now
        # harmless-to-reclick) questions and re-submitted — double-counting
        # both memory.qa_history and the site's own score.
        fresh = await self._perceive()
        submit = next(
            (e for e in fresh.elements if _SUBMIT_BUTTON_PATTERN.search(e.name) and not e.state.disabled),
            None,
        )
        if submit is not None:
            await click_element(self.session.frames(), submit)
        else:
            log.warning("Answered all questions but found no enabled Submit-like button afterwards")

    async def _perform_loop_action(self, view: PageView) -> None:
        if self.task.loop_action.type == "navigate" and self.task.loop_action.url:
            await self.session.goto(self.task.loop_action.url)
            return

        if self._cached_continue_selector:
            cached = next((e for e in view.elements if e.selector == self._cached_continue_selector), None)
            if cached is not None:
                await click_element(self.session.frames(), cached)
                return

        label = self.task.loop_action.button_text
        candidate = None
        if label:
            candidate = next((e for e in view.elements if e.name.strip().lower() == label.strip().lower()), None)
        if candidate is None:
            candidate = next(
                (e for e in view.elements if e.role in ("button", "link") and _CONTINUE_BUTTON_PATTERN.search(e.name)),
                None,
            )
        if candidate is None:
            # Last resort: ask the model (BUILD_SPEC §6.10).
            await self._advance(view)
            return

        self._cached_continue_selector = candidate.selector
        await click_element(self.session.frames(), candidate)
