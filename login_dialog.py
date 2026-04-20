# -*- coding: utf-8 -*-
"""
login_dialog.py — Survey Management System
User authentication dialog.  Checks app_users table with SHA-256 + salt.
Default first-run credentials:  admin / admin123
"""

import hashlib
import secrets
import os

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QGroupBox, QCheckBox
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont

try:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    psycopg2 = None


# ---------------------------------------------------------------------------
# Password helpers (module-level so other files can import them)
# ---------------------------------------------------------------------------

def hash_password(password, salt=None):
    """Return (sha256_hex, salt).  Generates a random salt when none given."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return digest, salt


def verify_password(password, stored_hash, salt):
    """True if password matches the stored hash+salt pair."""
    computed, _ = hash_password(password, salt)
    return computed == stored_hash


# ---------------------------------------------------------------------------
# LoginDialog
# ---------------------------------------------------------------------------

class LoginDialog(QDialog):
    """Shown before the main plugin window.  Sets self.session_user on success."""

    def __init__(self, parent=None, db_connection=None):
        super().__init__(parent)
        self.db_connection = db_connection
        self.session_user = None

        self.setWindowTitle("Survey Management System — Login")
        self.setMinimumWidth(430)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)

        self._setup_ui()

        if db_connection and not db_connection.closed:
            self._ensure_tables()
            self._ensure_default_superuser()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        # Header
        title = QLabel("🏛️  Nigerian Survey Management System")
        title.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #2c3e50; padding: 10px 0 2px 0;")
        layout.addWidget(title)

        sub = QLabel("Please log in to continue")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color: #7f8c8d; font-size: 10pt; margin-bottom: 6px;")
        layout.addWidget(sub)

        # Credentials group
        group = QGroupBox("Credentials")
        group.setStyleSheet("QGroupBox { font-weight: bold; }")
        form = QFormLayout()
        form.setSpacing(10)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Username")
        self.username_edit.setMinimumHeight(32)
        form.addRow("Username:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Password")
        self.password_edit.setMinimumHeight(32)
        self.password_edit.returnPressed.connect(self._do_login)
        form.addRow("Password:", self.password_edit)

        show_cb = QCheckBox("Show password")
        show_cb.toggled.connect(
            lambda on: self.password_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password
            )
        )
        form.addRow("", show_cb)
        group.setLayout(form)
        layout.addWidget(group)

        # Status
        self.status_lbl = QLabel("")
        self.status_lbl.setAlignment(Qt.AlignCenter)
        self.status_lbl.setStyleSheet("color: #e74c3c; font-weight: bold; min-height: 20px;")
        layout.addWidget(self.status_lbl)

        # Buttons
        btn_row = QHBoxLayout()

        login_btn = QPushButton("🔑  Login")
        login_btn.setMinimumHeight(36)
        login_btn.setStyleSheet(
            "background-color:#27ae60; color:white; font-weight:bold;"
            " font-size:11pt; border-radius:4px;"
        )
        login_btn.clicked.connect(self._do_login)
        btn_row.addWidget(login_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumHeight(36)
        cancel_btn.setStyleSheet(
            "background-color:#e74c3c; color:white; font-weight:bold; border-radius:4px;"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        hint = QLabel("First-run default:  admin / admin123  — change after login")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color:#95a5a6; font-size:9pt; font-style:italic; margin-top:4px;")
        layout.addWidget(hint)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # DB setup
    # ------------------------------------------------------------------

    def _cursor(self):
        if not self.db_connection or self.db_connection.closed:
            return None
        self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return self.db_connection.cursor()

    def _ensure_tables(self):
        cur = self._cursor()
        if not cur:
            return
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_users (
                    user_id       SERIAL PRIMARY KEY,
                    username      VARCHAR(50) UNIQUE NOT NULL,
                    password_hash VARCHAR(64) NOT NULL,
                    password_salt VARCHAR(32) NOT NULL,
                    role          VARCHAR(20) NOT NULL DEFAULT 'viewer'
                                      CHECK (role IN ('superuser','surveyor','viewer')),
                    full_name     VARCHAR(200),
                    email         VARCHAR(200),
                    is_active     BOOLEAN DEFAULT TRUE,
                    created_by    INTEGER REFERENCES app_users(user_id) ON DELETE SET NULL,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login    TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    log_id     BIGSERIAL PRIMARY KEY,
                    user_id    INTEGER REFERENCES app_users(user_id) ON DELETE SET NULL,
                    username   VARCHAR(50),
                    action     VARCHAR(80) NOT NULL,
                    table_name VARCHAR(100),
                    record_id  INTEGER,
                    old_values TEXT,
                    new_values TEXT,
                    logged_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.close()
        except Exception as e:
            print(f"[Login] ensure_tables: {e}")

    def _ensure_default_superuser(self):
        cur = self._cursor()
        if not cur:
            return
        try:
            cur.execute("SELECT COUNT(*) FROM app_users")
            if cur.fetchone()[0] == 0:
                pw_hash, salt = hash_password("admin123")
                cur.execute("""
                    INSERT INTO app_users
                        (username, password_hash, password_salt, role, full_name, is_active)
                    VALUES (%s, %s, %s, 'superuser', 'System Administrator', TRUE)
                """, ("admin", pw_hash, salt))
            cur.close()
        except Exception as e:
            print(f"[Login] ensure_default_superuser: {e}")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def _do_login(self):
        username = self.username_edit.text().strip()
        password = self.password_edit.text()

        if not username or not password:
            self.status_lbl.setText("Please enter username and password.")
            return

        # Offline fallback (no DB)
        if not self.db_connection or self.db_connection.closed:
            self.status_lbl.setText("No database connection — cannot authenticate.")
            return

        cur = self._cursor()
        if not cur:
            self.status_lbl.setText("Database connection error.")
            return

        try:
            cur.execute("""
                SELECT user_id, username, password_hash, password_salt,
                       role, full_name, is_active
                FROM app_users WHERE username = %s
            """, (username,))
            row = cur.fetchone()

            if not row:
                self.status_lbl.setText("Invalid username or password.")
                self._audit(cur, None, username, "LOGIN_FAILED", notes="Unknown user")
                cur.close()
                return

            uid, uname, pw_hash, pw_salt, role, full_name, is_active = row

            if not is_active:
                self.status_lbl.setText("Account is disabled. Contact your administrator.")
                self._audit(cur, uid, uname, "LOGIN_FAILED", notes="Disabled account")
                cur.close()
                return

            if not verify_password(password, pw_hash, pw_salt):
                self.status_lbl.setText("Invalid username or password.")
                self._audit(cur, uid, uname, "LOGIN_FAILED", notes="Wrong password")
                cur.close()
                return

            # Success
            cur.execute("UPDATE app_users SET last_login = NOW() WHERE user_id = %s", (uid,))
            self._audit(cur, uid, uname, "LOGIN_SUCCESS")
            cur.close()

            self.session_user = {
                "user_id":   uid,
                "username":  uname,
                "full_name": full_name or uname,
                "role":      role,
            }
            self.accept()

        except Exception as e:
            self.status_lbl.setText(f"Error: {str(e)[:70]}")
            print(f"[Login] _do_login: {e}")

    def _audit(self, cur, user_id, username, action, notes=None):
        try:
            cur.execute("""
                INSERT INTO audit_log (user_id, username, action, old_values)
                VALUES (%s, %s, %s, %s)
            """, (user_id, username, action, notes))
        except Exception as e:
            print(f"[Login] audit write: {e}")
