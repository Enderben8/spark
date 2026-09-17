"""Application settings.

Settings are split in two:

* :class:`AppSettings` — everything that is safe to write to a plain JSON/YAML
  file on disk (paths, budgets, provider *choice*, model ids, ...).
* Secrets (API keys) — never touch disk in plaintext. They live in the OS
  credential store via :mod:`keyring`, keyed by provider name.

This mirrors BUILD_SPEC.md §5 and §12.3.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PROVIDER_NAMES = ("gemini", "anthropic", "openai", "ollama", "stub")
OCR_ENGINE_NAMES = ("windows", "vision", "tesseract")


def _app_data_dir() -> Path:
    """Return the platform-appropriate per-user data directory for Spark.

    On Windows this is ``%LOCALAPPDATA%\\Spark``. Elsewhere (used for
    development/testing on Linux/macOS, since the shipped product targets
    Windows only) it falls back to ``~/.local/share/spark``.
    """
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "Spark"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "spark"
    return Path.home() / ".local" / "share" / "spark"


DATA_DIR = _app_data_dir()
RUNS_DIR = DATA_DIR / "runs"
SCRIPTS_DIR = DATA_DIR / "scripts"
CHROME_PROFILE_DIR = DATA_DIR / "chrome-profile"
SETTINGS_PATH = DATA_DIR / "settings.json"


class ProviderSettings(BaseModel):
    """Settings for one LLM provider. The API key itself is NOT here —
    see secrets.py. Only non-secret configuration lives on this model.
    """

    model: str = ""
    temperature: float = 0.2
    max_tokens: int = 2048
    base_url: str | None = None  # e.g. a local Ollama endpoint


class OcrSettings(BaseModel):
    read_engine: Literal["windows", "vision", "tesseract"] = "windows"
    escalation_engine: Literal["windows", "vision", "tesseract"] | None = "vision"
    geometry_engine: Literal["windows", "tesseract"] = "windows"
    escalation_confidence_threshold: float = 0.75
    escalation_min_chars: int = 100
    force_ocr: bool = False


class BudgetSettings(BaseModel):
    max_iterations: int = 25
    max_runtime_minutes: int = 60
    max_steps_per_iteration: int = 60
    max_model_calls: int = 500
    max_cost_usd: float = 2.00
    no_improvement_limit: int = 3


class RetentionSettings(BaseModel):
    keep_runs_days: int = 30
    screenshot_policy: Literal["question_and_score", "all", "none"] = (
        "question_and_score"
    )


class ChromeSettings(BaseModel):
    executable_path: str | None = None  # None = auto-detect
    debug_port: int = 9222
    profile_dir: str = str(CHROME_PROFILE_DIR)
    close_on_finish: bool = False


class SafetySettings(BaseModel):
    domain_allow_list: list[str] = Field(default_factory=list)
    blocked_action_patterns: list[str] = Field(
        default_factory=lambda: [
            "delete",
            "remove",
            "cancel subscription",
            "pay",
            "purchase",
            "confirm payment",
            "deactivate",
            "close account",
        ]
    )


class AppSettings(BaseModel):
    active_provider: Literal["gemini", "anthropic", "openai", "ollama", "stub"] = (
        "gemini"
    )
    providers: dict[str, ProviderSettings] = Field(
        default_factory=lambda: {
            # NOTE on every model id below: per BUILD_SPEC.md §6.7 and §15,
            # these are NOT to be trusted as current. Model lineups change
            # every few months and this file cannot verify anything at
            # import time. "gemini-2.5-flash" and "claude-sonnet-5" are the
            # two confirmed first-hand during this build (Google's own docs
            # for the former; this session's own identity for the latter).
            # The OpenAI default is deliberately conservative because
            # available search results for OpenAI's 2026 lineup were
            # inconsistent low-quality aggregator content, not something to
            # hard-code. Whoever deploys this MUST open each provider's own
            # model-list page (linked in reasoning/providers/*.py) and
            # correct these via Settings before relying on a provider.
            "gemini": ProviderSettings(model="gemini-2.5-flash"),
            "anthropic": ProviderSettings(model="claude-sonnet-5"),
            "openai": ProviderSettings(model="gpt-4.1-mini"),  # verify before use
            "ollama": ProviderSettings(
                model="llama3.2", base_url="http://localhost:11434"
            ),
        }
    )
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    budgets: BudgetSettings = Field(default_factory=BudgetSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    chrome: ChromeSettings = Field(default_factory=ChromeSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    log_level: str = "INFO"

    def provider_settings(self, name: str | None = None) -> ProviderSettings:
        name = name or self.active_provider
        return self.providers.get(name, ProviderSettings())


def load_settings(path: Path | None = None) -> AppSettings:
    path = path or SETTINGS_PATH
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppSettings()
    return AppSettings.model_validate(data)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    path = path or SETTINGS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")


def ensure_data_dirs() -> None:
    for d in (DATA_DIR, RUNS_DIR, SCRIPTS_DIR, CHROME_PROFILE_DIR):
        d.mkdir(parents=True, exist_ok=True)
