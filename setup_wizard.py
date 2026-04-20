# -*- coding: utf-8 -*-
"""
setup_wizard.py  —  Survey Management System v1.2
First-run setup wizard shown when no database has been configured yet.

Guides the user through:
  1. PostgreSQL check (is it installed?)
  2. psycopg2 auto-install
  3. Database connection setup
  4. Auto-setup (create tables)
  5. Default login info

Shown automatically on first launch only.
"""

import os
import sys

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QStackedWidget, QWidget,
    QLabel, QPushButton, QLineEdit, QSpinBox, QFormLayout,
    QGroupBox, QTextEdit, QProgressBar, QApplication, QMessageBox
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsSettings


GREEN  = "#1A5C38"
LGREEN = "#E8F5EE"


class SetupWizard(QDialog):
    """
    Step-by-step first-run wizard.
    Returns QDialog.Accepted when setup is complete.
    """

    TOTAL_STEPS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Survey Management System — First-Time Setup")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._psycopg2_ok = False
        self._db_ok       = False
        self._setup_ui()
        self._show_step(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header bar
        header = QWidget()
        header.setStyleSheet(f"background:{GREEN};")
        hlay = QVBoxLayout(header)
        hlay.setContentsMargins(24, 20, 24, 20)
        self.step_title = QLabel("Welcome")
        self.step_title.setStyleSheet("color:white; font-size:16pt; font-weight:bold;")
        self.step_sub = QLabel("")
        self.step_sub.setStyleSheet("color:#AAFFCC; font-size:10pt;")
        self.step_sub.setWordWrap(True)
        hlay.addWidget(self.step_title)
        hlay.addWidget(self.step_sub)
        layout.addWidget(header)

        # Progress dots
        dots_row = QHBoxLayout()
        dots_row.setContentsMargins(24, 10, 24, 4)
        self.dots = []
        for i in range(self.TOTAL_STEPS):
            dot = QLabel("●")
            dot.setStyleSheet("color:#CCCCCC; font-size:14pt;")
            dots_row.addWidget(dot)
            self.dots.append(dot)
        dots_row.addStretch()
        dots_widget = QWidget()
        dots_widget.setLayout(dots_row)
        layout.addWidget(dots_widget)

        # Page stack
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(24, 8, 24, 8)
        layout.addWidget(self.stack, 1)

        # Build pages
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_psycopg2())
        self.stack.addWidget(self._page_database())
        self.stack.addWidget(self._page_done())

        # Bottom buttons
        btn_bar = QWidget()
        btn_bar.setStyleSheet("background:#F5F5F5; border-top:1px solid #DDD;")
        blay = QHBoxLayout(btn_bar)
        blay.setContentsMargins(24, 12, 24, 12)

        self.back_btn = QPushButton("← Back")
        self.back_btn.clicked.connect(self._prev_step)
        self.back_btn.setVisible(False)
        blay.addWidget(self.back_btn)
        blay.addStretch()

        self.skip_btn = QPushButton("Skip Setup")
        self.skip_btn.setStyleSheet("color:#888;")
        self.skip_btn.clicked.connect(self.reject)
        blay.addWidget(self.skip_btn)

        self.next_btn = QPushButton("Next →")
        self.next_btn.setStyleSheet(
            f"background:{GREEN}; color:white; font-weight:bold; padding:8px 20px;"
        )
        self.next_btn.clicked.connect(self._next_step)
        blay.addWidget(self.next_btn)

        layout.addWidget(btn_bar)
        self.setLayout(layout)

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_welcome(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 16, 0, 0)

        intro = QLabel(
            "Welcome to the <b>Survey Management System</b> for Nigerian cadastral surveying.\n\n"
            "This wizard will set up everything you need in a few minutes:\n"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("font-size:11pt;")
        lay.addWidget(intro)

        steps_box = QGroupBox()
        steps_box.setStyleSheet(f"QGroupBox {{ background:{LGREEN}; border:none; border-radius:4px; }}")
        slay = QVBoxLayout(steps_box)
        for num, text in [
            ("1", "Install psycopg2 (Python-PostgreSQL connector)"),
            ("2", "Connect to your PostgreSQL database"),
            ("3", "Create all required tables automatically"),
            ("4", "Log in and start using the system"),
        ]:
            row = QHBoxLayout()
            num_lbl = QLabel(num)
            num_lbl.setFixedWidth(28)
            num_lbl.setAlignment(Qt.AlignCenter)
            num_lbl.setStyleSheet(
                f"background:{GREEN}; color:white; font-weight:bold; "
                "border-radius:12px; padding:2px;"
            )
            row.addWidget(num_lbl)
            row.addWidget(QLabel(text))
            row.addStretch()
            slay.addLayout(row)
        lay.addWidget(steps_box)

        prereq = QLabel(
            "<b>Prerequisite:</b> PostgreSQL must be installed on your computer or network. "
            "If you have not installed it yet, download the free installer from "
            "<a href='https://postgresql.org'>postgresql.org</a> before continuing. "
            "During installation, use Stack Builder to also install the PostGIS extension."
        )
        prereq.setWordWrap(True)
        prereq.setOpenExternalLinks(True)
        prereq.setStyleSheet(
            "background:#FFF8E1; padding:10px; border-left:3px solid #F9A825; font-size:10pt;"
        )
        lay.addWidget(prereq)
        lay.addStretch()
        return w

    def _page_psycopg2(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 16, 0, 0)

        info = QLabel(
            "<b>psycopg2</b> is the Python library that lets QGIS talk to PostgreSQL. "
            "Click <b>Install Now</b> to install it automatically — "
            "no command line needed, no admin rights required."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size:10pt;")
        lay.addWidget(info)

        # Status indicator
        self.psycopg2_status = QLabel("⚪  Checking...")
        self.psycopg2_status.setStyleSheet("font-size:11pt; font-weight:bold; margin:8px 0;")
        lay.addWidget(self.psycopg2_status)

        self.psycopg2_progress = QProgressBar()
        self.psycopg2_progress.setRange(0, 0)
        self.psycopg2_progress.setVisible(False)
        lay.addWidget(self.psycopg2_progress)

        self.install_psycopg2_btn = QPushButton("⬇  Install psycopg2 Automatically")
        self.install_psycopg2_btn.setStyleSheet(
            f"background:{GREEN}; color:white; font-weight:bold; padding:10px; font-size:11pt;"
        )
        self.install_psycopg2_btn.clicked.connect(self._do_install_psycopg2)
        lay.addWidget(self.install_psycopg2_btn)

        manual = QLabel(
            "<b>Manual alternative:</b> Open the OSGeo4W Shell and run:<br>"
            "<code style='background:#f0f0f0; padding:2px 8px;'>"
            "python -m pip install psycopg2-binary</code>"
        )
        manual.setWordWrap(True)
        manual.setOpenExternalLinks(True)
        manual.setStyleSheet(
            "background:#F5F5F5; padding:10px; border-left:3px solid #888; font-size:10pt; margin-top:8px;"
        )
        lay.addWidget(manual)
        lay.addStretch()

        # Check on page load
        self._check_psycopg2_status()
        return w

    def _page_database(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 16, 0, 0)

        info = QLabel(
            "Enter your PostgreSQL connection details. "
            "The plugin will create the <b>survey_management</b> database and all tables automatically."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size:10pt;")
        lay.addWidget(info)

        form = QFormLayout()
        form.setSpacing(8)

        self.wiz_host = QLineEdit("localhost")
        self.wiz_port = QSpinBox()
        self.wiz_port.setRange(1, 65535)
        self.wiz_port.setValue(5432)
        self.wiz_user = QLineEdit("postgres")
        self.wiz_pass = QLineEdit()
        self.wiz_pass.setEchoMode(QLineEdit.Password)
        self.wiz_pass.setPlaceholderText("Your PostgreSQL password")

        form.addRow("Host:", self.wiz_host)
        form.addRow("Port:", self.wiz_port)
        form.addRow("Username:", self.wiz_user)
        form.addRow("Password:", self.wiz_pass)
        lay.addLayout(form)

        self.db_setup_btn = QPushButton("🚀  Connect and Auto-Setup Database")
        self.db_setup_btn.setStyleSheet(
            f"background:{GREEN}; color:white; font-weight:bold; padding:10px; font-size:11pt; margin-top:8px;"
        )
        self.db_setup_btn.clicked.connect(self._do_database_setup)
        lay.addWidget(self.db_setup_btn)

        self.db_progress = QProgressBar()
        self.db_progress.setRange(0, 0)
        self.db_progress.setVisible(False)
        lay.addWidget(self.db_progress)

        self.db_log = QTextEdit()
        self.db_log.setReadOnly(True)
        self.db_log.setMaximumHeight(120)
        self.db_log.setStyleSheet("font-family:monospace; font-size:9pt; background:#1E1E1E; color:#00FF88;")
        self.db_log.setVisible(False)
        lay.addWidget(self.db_log)
        lay.addStretch()
        return w

    def _page_done(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 20, 0, 0)

        tick = QLabel("✅")
        tick.setStyleSheet("font-size:48pt;")
        tick.setAlignment(Qt.AlignCenter)
        lay.addWidget(tick)

        done_lbl = QLabel("Setup Complete!")
        done_lbl.setStyleSheet(f"font-size:18pt; font-weight:bold; color:{GREEN};")
        done_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(done_lbl)

        creds = QGroupBox("Your login credentials")
        creds.setStyleSheet(f"QGroupBox {{ background:{LGREEN}; border:none; border-radius:4px; }}")
        clay = QFormLayout(creds)
        clay.addRow("Username:", QLabel("<b>admin</b>"))
        clay.addRow("Password:", QLabel("<b>admin123</b>"))
        clay.addRow("", QLabel("<i>Change this immediately in User Administration → Change Password</i>"))
        lay.addWidget(creds)

        tips = QLabel(
            "• <b>DXF Import:</b> Click 📐 Import from DXF/DWG on the Coordinate or Traverse tab<br>"
            "• <b>AI extraction</b> runs automatically — no API key needed<br>"
            "• <b>Create users</b> for each staff member in 👥 User Administration<br>"
            "• <b>CRS:</b> Select EPSG:26333 for Imo/eastern states"
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("font-size:10pt; padding:8px;")
        lay.addWidget(tips)
        lay.addStretch()
        return w

    # ── Step navigation ───────────────────────────────────────────────────────

    def _show_step(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, dot in enumerate(self.dots):
            dot.setStyleSheet(f"color:{GREEN}; font-size:14pt;" if i == idx
                              else ("color:#1A5C38; font-size:10pt;" if i < idx
                                    else "color:#CCCCCC; font-size:14pt;"))

        titles = [
            ("Welcome", "Survey Management System Setup Wizard"),
            ("Step 1 of 3", "Install psycopg2 — PostgreSQL connector"),
            ("Step 2 of 3", "Connect to PostgreSQL and create database"),
            ("All done!", "You are ready to start using the plugin"),
        ]
        self.step_title.setText(titles[idx][0])
        self.step_sub.setText(titles[idx][1])

        self.back_btn.setVisible(idx > 0)
        self.skip_btn.setVisible(idx < self.TOTAL_STEPS - 1)

        if idx == self.TOTAL_STEPS - 1:
            self.next_btn.setText("Start Using Plugin ✓")
            self.next_btn.setStyleSheet(
                f"background:{GREEN}; color:white; font-weight:bold; padding:8px 20px;"
            )
        else:
            self.next_btn.setText("Next →")

        # Auto-check psycopg2 when arriving at that page
        if idx == 1:
            self._check_psycopg2_status()

    def _next_step(self):
        idx = self.stack.currentIndex()
        if idx == self.TOTAL_STEPS - 1:
            self.accept()
            return
        # Gate: must have psycopg2 before proceeding past step 1
        if idx == 1 and not self._psycopg2_ok:
            QMessageBox.warning(self, "psycopg2 Required",
                "Please install psycopg2 before continuing.\n\n"
                "Click 'Install psycopg2 Automatically' above.")
            return
        # Gate: must have database before done
        if idx == 2 and not self._db_ok:
            QMessageBox.warning(self, "Database Setup Required",
                "Please complete the database setup before continuing.")
            return
        self._show_step(idx + 1)

    def _prev_step(self):
        idx = self.stack.currentIndex()
        if idx > 0:
            self._show_step(idx - 1)

    # ── psycopg2 install ──────────────────────────────────────────────────────

    def _check_psycopg2_status(self):
        try:
            import psycopg2  # noqa
            self.psycopg2_status.setText(f"✅  psycopg2 is installed and ready")
            self.psycopg2_status.setStyleSheet(f"color:{GREEN}; font-size:11pt; font-weight:bold;")
            self.install_psycopg2_btn.setEnabled(False)
            self.install_psycopg2_btn.setText("✅  Already installed")
            self._psycopg2_ok = True
        except ImportError:
            self.psycopg2_status.setText("⚠  psycopg2 is not installed")
            self.psycopg2_status.setStyleSheet("color:#E65100; font-size:11pt; font-weight:bold;")
            self._psycopg2_ok = False

    def _do_install_psycopg2(self):
        self.install_psycopg2_btn.setEnabled(False)
        self.install_psycopg2_btn.setText("Installing...")
        self.psycopg2_progress.setVisible(True)
        self.psycopg2_status.setText("⏳  Installing psycopg2-binary...")
        QApplication.processEvents()

        ok, msg = _install_package("psycopg2-binary")

        self.psycopg2_progress.setVisible(False)

        if ok:
            self.psycopg2_status.setText("✅  psycopg2 installed successfully!")
            self.psycopg2_status.setStyleSheet(f"color:{GREEN}; font-size:11pt; font-weight:bold;")
            self.install_psycopg2_btn.setText("✅  Installed")
            self._psycopg2_ok = True
        else:
            self.psycopg2_status.setText(f"❌  Installation failed")
            self.psycopg2_status.setStyleSheet("color:#C62828; font-size:11pt; font-weight:bold;")
            self.install_psycopg2_btn.setText("⬇  Retry Install")
            self.install_psycopg2_btn.setEnabled(True)
            QMessageBox.warning(self, "Install Failed",
                f"Could not install psycopg2 automatically.\n\n{msg}\n\n"
                "Use the OSGeo4W Shell method shown below.")

    # ── Database setup ────────────────────────────────────────────────────────

    def _do_database_setup(self):
        self.db_setup_btn.setEnabled(False)
        self.db_setup_btn.setText("Setting up...")
        self.db_progress.setVisible(True)
        self.db_log.setVisible(True)
        self.db_log.clear()
        QApplication.processEvents()

        host     = self.wiz_host.text().strip() or "localhost"
        port     = self.wiz_port.value()
        user     = self.wiz_user.text().strip() or "postgres"
        password = self.wiz_pass.text()

        def log(msg):
            self.db_log.append(msg)
            QApplication.processEvents()

        try:
            import psycopg2
            from psycopg2 import sql as pgsql

            log("Connecting to PostgreSQL...")
            try:
                conn = psycopg2.connect(
                    host=host, port=port, user=user,
                    password=password, dbname="postgres"
                )
            except psycopg2.OperationalError as e:
                raise Exception(f"Cannot connect to PostgreSQL:\n{e}\n\n"
                                "Check host, port, username and password.")

            conn.autocommit = True
            cur = conn.cursor()

            log("✅ Connected to PostgreSQL")

            # Create database
            cur.execute("SELECT 1 FROM pg_database WHERE datname='survey_management'")
            if not cur.fetchone():
                cur.execute(pgsql.SQL("CREATE DATABASE {}").format(
                    pgsql.Identifier("survey_management")))
                log("✅ Created database: survey_management")
            else:
                log("ℹ  Database already exists")

            cur.close()
            conn.close()

            # Connect to the new database
            conn2 = psycopg2.connect(
                host=host, port=port, user=user,
                password=password, dbname="survey_management"
            )
            conn2.autocommit = True
            cur2 = conn2.cursor()

            # Enable PostGIS
            try:
                cur2.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                log("✅ PostGIS extension enabled")
            except Exception as e:
                log(f"⚠  PostGIS: {e} (install PostGIS via Stack Builder if missing)")

            # Create all tables
            tables = _get_table_sql()
            for table_name, table_sql in tables:
                try:
                    cur2.execute(table_sql)
                    log(f"✅ Table: {table_name}")
                except Exception as e:
                    log(f"⚠  {table_name}: {e}")

            cur2.close()
            conn2.close()

            # Save connection settings to QGIS settings
            s = QgsSettings()
            s.setValue("survey_management/host",     host)
            s.setValue("survey_management/port",     str(port))
            s.setValue("survey_management/username", user)
            s.setValue("survey_management/password", password)
            s.setValue("survey_management/database", "survey_management")
            s.setValue("survey_management/setup_complete", "true")

            log("")
            log("✅✅✅  SETUP COMPLETE  ✅✅✅")
            self._db_ok = True
            self.db_setup_btn.setText("✅  Setup Complete")
            self.db_progress.setVisible(False)
            # Auto-advance to done page after a moment
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(1200, lambda: self._show_step(3))

        except Exception as e:
            self.db_progress.setVisible(False)
            log(f"\n❌ ERROR: {e}")
            self.db_setup_btn.setText("🚀  Retry Setup")
            self.db_setup_btn.setEnabled(True)


# ── Package install helper (shared with dependency_manager) ──────────────────

def _install_package(package_name):
    """
    Install a pip package into the plugin's lib/ folder.
    Returns (True, "") on success or (False, error_message) on failure.
    """
    lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
    os.makedirs(lib, exist_ok=True)

    args = ["install", "--target", lib, "--no-user",
            "--no-warn-script-location", "--disable-pip-version-check",
            "--quiet", package_name]

    try:
        from pip._internal.cli.main import main as pip_main
        ret = pip_main(args)
        if ret == 0:
            if lib not in sys.path:
                sys.path.append(lib) if lib not in sys.path else None
            return True, ""
        return False, f"pip exit code {ret}"
    except SystemExit as e:
        code = getattr(e, "code", str(e))
        if str(code) == "0" or code == 0:
            if lib not in sys.path:
                sys.path.append(lib) if lib not in sys.path else None
            return True, ""
        return False, f"pip SystemExit {code}"
    except Exception as e:
        try:
            import runpy
            saved = sys.argv[:]
            sys.argv = ["pip"] + args
            try:
                runpy.run_module("pip", run_name="__main__", alter_sys=True)
                if lib not in sys.path:
                    sys.path.append(lib) if lib not in sys.path else None
                return True, ""
            except SystemExit as e2:
                code = getattr(e2, "code", str(e2))
                if str(code) == "0" or code == 0:
                    if lib not in sys.path:
                        sys.path.append(lib) if lib not in sys.path else None
                    return True, ""
                return False, f"runpy exit {code}"
            finally:
                sys.argv = saved
        except Exception as e3:
            return False, str(e3)


# ── Table SQL ─────────────────────────────────────────────────────────────────

def _get_table_sql():
    """Return list of (name, CREATE SQL) for all required tables."""
    return [
        ("surveys", """
            CREATE TABLE IF NOT EXISTS surveys (
                id SERIAL PRIMARY KEY,
                plan_number VARCHAR(100) UNIQUE NOT NULL,
                owner_name VARCHAR(200),
                survey_date DATE,
                surveyor VARCHAR(200),
                lga VARCHAR(100),
                state VARCHAR(100),
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )"""),
        ("survey_points", """
            CREATE TABLE IF NOT EXISTS survey_points (
                id SERIAL PRIMARY KEY,
                survey_id INTEGER REFERENCES surveys(id) ON DELETE CASCADE,
                point_number INTEGER,
                easting DOUBLE PRECISION,
                northing DOUBLE PRECISION,
                description TEXT,
                geom GEOMETRY(Point, 4326),
                created_at TIMESTAMP DEFAULT NOW()
            )"""),
        ("survey_boundaries", """
            CREATE TABLE IF NOT EXISTS survey_boundaries (
                id SERIAL PRIMARY KEY,
                survey_id INTEGER REFERENCES surveys(id) ON DELETE CASCADE,
                area_sqm DOUBLE PRECISION,
                geom GEOMETRY(Polygon, 4326),
                created_at TIMESTAMP DEFAULT NOW()
            )"""),
        ("survey_traverses", """
            CREATE TABLE IF NOT EXISTS survey_traverses (
                id SERIAL PRIMARY KEY,
                survey_id INTEGER REFERENCES surveys(id) ON DELETE CASCADE,
                start_easting DOUBLE PRECISION,
                start_northing DOUBLE PRECISION,
                total_length DOUBLE PRECISION,
                closure_error DOUBLE PRECISION,
                precision_ratio INTEGER,
                created_at TIMESTAMP DEFAULT NOW()
            )"""),
        ("traverse_legs", """
            CREATE TABLE IF NOT EXISTS traverse_legs (
                id SERIAL PRIMARY KEY,
                traverse_id INTEGER REFERENCES survey_traverses(id) ON DELETE CASCADE,
                leg_number INTEGER,
                bearing_dms VARCHAR(30),
                bearing_decimal DOUBLE PRECISION,
                distance_m DOUBLE PRECISION,
                easting DOUBLE PRECISION,
                northing DOUBLE PRECISION,
                geom GEOMETRY(LineString, 4326),
                created_at TIMESTAMP DEFAULT NOW()
            )"""),
        ("survey_documents", """
            CREATE TABLE IF NOT EXISTS survey_documents (
                id SERIAL PRIMARY KEY,
                survey_id INTEGER REFERENCES surveys(id) ON DELETE CASCADE,
                filename VARCHAR(500),
                filepath TEXT,
                description TEXT,
                file_size BIGINT,
                md5_checksum VARCHAR(32),
                is_primary BOOLEAN DEFAULT FALSE,
                uploaded_at TIMESTAMP DEFAULT NOW(),
                uploaded_by VARCHAR(100)
            )"""),
        ("app_users", """
            CREATE TABLE IF NOT EXISTS app_users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                full_name VARCHAR(200),
                email VARCHAR(200),
                password_hash VARCHAR(200) NOT NULL,
                salt VARCHAR(100) NOT NULL,
                role VARCHAR(20) DEFAULT 'viewer',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                last_login TIMESTAMP
            )"""),
        ("audit_log", """
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                username VARCHAR(100),
                action VARCHAR(100),
                table_name VARCHAR(100),
                record_id INTEGER,
                old_values TEXT,
                new_values TEXT,
                ip_address VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW()
            )"""),
    ]
