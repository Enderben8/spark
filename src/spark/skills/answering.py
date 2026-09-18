"""Question detection and grounded answering. See BUILD_SPEC.md §6.9.

The crux of the whole tool: the passage and the questions about it are
usually on different screens (BUILD_SPEC §6.6). Every answer here is
grounded in :class:`~spark.memory.RunMemory`'s remembered passage text, not
in whatever the model happens to already "know" about the topic — the
prompt in reasoning/prompts.py explicitly forbids guessing from general
knowledge, and a low-confidence answer triggers one forced OCR re-read
before falling back to answering anyway (BUILD_SPEC §6.9 step 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from spark.browser.actions import ActionExecutionError, click_element, verify_checked
from spark.logsetup import get_logger
from spark.memory import RunMemory
from spark.perception.dom import InteractiveElement
from spark.perception.page_view import PageView
from spark.reasoning.prompts import build_answer_question_prompt
from spark.reasoning.provider import LLMProvider, Message, MessageRole
from spark.reasoning.schemas import AnsweredQuestion

log = get_logger("skills.answering")

MIN_OPTIONS_PER_QUESTION = 2
# [ASSUMPTION] BUILD_SPEC §6.9 step 3.
LOW_CONFIDENCE_THRESHOLD = 0.6
# How much passage text to hand the model per question (BUILD_SPEC §6.6).
MAX_PASSAGE_CONTEXT_CHARS = 12_000


@dataclass
class QuestionGroup:
    group_key: str
    question_text: str
    options: list[InteractiveElement]  # page order preserved


def detect_question_groups(view: PageView) -> list[QuestionGroup]:
    """Group radio/checkbox elements sharing an HTML ``name`` attribute into
    one question each, and recover each question's text by proximity
    (BUILD_SPEC §6.9: "a question string plus 2+ mutually exclusive option
    elements ... radio group, list of buttons, select, or labelled clickable
    divs"). Only the radio/checkbox case is implemented here — it's the
    shape the owner's own site uses; button-list and `<select>`-based
    question UIs are a plausible future extension, noted rather than
    speculatively built.
    """
    radios = [e for e in view.elements if e.role in ("radio", "checkbox") and e.group_name]

    groups: dict[str, list[InteractiveElement]] = {}
    order: list[str] = []
    for r in radios:
        key = f"{r.frame_path}::{r.group_name}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    result: list[QuestionGroup] = []
    for key in order:
        options = groups[key]
        if len(options) < MIN_OPTIONS_PER_QUESTION:
            continue
        question_text = _find_question_text(options, view)
        result.append(QuestionGroup(group_key=key, question_text=question_text, options=options))
    return result


def _find_question_text(options: list[InteractiveElement], view: PageView) -> str:
    """The closest text block above the WHOLE option group (in the same
    frame) — in practice, the question sentence immediately before its
    answer choices.

    Deliberately uses the group's minimum y as the cutoff, not the first
    option's own y: an option's own label is itself collected as a DOM text
    block (a `<label>` is a leaf block), and it sits at essentially the same
    height as its input — using a single option's y as the cutoff can pick
    up that option's own label instead of walking further up to the actual
    question text. Any block whose text exactly matches an option's own
    label is excluded outright as a second line of defence.
    """
    frame_path = options[0].frame_path
    option_labels = {option_label(o).strip() for o in options}
    top_y = min(o.bbox.y for o in options)

    candidates = [
        b
        for b in view.dom_text_blocks
        if b.frame_path == frame_path and b.bbox.y < top_y and b.text.strip() not in option_labels
    ]
    if not candidates:
        return ""
    candidates.sort(key=lambda b: b.bbox.y)
    return candidates[-1].text


def option_label(option: InteractiveElement) -> str:
    """The human-readable text of one answer option — the accessible name
    when the input has a real `<label>`, else the nearby-sibling text
    BUILD_SPEC §6.3 specifically added the DOM extractor to recover.
    """
    return option.name if option.name else option.nearby_text


async def answer_question_group(
    *,
    provider: LLMProvider,
    group: QuestionGroup,
    memory: RunMemory,
    force_low_confidence_reread: Callable[[], Awaitable[PageView]] | None = None,
) -> AnsweredQuestion:
    """Answer one question, grounded in remembered passage text. If
    confidence comes back below :data:`LOW_CONFIDENCE_THRESHOLD` and a
    re-read callback was supplied, re-read the current page with OCR forced
    (BUILD_SPEC §6.9 step 3) and retry exactly once before accepting
    whatever answer results.
    """
    option_texts = [option_label(o) for o in group.options]
    passage = memory.relevant_passages(group.question_text, max_chars=MAX_PASSAGE_CONTEXT_CHARS)

    async def _ask() -> AnsweredQuestion:
        message = Message(
            role=MessageRole.user,
            text=build_answer_question_prompt(
                question=group.question_text, options=option_texts, passage_text=passage
            ),
        )
        response = await provider.complete([message], schema=AnsweredQuestion, max_tokens=1024, temperature=0.0)
        return response.parsed

    answer = await _ask()

    if answer.confidence < LOW_CONFIDENCE_THRESHOLD and force_low_confidence_reread is not None:
        log.info(
            "Low-confidence answer (%.2f) for %r; forcing an OCR re-read and retrying once",
            answer.confidence,
            group.question_text,
        )
        try:
            fresh_view = await force_low_confidence_reread()
            if fresh_view.text and fresh_view.text not in passage:
                memory.remember_passage(
                    step_index=len(memory.passages),
                    url=fresh_view.url,
                    text=fresh_view.text,
                    source=fresh_view.text_source,
                )
                passage = memory.relevant_passages(group.question_text, max_chars=MAX_PASSAGE_CONTEXT_CHARS)
        except Exception as exc:
            log.warning("Forced re-read failed (%s); keeping the original low-confidence answer", exc)
        else:
            answer = await _ask()

    return answer


def option_id_to_element(group: QuestionGroup, chosen_option_id: str) -> InteractiveElement:
    """The prompt (build_answer_question_prompt) presents options as a
    0-indexed list; `chosen_option_id` comes back as that index, as a
    string (BUILD_SPEC §6.9's answer schema deliberately uses an opaque id
    rather than trusting the model to echo option text verbatim).
    """
    try:
        idx = int(chosen_option_id)
    except ValueError as exc:
        raise ActionExecutionError(
            f"Model returned a non-numeric chosen_option_id {chosen_option_id!r} for a "
            f"{len(group.options)}-option question"
        ) from exc
    if not (0 <= idx < len(group.options)):
        raise ActionExecutionError(
            f"Model returned out-of-range chosen_option_id {chosen_option_id!r} for a "
            f"{len(group.options)}-option question"
        )
    return group.options[idx]


async def click_and_verify_answer(frames, group: QuestionGroup, chosen_option_id: str) -> None:
    """Click the chosen option and verify the selection actually stuck
    (BUILD_SPEC §6.9 step 4: "An unverified click is a failed click.").
    """
    element = option_id_to_element(group, chosen_option_id)
    await click_element(frames, element)
    if not await verify_checked(frames, element):
        raise ActionExecutionError(
            f"Clicked option {chosen_option_id} for {group.group_key!r} but it did not "
            f"register as checked afterwards"
        )
