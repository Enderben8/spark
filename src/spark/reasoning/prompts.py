"""All prompt text lives here, versioned, per BUILD_SPEC.md §6.8. The
version is recorded in every run log (BUILD_SPEC §13) so a behaviour change
traced back to a prompt edit is diagnosable after the fact.

Four prompts: observe/decide (§7/§8), answer questions (§6.9), extract
score (§6.10), classify page (§8). Each builder function returns plain
prompt text — callers wrap it in a ``reasoning.provider.Message`` alongside
whatever images/schema the call needs.
"""
from __future__ import annotations

from spark.perception.dom import InteractiveElement

PROMPT_VERSION = "2026-09-17.1"

_ELEMENT_LIMIT = 80  # keep the inventory compact; see format_elements docstring


def format_elements(elements: list[InteractiveElement], *, limit: int = _ELEMENT_LIMIT) -> str:
    """Render the interactive-element inventory as a compact numbered list —
    id, role, name, nearby text — never raw HTML (BUILD_SPEC §6.8: "give the
    element inventory as a compact numbered list ... not raw HTML").
    Truncated to ``limit`` entries (in-viewport elements first) so a
    cluttered page doesn't blow the context budget; callers should prefer
    re-perceiving a shorter list over raising this limit.
    """
    ordered = sorted(elements, key=lambda e: (not e.in_viewport,))
    lines = []
    for e in ordered[:limit]:
        state_bits = []
        if e.state.checked:
            state_bits.append("checked")
        if e.state.disabled:
            state_bits.append("disabled")
        if e.state.selected:
            state_bits.append("selected")
        state_str = f" [{', '.join(state_bits)}]" if state_bits else ""
        nearby = f" (near: {e.nearby_text[:80]!r})" if e.nearby_text and e.nearby_text != e.name else ""
        lines.append(f"{e.id}: {e.role} '{e.name}'{state_str}{nearby}")
    if len(elements) > limit:
        lines.append(f"... and {len(elements) - limit} more elements not shown")
    return "\n".join(lines) if lines else "(no interactive elements found)"


def format_history(recent_actions: list[str], *, limit: int = 8) -> str:
    if not recent_actions:
        return "(no actions taken yet this run)"
    return "\n".join(f"- {a}" for a in recent_actions[-limit:])


def build_observe_decide_prompt(
    *,
    goal: str,
    page_text_excerpt: str,
    elements: list[InteractiveElement],
    recent_actions: list[str],
) -> str:
    return f"""You are operating a web browser to accomplish this goal:
{goal}

Current page text (may be truncated):
---
{page_text_excerpt}
---

Interactive elements on the current page:
{format_elements(elements)}

Recent actions and their outcomes:
{format_history(recent_actions)}

Decide the single next action to take. Rules:
- Only ever answer using information actually present on this page or in
  memory of what was already read — never invent page content.
- If a "Next" or "Continue" style control is visible and there is nothing
  else to do on this page, take it.
- If comprehension questions are visible, use the answer_questions action
  type rather than clicking options yourself.
- If a score is visible, use the report_score action type.
- Prefer the fewest steps that make real progress toward the goal.
- Set `expectation` to what you expect to observe after this action
  succeeds (e.g. "a question set should appear"), so the result can be
  verified.
"""


def build_answer_question_prompt(
    *,
    question: str,
    options: list[str],
    passage_text: str,
) -> str:
    return f"""Answer this multiple-choice question using ONLY the passage
text below. If the passage does not contain enough information to answer
confidently, say so in your reasoning and lower your confidence accordingly
— do not guess from general knowledge.

Passage:
---
{passage_text}
---

Question: {question}

Options:
{chr(10).join(f"{i}: {opt}" for i, opt in enumerate(options))}

Respond with the option's index as chosen_option_id (as a string), your
confidence (0 to 1), a short citation quoting the exact part of the passage
that supports your answer, and your reasoning.
"""


def build_button_question_prompt(
    *,
    page_text: str,
    elements: list[InteractiveElement],
    passage_text: str,
) -> str:
    return f"""The current page shows a question with several answer choices,
each of which is a clickable button. Answer it using ONLY the passage text
below. If the passage does not contain enough information to answer
confidently, say so in your reasoning and lower your confidence — do not
guess from general knowledge.

Passage that was read earlier:
---
{passage_text}
---

Text currently on the page (the question and its choices):
---
{page_text}
---

Clickable elements on the page:
{format_elements(elements)}

Pick the ONE element that is the correct answer choice and return its id
(for example "e7") as chosen_element_id. It must be an answer choice — never
a navigation, menu, or "next" style control. Also return the question text,
your confidence (0 to 1), a short citation quoting the exact part of the
passage that supports the answer, and your reasoning.
"""


def build_extract_score_prompt(*, page_text: str) -> str:
    return f"""The following page text may contain a score. Find it and
report it. A score is commonly shown as a percentage (e.g. "80%"), a count
out of a total (e.g. "12/20" or "12 out of 20"), or a plain points number
with no maximum (e.g. "Score: 450"). Do not confuse a percentage with a
points score — only set is_percentage=true if a '%' sign or the word
"percent" is actually present next to the number. If no score is present on
this page, set found=false and leave the other fields empty.

Page text:
---
{page_text}
---
"""


def build_classify_page_prompt(*, page_text_excerpt: str, elements: list[InteractiveElement]) -> str:
    return f"""Classify the current page into exactly one category:
- "reading": primarily a passage of text to read, with a way to move on
- "questions": one or more comprehension/quiz questions to answer
- "score": a score or result is being displayed
- "navigation": a menu, list of links, or other way to choose where to go
- "blocked": a login wall, CAPTCHA, error page, or other obstacle
- "other": none of the above fit

Page text (may be truncated):
---
{page_text_excerpt}
---

Interactive elements:
{format_elements(elements, limit=30)}
"""
