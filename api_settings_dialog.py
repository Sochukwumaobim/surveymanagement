# -*- coding: utf-8 -*-
"""
api_settings_dialog.py  —  Survey Management System v1.2
Dialog for entering and storing the Anthropic API key used by the
AI-powered DXF metadata extractor.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QGroupBox, QCheckBox, QMessageBox
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsSettings


class APISettingsDialog(QDialog):
    """Shown when no API key is configured."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Extraction Settings — Survey Management System")
        self.setMinimumWidth(520)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._setup_ui()
        self._load_existing()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        hdr = QLabel("🤖  AI-Powered DXF Data Extraction")
        hdr.setStyleSheet("font-size:13pt; font-weight:bold; color:#1A5C38; padding:4px 0;")
        layout.addWidget(hdr)

        info = QLabel(
            "The plugin uses <b>Google Gemini AI</b> (completely free) to intelligently "
            "read survey plan text and extract:\n\n"
            "  • Plan number, owner name, surveyor, LGA, state, date\n"
            "  • All traverse legs (bearings and distances)\n"
            "  • Beacon numbers and descriptions\n\n"
            "Get a free API key at aistudio.google.com — no payment required."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size:10pt;")
        layout.addWidget(info)

        group = QGroupBox("Anthropic API Key")
        group.setStyleSheet("QGroupBox { font-weight:bold; }")
        form = QFormLayout()
        form.setSpacing(10)

        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("AIza...")
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setMinimumHeight(32)
        form.addRow("API Key:", self.key_edit)

        show_cb = QCheckBox("Show key")
        show_cb.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        form.addRow("", show_cb)

        self.save_cb = QCheckBox("Save key in QGIS settings")
        self.save_cb.setChecked(True)
        form.addRow("", self.save_cb)

        group.setLayout(form)
        layout.addWidget(group)

        howto = QLabel(
            "<b>How to get your free key (2 minutes):</b><br>"
            "1. Go to <a href='https://aistudio.google.com/app/apikey'>"
            "aistudio.google.com/app/apikey</a><br>"
            "2. Sign in with your Google account<br>"
            "3. Click <b>Create API Key</b><br>"
            "4. Copy the key (starts with AIza...) and paste it above<br><br>"
            "<b>Free limits:</b> 1,500 requests/day  |  "
            "15 requests/minute  |  No credit card needed"
        )
        howto.setOpenExternalLinks(True)
        howto.setWordWrap(True)
        howto.setStyleSheet(
            "background:#F5F5F5; padding:10px; "
            "border-left:3px solid #1A5C38; font-size:10pt;"
        )
        layout.addWidget(howto)

        skip_lbl = QLabel(
            "If you skip this step, the plugin will still import coordinates "
            "from DXF files — only the AI metadata extraction will be disabled. "
            "You can configure the API key later via the plugin menu."
        )
        skip_lbl.setWordWrap(True)
        skip_lbl.setStyleSheet("color:#888; font-size:9pt; font-style:italic;")
        layout.addWidget(skip_lbl)

        btn_row = QHBoxLayout()

        save_btn = QPushButton("💾  Save and Enable AI Extraction")
        save_btn.setStyleSheet(
            "background-color:#1A5C38; color:white; font-weight:bold; padding:8px 14px;"
        )
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        skip_btn = QPushButton("Skip — Use Without AI")
        skip_btn.setStyleSheet("padding:8px;")
        skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(skip_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def _load_existing(self):
        key = QgsSettings().value("survey_management/gemini_api_key", "")
        if key:
            self.key_edit.setText(key)

    def _save(self):
        key = self.key_edit.text().strip()
        if not key:
            QMessageBox.warning(self, "No Key", "Please enter an API key.")
            return
        if not key.startswith("AIza"):
            reply = QMessageBox.question(
                self, "Unusual Key Format",
                "Google Gemini API keys normally start with 'AIza'. "
                "Are you sure this is a Google AI Studio API key?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        if self.save_cb.isChecked():
            QgsSettings().setValue("survey_management/gemini_api_key", key)

        self.accept()

    def get_key(self):
        return self.key_edit.text().strip()
