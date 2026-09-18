"""Settings dialog. See BUILD_SPEC.md §10: provider + model + API key
(stored via keyring, never in the plain settings file), OCR engines, Chrome
path/port/profile, budgets, and the "Set up browser profile" button that
BUILD_SPEC §2.1/§6.11 require for the one-time manual sign-in flow.
"""
from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from spark.config import AppSettings, PROVIDER_NAMES, OCR_ENGINE_NAMES
from spark.logsetup import get_logger

log = get_logger("gui.settings_dialog")


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Spark Settings")
        self.settings = settings.model_copy(deep=True)

        tabs = QTabWidget()
        tabs.addTab(self._build_provider_tab(), "AI Provider")
        tabs.addTab(self._build_perception_tab(), "Reading (OCR)")
        tabs.addTab(self._build_chrome_tab(), "Browser")
        tabs.addTab(self._build_budgets_tab(), "Budgets && Safety")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # -- AI provider ---------------------------------------------------

    def _build_provider_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.provider_combo = QComboBox()
        self.provider_combo.addItems(PROVIDER_NAMES)
        self.provider_combo.setCurrentText(self.settings.active_provider)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("Provider:", self.provider_combo)

        self.model_edit = QLineEdit()
        form.addRow("Model:", self.model_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Leave blank to keep the currently saved key")
        form.addRow("API key:", self.api_key_edit)

        save_key_btn = QPushButton("Save API key to OS credential store")
        save_key_btn.clicked.connect(self._save_api_key)
        form.addRow("", save_key_btn)

        note = QLabel(
            "API keys are stored in your OS credential store (Windows Credential\n"
            "Manager), never written to a plain settings file. See BUILD_SPEC §12.3."
        )
        note.setWordWrap(True)
        form.addRow(note)

        self._on_provider_changed(self.provider_combo.currentText())
        return widget

    def _on_provider_changed(self, name: str) -> None:
        ps = self.settings.provider_settings(name)
        self.model_edit.setText(ps.model)

    def _save_api_key(self) -> None:
        key = self.api_key_edit.text().strip()
        if not key:
            QMessageBox.information(self, "No key entered", "Type an API key first.")
            return
        provider = self.provider_combo.currentText()
        try:
            from spark.secrets import set_api_key

            set_api_key(provider, key)
        except Exception as exc:
            QMessageBox.warning(self, "Could not save key", str(exc))
            return
        self.api_key_edit.clear()
        QMessageBox.information(self, "Saved", f"API key for {provider} saved to the OS credential store.")

    # -- OCR -------------------------------------------------------------

    def _build_perception_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.read_engine_combo = QComboBox()
        self.read_engine_combo.addItems(OCR_ENGINE_NAMES)
        self.read_engine_combo.setCurrentText(self.settings.ocr.read_engine)
        form.addRow("Read engine:", self.read_engine_combo)

        self.escalation_engine_combo = QComboBox()
        self.escalation_engine_combo.addItems(["(none)", *OCR_ENGINE_NAMES])
        self.escalation_engine_combo.setCurrentText(self.settings.ocr.escalation_engine or "(none)")
        form.addRow("Escalation engine:", self.escalation_engine_combo)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setValue(self.settings.ocr.escalation_confidence_threshold)
        form.addRow("Escalation confidence threshold:", self.confidence_spin)

        note = QLabel(
            "Reading text and locating a click target are different jobs (BUILD_SPEC §6.4):\n"
            "only a geometry-capable engine (windows/tesseract) can be used to click something\n"
            "that exists only in pixels."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return widget

    # -- Chrome ------------------------------------------------------------

    def _build_chrome_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.chrome_path_edit = QLineEdit(self.settings.chrome.executable_path or "")
        self.chrome_path_edit.setPlaceholderText("Auto-detect")
        form.addRow("Chrome executable:", self.chrome_path_edit)

        self.chrome_port_spin = QSpinBox()
        self.chrome_port_spin.setRange(1024, 65535)
        self.chrome_port_spin.setValue(self.settings.chrome.debug_port)
        form.addRow("Debug port:", self.chrome_port_spin)

        self.chrome_profile_edit = QLineEdit(self.settings.chrome.profile_dir)
        form.addRow("Automation profile directory:", self.chrome_profile_edit)

        setup_btn = QPushButton("Set up browser profile (sign in)")
        setup_btn.clicked.connect(self._setup_browser_profile)
        form.addRow("", setup_btn)

        note = QLabel(
            "This is a SEPARATE Chrome profile from your everyday browser — since Chrome 136,\n"
            "the debugging port Spark needs is silently ignored on the default profile\n"
            "(BUILD_SPEC §2.1). Sign in to your site once here; the session is then remembered."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return widget

    def _setup_browser_profile(self) -> None:
        """Launches Chrome on the automation profile (with no headless/
        sandbox flags — a real, visible window) and leaves it running for
        the user to sign in by hand (BUILD_SPEC §2.1/§6.11). Spark never
        reads, stores, or transmits what's typed into that window.
        """
        from spark.browser.launcher import ChromeLauncher
        from spark.config import ChromeSettings

        chrome_settings = ChromeSettings(
            executable_path=self.chrome_path_edit.text().strip() or None,
            debug_port=self.chrome_port_spin.value(),
            profile_dir=self.chrome_profile_edit.text().strip(),
            close_on_finish=False,
        )
        try:
            launcher = ChromeLauncher(chrome_settings)
            asyncio.run(launcher.ensure_running())
        except Exception as exc:
            log.exception("Failed to launch the browser profile")
            QMessageBox.warning(self, "Could not launch Chrome", str(exc))
            return
        QMessageBox.information(
            self,
            "Browser opened",
            "Sign in to your site in the window that just opened, then close this dialog. "
            "Spark never sees your password.",
        )

    # -- Budgets & safety --------------------------------------------------

    def _build_budgets_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.max_iterations_spin = QSpinBox()
        self.max_iterations_spin.setRange(1, 10_000)
        self.max_iterations_spin.setValue(self.settings.budgets.max_iterations)
        form.addRow("Max iterations:", self.max_iterations_spin)

        self.max_runtime_spin = QSpinBox()
        self.max_runtime_spin.setRange(1, 24 * 60)
        self.max_runtime_spin.setValue(self.settings.budgets.max_runtime_minutes)
        form.addRow("Max runtime (minutes):", self.max_runtime_spin)

        self.max_model_calls_spin = QSpinBox()
        self.max_model_calls_spin.setRange(1, 1_000_000)
        self.max_model_calls_spin.setValue(self.settings.budgets.max_model_calls)
        form.addRow("Max model calls:", self.max_model_calls_spin)

        self.max_cost_spin = QDoubleSpinBox()
        self.max_cost_spin.setRange(0.0, 10_000.0)
        self.max_cost_spin.setDecimals(2)
        self.max_cost_spin.setValue(self.settings.budgets.max_cost_usd)
        form.addRow("Max cost (USD):", self.max_cost_spin)

        note = QLabel(
            "A fully autonomous run still needs limits (BUILD_SPEC §12.1) — these are ceilings,\n"
            "not approval prompts. Cost tracking only works once per-provider $/1k-token rates\n"
            "are entered for the active provider; until then it's reported as unknown, not $0."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return widget

    # -- collecting the result ---------------------------------------------

    def result_settings(self) -> AppSettings:
        s = self.settings
        s.active_provider = self.provider_combo.currentText()
        ps = s.provider_settings(s.active_provider)
        ps.model = self.model_edit.text().strip()
        s.providers[s.active_provider] = ps

        s.ocr.read_engine = self.read_engine_combo.currentText()
        escalation = self.escalation_engine_combo.currentText()
        s.ocr.escalation_engine = None if escalation == "(none)" else escalation
        s.ocr.escalation_confidence_threshold = self.confidence_spin.value()

        s.chrome.executable_path = self.chrome_path_edit.text().strip() or None
        s.chrome.debug_port = self.chrome_port_spin.value()
        s.chrome.profile_dir = self.chrome_profile_edit.text().strip()

        s.budgets.max_iterations = self.max_iterations_spin.value()
        s.budgets.max_runtime_minutes = self.max_runtime_spin.value()
        s.budgets.max_model_calls = self.max_model_calls_spin.value()
        s.budgets.max_cost_usd = self.max_cost_spin.value()
        return s
