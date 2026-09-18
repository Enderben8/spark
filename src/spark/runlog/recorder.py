"""Per-run artefacts: a JSONL step log, screenshots, a memory dump, and a
human-readable report.md. See BUILD_SPEC.md §13.

Named ``runlog`` rather than the spec's literal ``logging/`` (BUILD_SPEC
§4's repo layout) to avoid any ambiguity with the stdlib ``logging`` module
this whole codebase already uses via ``spark.logsetup`` — a package named
``spark.logging`` risks confusing readers even though Python's own import
resolution would keep it distinct. Same job, clearer name.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from spark.config import AppSettings, RUNS_DIR, RetentionSettings
from spark.logsetup import get_logger
from spark.memory import RunMemory
from spark.reasoning.prompts import PROMPT_VERSION
from spark.scripts.model import Task

log = get_logger("runlog.recorder")


def _redact_settings(settings: AppSettings) -> dict:
    """No API keys live in AppSettings at all — they're in the OS keyring
    (see secrets.py) — so there is nothing to strip today. This function is
    still the single place that would need updating if a secret-shaped
    field is ever added to AppSettings (BUILD_SPEC §12.3/§13: "resolved
    settings, keys redacted").
    """
    return settings.model_dump()


def _safe_filename(name: str, *, max_len: int = 60) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    return safe[:max_len].strip("-") or "task"


class RunRecorder:
    """One instance per run. Created before the orchestrator starts,
    written to as the orchestrator progresses, finalised once at the end.
    """

    def __init__(self, *, task: Task, settings: AppSettings, base_dir: Path | None = None):
        self.task = task
        self.settings = settings
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = (base_dir or RUNS_DIR) / f"{timestamp}-{_safe_filename(task.name)}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self._jsonl_path = self.run_dir / "run.jsonl"
        self._jsonl_file = self._jsonl_path.open("a", encoding="utf-8")
        self._step_count = 0

    def record_step(self, **fields: object) -> None:
        entry = {"step": self._step_count, "at": datetime.now(timezone.utc).isoformat(), **fields}
        self._jsonl_file.write(json.dumps(entry, default=str) + "\n")
        self._jsonl_file.flush()
        self._step_count += 1

    def save_screenshot(self, png_bytes: bytes, label: str) -> str:
        path = self.run_dir / "screenshots" / f"{self._step_count:04d}-{_safe_filename(label, max_len=30)}.png"
        path.write_bytes(png_bytes)
        return str(path.relative_to(self.run_dir))

    def should_screenshot(self, page_type: str) -> bool:
        policy = self.settings.retention.screenshot_policy
        if policy == "all":
            return True
        if policy == "none":
            return False
        return page_type in ("questions", "score")  # "question_and_score", the default

    def finalize(self, *, outcome: str, message: str, iterations: int, steps: int, memory: RunMemory) -> None:
        (self.run_dir / "memory.json").write_text(memory.model_dump_json(indent=2), encoding="utf-8")
        (self.run_dir / "task.json").write_text(
            json.dumps(self.task.model_dump(), indent=2), encoding="utf-8"
        )
        (self.run_dir / "settings.json").write_text(
            json.dumps(_redact_settings(self.settings), indent=2), encoding="utf-8"
        )
        report = self._build_report(outcome=outcome, message=message, iterations=iterations, steps=steps, memory=memory)
        (self.run_dir / "report.md").write_text(report, encoding="utf-8")
        self._jsonl_file.close()
        log.info("Run artefacts written to %s", self.run_dir)

    def _build_report(self, *, outcome: str, message: str, iterations: int, steps: int, memory: RunMemory) -> str:
        lines = [
            f"# Run report: {self.task.name}",
            "",
            f"- **Outcome:** {outcome}",
            f"- **Message:** {message}",
            f"- **Iterations:** {iterations}",
            f"- **Steps:** {steps}",
            f"- **Prompt version:** {PROMPT_VERSION}",
            "",
            "## Score history",
            "",
        ]
        if memory.score_readings:
            for r in memory.score_readings:
                lines.append(f"- Step {r.step_index}: `{r.raw_text}` (target {r.target})")
        else:
            lines.append("(no score readings recorded)")

        lines += ["", "## Questions answered", ""]
        if memory.qa_history:
            known = [q for q in memory.qa_history if q.marked_correct is not None]
            if known:
                n_correct = sum(1 for q in known if q.marked_correct)
                lines.append(f"- {n_correct}/{len(known)} confirmed correct by the site")
            for q in memory.qa_history:
                lines.append(
                    f"- **{q.question}**\n"
                    f"  - chosen: option `{q.chosen_option_id}` (confidence {q.confidence:.2f})\n"
                    f"  - citation: {q.citation!r}"
                )
        else:
            lines.append("(no questions answered)")

        lines += ["", "## Passages read", ""]
        if memory.passages:
            for p in memory.passages:
                lines.append(f"- `{p.url}` ({p.source}, {len(p.text)} chars)")
        else:
            lines.append("(no passages recorded)")

        return "\n".join(lines) + "\n"


def purge_old_runs(retention: RetentionSettings, base_dir: Path | None = None) -> int:
    """Delete run directories older than ``retention.keep_runs_days``
    (BUILD_SPEC §12.3). Returns the number removed.
    """
    base = base_dir or RUNS_DIR
    if not base.exists():
        return 0
    cutoff = time.time() - retention.keep_runs_days * 86400
    removed = 0
    for entry in base.iterdir():
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
