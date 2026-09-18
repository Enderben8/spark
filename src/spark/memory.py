"""Per-run memory: what Spark has read, what it's been asked, and what score
it has seen. See BUILD_SPEC.md §6.6.

This is the answer to the single most common way a naive version of this
tool fails: the passage and the questions about it are on different
screens. An agent that navigates away and re-perceives from scratch has
nothing left to answer from except the model's general knowledge — which is
exactly what BUILD_SPEC forbids (§6.9: "answer only from the provided
passage text; if the passage does not contain the answer, say so rather
than guessing"). A passage is never dropped for the duration of a run.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class PassageRecord(BaseModel):
    step_index: int
    url: str
    text: str
    source: str  # PageView.text_source: "dom" | "ocr" | "dom+ocr"
    captured_at: datetime


class QARecord(BaseModel):
    step_index: int
    question: str
    options: list[str]
    chosen_option_id: str
    reasoning: str
    citation: str
    confidence: float
    marked_correct: bool | None = None  # filled in later if the site reveals it


class ScoreRecord(BaseModel):
    step_index: int
    raw_text: str
    value: float
    maximum: float | None
    is_percentage: bool
    target: float
    captured_at: datetime


class RunMemory(BaseModel):
    passages: list[PassageRecord] = Field(default_factory=list)
    qa_history: list[QARecord] = Field(default_factory=list)
    score_readings: list[ScoreRecord] = Field(default_factory=list)
    visited_signatures: list[str] = Field(default_factory=list)

    def remember_passage(self, *, step_index: int, url: str, text: str, source: str) -> None:
        self.passages.append(
            PassageRecord(
                step_index=step_index,
                url=url,
                text=text,
                source=source,
                captured_at=datetime.now(timezone.utc),
            )
        )

    def remember_answer(
        self,
        *,
        step_index: int,
        question: str,
        options: list[str],
        chosen_option_id: str,
        reasoning: str,
        citation: str,
        confidence: float,
    ) -> None:
        self.qa_history.append(
            QARecord(
                step_index=step_index,
                question=question,
                options=options,
                chosen_option_id=chosen_option_id,
                reasoning=reasoning,
                citation=citation,
                confidence=confidence,
            )
        )

    def remember_score(
        self, *, step_index: int, raw_text: str, value: float, maximum: float | None, is_percentage: bool, target: float
    ) -> None:
        self.score_readings.append(
            ScoreRecord(
                step_index=step_index,
                raw_text=raw_text,
                value=value,
                maximum=maximum,
                is_percentage=is_percentage,
                target=target,
                captured_at=datetime.now(timezone.utc),
            )
        )

    def mark_visited(self, signature: str) -> None:
        self.visited_signatures.append(signature)

    def recent_visit_count(self, signature: str, last_n: int) -> int:
        return self.visited_signatures[-last_n:].count(signature)

    @property
    def latest_score(self) -> ScoreRecord | None:
        return self.score_readings[-1] if self.score_readings else None

    @property
    def all_passage_text(self) -> str:
        """All remembered passage text, in the order it was read. Used as
        the fallback when relevant-passage selection (BUILD_SPEC §6.6:
        "select by recency and lexical overlap ... do not silently truncate
        from the front") needs a starting corpus to search.
        """
        return "\n\n".join(p.text for p in self.passages)

    def relevant_passages(self, query: str, *, max_chars: int) -> str:
        """Select passage text to ground an answer in, when the full
        remembered corpus would exceed the context budget. Prefers passages
        with more lexical overlap with ``query`` (the question being
        answered), breaking ties by recency — never truncates from the
        front of a single passage, which would arbitrarily cut whichever
        text happened to be read first (BUILD_SPEC §6.6).
        """
        if not self.passages:
            return ""
        total_len = sum(len(p.text) for p in self.passages)
        if total_len <= max_chars:
            return self.all_passage_text

        query_words = {w.lower().strip(".,?!\"'") for w in query.split() if len(w) > 3}

        def overlap_score(text: str) -> int:
            text_words = {w.lower().strip(".,?!\"'") for w in text.split()}
            return len(query_words & text_words)

        ranked = sorted(
            enumerate(self.passages),
            key=lambda pair: (overlap_score(pair[1].text), pair[0]),
            reverse=True,
        )

        selected: list[str] = []
        used = 0
        for _, passage in ranked:
            if used + len(passage.text) > max_chars and selected:
                continue
            selected.append(passage.text)
            used += len(passage.text)
            if used >= max_chars:
                break
        return "\n\n".join(selected)
