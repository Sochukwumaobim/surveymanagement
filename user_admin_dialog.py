# -*- coding: utf-8 -*-
"""
user_admin_dialog.py — Survey Management System
Superuser panel: create/edit/disable users, view audit log, change own password.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QLineEdit, QPushButton, QLabel, QComboBox, QCheckBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QGroupBox, QMessageBox, QDateEdit,
    QApplication
)
from qgis.PyQt.QtCore import Qt, QDate
from qgis.PyQt.QtGui import QColor, QFont

from .login_dialog import hash_password, verify_password

try:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    psycopg2 = None


class UserAdminDialog(QDialog):
    """Full user management and audit log viewer.  Only for superusers."""

    def __init__(self, parent=None, db_connection=None, session_user=None):
        super().__init__(parent)
        self.db_connection = db_connection
        self.session_user = session_user or {}

        self.setWindowTitle("User Administration — Survey Management System")
        self.setMinimumWidth(860)
        self.setMinimumHeight(620)
        self.setWindowFlags(
            Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint |
            Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )

        self._setup_ui()
        self._load_users()
        self._load_audit_log()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout()

        # Header
        hdr = QLabel(
            f"👤  User Administration   |   Logged in as: "
            f"{self.session_user.get('username','?')} "
            f"({self.session_user.get('role','?')})"
        )
        hdr.setStyleSheet("font-size:12pt; font-weight:bold; color:#2c3e50; padding:6px 0;")
        layout.addWidget(hdr)

        tabs = QTabWidget()
        tabs.addTab(self._build_users_tab(), "👥  Manage Users")
        tabs.addTab(self._build_create_tab(), "➕  Create User")
        tabs.addTab(self._build_change_pw_tab(), "🔑  Change Password")
        tabs.addTab(self._build_audit_tab(), "📋  Audit Log")
        layout.addWidget(tabs)

        close_btn = QPushButton("✖  Close")
        close_btn.setStyleSheet(
            "background-color:#e74c3c; color:white; font-weight:bold; padding:7px;"
        )
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)

        self.setLayout(layout)

    # ---------- Users tab ----------

    def _build_users_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        toolbar = QHBoxLayout()
        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.setStyleSheet("background-color:#3498db; color:white; font-weight:bold; padding:5px;")
        refresh_btn.clicked.connect(self._load_users)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.users_table = QTableWidget()
        self.users_table.setColumnCount(8)
        self.users_table.setHorizontalHeaderLabels([
            "ID", "Username", "Full Name", "Role", "Email", "Active", "Last Login", "Actions"
        ])
        self.users_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.users_table.setAlternatingRowColors(True)
        self.users_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.users_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.users_table)

        tab.setLayout(layout)
        return tab

    # ---------- Create user tab ----------

    def _build_create_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)

        group = QGroupBox("New User Details")
        group.setStyleSheet("QGroupBox { font-weight:bold; }")
        form = QFormLayout()
        form.setSpacing(10)

        self.new_username = QLineEdit()
        self.new_username.setPlaceholderText("Lowercase, no spaces")
        form.addRow("Username *:", self.new_username)

        self.new_full_name = QLineEdit()
        self.new_full_name.setPlaceholderText("Full display name")
        form.addRow("Full Name:", self.new_full_name)

        self.new_email = QLineEdit()
        self.new_email.setPlaceholderText("user@example.com")
        form.addRow("Email:", self.new_email)

        self.new_role = QComboBox()
        self.new_role.addItems(["viewer", "surveyor", "superuser"])
        self.new_role.setCurrentIndex(1)
        form.addRow("Role *:", self.new_role)

        self.new_pw = QLineEdit()
        self.new_pw.setEchoMode(QLineEdit.Password)
        self.new_pw.setPlaceholderText("Minimum 6 characters")
        form.addRow("Password *:", self.new_pw)

        self.new_pw2 = QLineEdit()
        self.new_pw2.setEchoMode(QLineEdit.Password)
        self.new_pw2.setPlaceholderText("Repeat password")
        form.addRow("Confirm PW *:", self.new_pw2)

        group.setLayout(form)
        layout.addWidget(group)

        self.create_status = QLabel("")
        self.create_status.setStyleSheet("font-weight:bold; min-height:18px;")
        layout.addWidget(self.create_status)

        create_btn = QPushButton("➕  Create User")
        create_btn.setStyleSheet(
            "background-color:#27ae60; color:white; font-weight:bold; padding:8px;"
        )
        create_btn.clicked.connect(self._create_user)
        layout.addWidget(create_btn)
        layout.addStretch()

        tab.setLayout(layout)
        return tab

    # ---------- Change password tab ----------

    def _build_change_pw_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)

        group = QGroupBox("Change Password")
        group.setStyleSheet("QGroupBox { font-weight:bold; }")
        form = QFormLayout()
        form.setSpacing(10)

        self.chpw_target = QComboBox()
        form.addRow("User to change:", self.chpw_target)

        self.chpw_new = QLineEdit()
        self.chpw_new.setEchoMode(QLineEdit.Password)
        self.chpw_new.setPlaceholderText("New password")
        form.addRow("New Password *:", self.chpw_new)

        self.chpw_confirm = QLineEdit()
        self.chpw_confirm.setEchoMode(QLineEdit.Password)
        self.chpw_confirm.setPlaceholderText("Confirm new password")
        form.addRow("Confirm *:", self.chpw_confirm)

        group.setLayout(form)
        layout.addWidget(group)

        self.chpw_status = QLabel("")
        self.chpw_status.setStyleSheet("font-weight:bold; min-height:18px;")
        layout.addWidget(self.chpw_status)

        chpw_btn = QPushButton("🔑  Update Password")
        chpw_btn.setStyleSheet(
            "background-color:#f39c12; color:white; font-weight:bold; padding:8px;"
        )
        chpw_btn.clicked.connect(self._change_password)
        layout.addWidget(chpw_btn)
        layout.addStretch()

        tab.setLayout(layout)
        return tab

    # ---------- Audit log tab ----------

    def _build_audit_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter by action:"))

        self.audit_filter = QComboBox()
        self.audit_filter.addItems([
            "All", "LOGIN_SUCCESS", "LOGIN_FAILED",
            "SURVEY_CREATE", "SURVEY_UPDATE", "SURVEY_DELETE",
            "DOC_UPLOAD", "DOC_VERIFY", "USER_CREATE",
            "USER_DISABLE", "PASSWORD_CHANGE"
        ])
        self.audit_filter.currentTextChanged.connect(self._load_audit_log)
        filter_row.addWidget(self.audit_filter)

        filter_row.addWidget(QLabel("Rows:"))
        self.audit_limit = QComboBox()
        self.audit_limit.addItems(["50", "200", "500", "All"])
        self.audit_limit.currentTextChanged.connect(self._load_audit_log)
        filter_row.addWidget(self.audit_limit)

        refresh_btn = QPushButton("🔄  Refresh")
        refresh_btn.setStyleSheet("background-color:#3498db; color:white; font-weight:bold; padding:5px;")
        refresh_btn.clicked.connect(self._load_audit_log)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(7)
        self.audit_table.setHorizontalHeaderLabels([
            "Time", "User", "Action", "Table", "Record ID", "Details", "Notes"
        ])
        self.audit_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.audit_table.setAlternatingRowColors(True)
        self.audit_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.audit_table)

        self.audit_count_lbl = QLabel("Loading…")
        self.audit_count_lbl.setStyleSheet("color:#7f8c8d; font-style:italic;")
        layout.addWidget(self.audit_count_lbl)

        tab.setLayout(layout)
        return tab

    # ------------------------------------------------------------------
    # Data loaders
    # ------------------------------------------------------------------

    def _cursor(self):
        if not self.db_connection or self.db_connection.closed:
            return None
        self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        return self.db_connection.cursor()

    def _load_users(self):
        cur = self._cursor()
        if not cur:
            return
        try:
            cur.execute("""
                SELECT user_id, username, full_name, role, email,
                       is_active, last_login
                FROM app_users ORDER BY user_id
            """)
            rows = cur.fetchall()
            cur.close()

            self.users_table.setRowCount(len(rows))
            self.chpw_target.clear()

            for r, (uid, uname, fname, role, email, active, last_login) in enumerate(rows):
                self.users_table.setItem(r, 0, QTableWidgetItem(str(uid)))
                self.users_table.setItem(r, 1, QTableWidgetItem(uname or ""))
                self.users_table.setItem(r, 2, QTableWidgetItem(fname or ""))

                role_item = QTableWidgetItem(role or "")
                role_colors = {
                    "superuser": "#8e44ad",
                    "surveyor":  "#27ae60",
                    "viewer":    "#2980b9"
                }
                role_item.setForeground(QColor(role_colors.get(role, "#000")))
                f = QFont()
                f.setBold(True)
                role_item.setFont(f)
                self.users_table.setItem(r, 3, role_item)

                self.users_table.setItem(r, 4, QTableWidgetItem(email or ""))

                active_item = QTableWidgetItem("✅ Active" if active else "🚫 Disabled")
                active_item.setForeground(QColor("#27ae60" if active else "#e74c3c"))
                self.users_table.setItem(r, 5, active_item)

                login_str = last_login.strftime("%Y-%m-%d %H:%M") if last_login else "Never"
                self.users_table.setItem(r, 6, QTableWidgetItem(login_str))

                # Action buttons
                action_w = QWidget()
                action_l = QHBoxLayout()
                action_l.setContentsMargins(2, 2, 2, 2)

                if active:
                    dis_btn = QPushButton("🚫 Disable")
                    dis_btn.setStyleSheet("background-color:#e74c3c; color:white; font-size:9pt;")
                    dis_btn.clicked.connect(lambda _, i=uid, n=uname: self._toggle_user(i, n, False))
                    action_l.addWidget(dis_btn)
                else:
                    ena_btn = QPushButton("✅ Enable")
                    ena_btn.setStyleSheet("background-color:#27ae60; color:white; font-size:9pt;")
                    ena_btn.clicked.connect(lambda _, i=uid, n=uname: self._toggle_user(i, n, True))
                    action_l.addWidget(ena_btn)

                # Cannot delete yourself
                if uid != self.session_user.get("user_id"):
                    del_btn = QPushButton("🗑 Delete")
                    del_btn.setStyleSheet("background-color:#7f8c8d; color:white; font-size:9pt;")
                    del_btn.clicked.connect(lambda _, i=uid, n=uname: self._delete_user(i, n))
                    action_l.addWidget(del_btn)

                action_w.setLayout(action_l)
                self.users_table.setCellWidget(r, 7, action_w)

                self.chpw_target.addItem(f"{uname} ({role})", uid)

        except Exception as e:
            print(f"[UserAdmin] _load_users: {e}")

    def _load_audit_log(self):
        cur = self._cursor()
        if not cur:
            return
        try:
            action_filter = self.audit_filter.currentText()
            limit_text = self.audit_limit.currentText()
            limit_clause = "" if limit_text == "All" else f"LIMIT {limit_text}"

            if action_filter == "All":
                cur.execute(f"""
                    SELECT logged_at, username, action, table_name,
                           record_id, old_values, new_values
                    FROM audit_log
                    ORDER BY logged_at DESC {limit_clause}
                """)
            else:
                cur.execute(f"""
                    SELECT logged_at, username, action, table_name,
                           record_id, old_values, new_values
                    FROM audit_log
                    WHERE action = %s
                    ORDER BY logged_at DESC {limit_clause}
                """, (action_filter,))

            rows = cur.fetchall()
            cur.close()

            self.audit_table.setRowCount(len(rows))
            for r, (ts, uname, action, table, rec_id, old_v, new_v) in enumerate(rows):
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else ""
                self.audit_table.setItem(r, 0, QTableWidgetItem(ts_str))
                self.audit_table.setItem(r, 1, QTableWidgetItem(uname or ""))

                act_item = QTableWidgetItem(action or "")
                if "FAIL" in (action or ""):
                    act_item.setForeground(QColor("#e74c3c"))
                elif "SUCCESS" in (action or "") or "CREATE" in (action or ""):
                    act_item.setForeground(QColor("#27ae60"))
                self.audit_table.setItem(r, 2, act_item)

                self.audit_table.setItem(r, 3, QTableWidgetItem(table or ""))
                self.audit_table.setItem(r, 4, QTableWidgetItem(str(rec_id) if rec_id else ""))
                self.audit_table.setItem(r, 5, QTableWidgetItem((old_v or "")[:80]))
                self.audit_table.setItem(r, 6, QTableWidgetItem((new_v or "")[:80]))

            self.audit_count_lbl.setText(f"Showing {len(rows)} entries")

        except Exception as e:
            print(f"[UserAdmin] _load_audit_log: {e}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _create_user(self):
        username  = self.new_username.text().strip().lower()
        full_name = self.new_full_name.text().strip()
        email     = self.new_email.text().strip()
        role      = self.new_role.currentText()
        pw        = self.new_pw.text()
        pw2       = self.new_pw2.text()

        if not username:
            self._set_create_status("Username is required.", error=True)
            return
        if len(pw) < 6:
            self._set_create_status("Password must be at least 6 characters.", error=True)
            return
        if pw != pw2:
            self._set_create_status("Passwords do not match.", error=True)
            return

        cur = self._cursor()
        if not cur:
            self._set_create_status("No database connection.", error=True)
            return

        try:
            pw_hash, salt = hash_password(pw)
            cur.execute("""
                INSERT INTO app_users
                    (username, password_hash, password_salt, role,
                     full_name, email, is_active, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)
                RETURNING user_id
            """, (username, pw_hash, salt, role,
                  full_name or None, email or None,
                  self.session_user.get("user_id")))
            new_id = cur.fetchone()[0]

            self._write_audit(cur, "USER_CREATE",
                              new_values=f"username={username} role={role}")
            cur.close()

            self._set_create_status(f"✅ User '{username}' created (ID {new_id}).", error=False)

            # Clear form
            self.new_username.clear()
            self.new_full_name.clear()
            self.new_email.clear()
            self.new_pw.clear()
            self.new_pw2.clear()

            self._load_users()

        except Exception as e:
            if "duplicate" in str(e).lower() or "unique" in str(e).lower():
                self._set_create_status(f"Username '{username}' already exists.", error=True)
            else:
                self._set_create_status(f"Error: {str(e)[:70]}", error=True)
            print(f"[UserAdmin] _create_user: {e}")

    def _set_create_status(self, msg, error=True):
        colour = "#e74c3c" if error else "#27ae60"
        self.create_status.setStyleSheet(f"color:{colour}; font-weight:bold;")
        self.create_status.setText(msg)

    def _toggle_user(self, user_id, username, enable):
        verb = "enable" if enable else "disable"
        reply = QMessageBox.question(
            self, f"Confirm {verb.title()}",
            f"Are you sure you want to {verb} user '{username}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        cur = self._cursor()
        if not cur:
            return
        try:
            cur.execute(
                "UPDATE app_users SET is_active = %s WHERE user_id = %s",
                (enable, user_id)
            )
            action = "USER_ENABLE" if enable else "USER_DISABLE"
            self._write_audit(cur, action, record_id=user_id,
                              new_values=f"username={username}")
            cur.close()
            self._load_users()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_user(self, user_id, username):
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Permanently delete user '{username}'?\n\n"
            "Their audit log entries will be preserved.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        cur = self._cursor()
        if not cur:
            return
        try:
            self._write_audit(cur, "USER_DELETE", record_id=user_id,
                              old_values=f"username={username}")
            cur.execute("DELETE FROM app_users WHERE user_id = %s", (user_id,))
            cur.close()
            self._load_users()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _change_password(self):
        target_uid = self.chpw_target.currentData()
        target_name = self.chpw_target.currentText()
        new_pw  = self.chpw_new.text()
        confirm = self.chpw_confirm.text()

        if len(new_pw) < 6:
            self.chpw_status.setStyleSheet("color:#e74c3c; font-weight:bold;")
            self.chpw_status.setText("Password must be at least 6 characters.")
            return
        if new_pw != confirm:
            self.chpw_status.setStyleSheet("color:#e74c3c; font-weight:bold;")
            self.chpw_status.setText("Passwords do not match.")
            return

        cur = self._cursor()
        if not cur:
            return
        try:
            pw_hash, salt = hash_password(new_pw)
            cur.execute("""
                UPDATE app_users
                SET password_hash = %s, password_salt = %s
                WHERE user_id = %s
            """, (pw_hash, salt, target_uid))
            self._write_audit(cur, "PASSWORD_CHANGE", record_id=target_uid,
                              new_values=f"target={target_name}")
            cur.close()

            self.chpw_status.setStyleSheet("color:#27ae60; font-weight:bold;")
            self.chpw_status.setText(f"✅ Password updated for {target_name}.")
            self.chpw_new.clear()
            self.chpw_confirm.clear()

        except Exception as e:
            self.chpw_status.setStyleSheet("color:#e74c3c; font-weight:bold;")
            self.chpw_status.setText(f"Error: {str(e)[:70]}")

    def _write_audit(self, cur, action, record_id=None,
                     old_values=None, new_values=None):
        try:
            cur.execute("""
                INSERT INTO audit_log
                    (user_id, username, action, record_id, old_values, new_values)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                self.session_user.get("user_id"),
                self.session_user.get("username"),
                action, record_id, old_values, new_values
            ))
        except Exception as e:
            print(f"[UserAdmin] audit write: {e}")
