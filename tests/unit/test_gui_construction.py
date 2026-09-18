"""M9 smoke tests: the GUI constructs and its widgets wire up correctly in
an offscreen Qt platform (no real display in this sandbox — see
BUILD_SPEC.md §15/§16: full visual verification still needs a real Windows
desktop; this only proves the code runs and the object graph is sound).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from spark.config import AppSettings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_constructs(qapp, tmp_path, monkeypatch):
    import spark.config as config_module

    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config_module, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config_module, "SCRIPTS_DIR", tmp_path / "scripts")
    monkeypatch.setattr(config_module, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(config_module, "CHROME_PROFILE_DIR", tmp_path / "chrome-profile")
    monkeypatch.setattr(config_module, "SETTINGS_PATH", tmp_path / "settings.json")

    from spark.gui.main_window import MainWindow

    window = MainWindow()
    assert window.windowTitle() == "Spark"
    assert window.start_btn.isEnabled() is True
    assert window.stop_btn.isEnabled() is False
    window.close()


def test_settings_dialog_round_trips_provider_choice(qapp):
    from spark.gui.settings_dialog import SettingsDialog

    settings = AppSettings()
    dialog = SettingsDialog(settings)
    dialog.provider_combo.setCurrentText("anthropic")
    dialog.model_edit.setText("claude-sonnet-5")
    dialog.max_iterations_spin.setValue(42)

    result = dialog.result_settings()
    assert result.active_provider == "anthropic"
    assert result.providers["anthropic"].model == "claude-sonnet-5"
    assert result.budgets.max_iterations == 42
    dialog.close()


def test_settings_dialog_escalation_none_option(qapp):
    from spark.gui.settings_dialog import SettingsDialog

    settings = AppSettings()
    dialog = SettingsDialog(settings)
    dialog.escalation_engine_combo.setCurrentText("(none)")
    result = dialog.result_settings()
    assert result.ocr.escalation_engine is None
    dialog.close()


def test_qt_log_handler_emits_signal(qapp):
    import logging

    from spark.gui.log_view import QtLogHandler, QtLogSignal

    signal_obj = QtLogSignal()
    received = []
    signal_obj.message.connect(lambda level, msg: received.append((level, msg)))

    handler = QtLogHandler(signal_obj)
    logger = logging.getLogger("spark.test_gui_logging")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("hello from the log pane")

    assert len(received) == 1
    assert received[0][0] == "INFO"
    assert "hello from the log pane" in received[0][1]
    logger.removeHandler(handler)
