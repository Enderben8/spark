from pathlib import Path

from spark.config import AppSettings, load_settings, save_settings


def test_default_settings_roundtrip(tmp_path: Path) -> None:
    settings = AppSettings()
    path = tmp_path / "settings.json"
    save_settings(settings, path)
    loaded = load_settings(path)
    assert loaded == settings


def test_missing_settings_file_returns_defaults(tmp_path: Path) -> None:
    loaded = load_settings(tmp_path / "does-not-exist.json")
    assert loaded.active_provider == "gemini"
    assert loaded.ocr.read_engine == "windows"
    assert loaded.ocr.escalation_engine == "vision"


def test_score_cumulative_default_is_sane() -> None:
    settings = AppSettings()
    assert settings.budgets.max_iterations == 25
    assert settings.retention.screenshot_policy == "question_and_score"
