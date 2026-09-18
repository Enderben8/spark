"""Score detection, parsing, and the loop-until-target stop condition.

See BUILD_SPEC.md §6.10. The two things most likely to go subtly wrong here
(both called out explicitly in the spec, both worth restating):

1. **Never derive a percentage when no maximum was actually shown.** A
   points score compared as a percentage — or vice versa — produces a stop
   condition that either never fires or fires immediately.
2. **Never sum readings that are already cumulative.** If the site itself
   maintains the running total (the confirmed case for the owner's site —
   BUILD_SPEC §16), Spark compares the latest reading against the target.
   Summing on top of that double-counts and stops the run early while
   reporting success.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from spark.logsetup import get_logger
from spark.memory import RunMemory
from spark.reasoning.prompts import build_extract_score_prompt
from spark.reasoning.provider import LLMProvider, Message, MessageRole
from spark.reasoning.schemas import ScoreExtraction

log = get_logger("skills.scoring")


@dataclass
class ScoreReading:
    raw: str
    value: float
    maximum: float | None
    is_percentage: bool


def regex_sweep(text: str) -> ScoreReading | None:
    """Priority 3 detection (BUILD_SPEC §6.10): a plain regex sweep for
    common score shapes — used when there's no task-file hint and the AI
    extraction path is unavailable or came back empty. Order matters: the
    more specific shapes (percentage, x/y) are tried before the bare
    "N points" / "Score: N" shapes, so "12/20" isn't misread as a lone "20".
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if m:
        return ScoreReading(raw=m.group(0), value=float(m.group(1)), maximum=100.0, is_percentage=True)

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        return ScoreReading(
            raw=m.group(0), value=float(m.group(1)), maximum=float(m.group(2)), is_percentage=False
        )

    m = re.search(r"score[:\s]+(\d+(?:\.\d+)?)\b", text, re.IGNORECASE)
    if m:
        return ScoreReading(raw=m.group(0), value=float(m.group(1)), maximum=None, is_percentage=False)

    m = re.search(r"(\d+(?:\.\d+)?)\s*points\b", text, re.IGNORECASE)
    if m:
        return ScoreReading(raw=m.group(0), value=float(m.group(1)), maximum=None, is_percentage=False)

    return None


def detect_by_hint(hint_text: str | None, hint_regex: str | None) -> ScoreReading | None:
    """Priority 1 (BUILD_SPEC §6.10): a task-file selector or regex hint
    always wins when present. ``hint_text`` is the live text already
    extracted by the caller from the configured CSS selector (DOM lookups
    belong in the orchestrator, not here); ``hint_regex`` is applied against
    the full page text by the caller if given.
    """
    if hint_regex:
        return None  # caller applies hint_regex directly against page text via regex_sweep-style match; see extract_score
    if hint_text:
        m = re.search(r"(\d+(?:\.\d+)?)", hint_text)
        if m:
            return ScoreReading(
                raw=hint_text.strip(), value=float(m.group(1)), maximum=None, is_percentage="%" in hint_text
            )
    return None


async def extract_score(
    *,
    provider: LLMProvider,
    page_text: str,
    hint_selector_text: str | None = None,
    hint_regex: str | None = None,
) -> ScoreReading | None:
    """Full detection cascade (BUILD_SPEC §6.10 priority order): task-file
    hint > AI classify+extract > regex sweep.
    """
    if hint_regex:
        m = re.search(hint_regex, page_text)
        if m and m.groups():
            return ScoreReading(
                raw=m.group(0), value=float(m.group(1)), maximum=None, is_percentage="%" in m.group(0)
            )

    hinted = detect_by_hint(hint_selector_text, hint_regex)
    if hinted:
        return hinted

    extraction: ScoreExtraction | None = None
    try:
        message = Message(role=MessageRole.user, text=build_extract_score_prompt(page_text=page_text))
        response = await provider.complete([message], schema=ScoreExtraction, max_tokens=512, temperature=0.0)
        extraction = response.parsed
    except Exception as exc:
        log.warning("AI score extraction failed (%s); falling back to regex sweep", exc)

    if extraction is not None and extraction.found and extraction.value is not None:
        return ScoreReading(
            raw=extraction.raw_text or str(extraction.value),
            value=extraction.value,
            maximum=extraction.maximum,
            is_percentage=extraction.is_percentage,
        )

    return regex_sweep(page_text)


class ScoreComparison:
    """The stop-condition decision from BUILD_SPEC §6.10, given a task's
    configured target, cumulative-vs-per-round semantics, and comparison
    operator.
    """

    def __init__(self, *, target: float, cumulative: bool = True, comparison: str = ">="):
        if comparison not in (">=", ">", "=="):
            raise ValueError(f"Unsupported comparison operator: {comparison!r}")
        self.target = target
        self.cumulative = cumulative
        self.comparison = comparison

    def target_reached(self, reading: ScoreReading) -> bool:
        if self.comparison == ">=":
            return reading.value >= self.target
        if self.comparison == ">":
            return reading.value > self.target
        return reading.value == self.target

    def check_regression(self, reading: ScoreReading, memory: RunMemory) -> str | None:
        """BUILD_SPEC §6.10 sanity check: in cumulative mode, a reading
        LOWER than the previous one means a reset, a misread, or a new
        session — never progress. Returns a warning string, or None.
        """
        if not self.cumulative:
            return None
        previous = memory.latest_score
        if previous is not None and reading.value < previous.value:
            return (
                f"Score reading ({reading.value}) is lower than the previous cumulative "
                f"reading ({previous.value}) — treating as a reset/misread/new session, "
                f"not as progress."
            )
        return None


def no_improvement_streak(memory: RunMemory, n: int) -> bool:
    """True when the last ``n`` score readings show no increase at all
    (BUILD_SPEC §6.10 guard rail — "stop if the score fails to improve
    across N consecutive iterations ... grinding a broken loop is worse
    than stopping"). Needs at least ``n`` readings to judge; returns False
    otherwise (not enough evidence yet, not "no improvement").
    """
    readings = memory.score_readings[-n:]
    if len(readings) < n:
        return False
    values = [r.value for r in readings]
    return all(b <= a for a, b in zip(values, values[1:]))
