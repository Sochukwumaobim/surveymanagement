# -*- coding: utf-8 -*-
"""
/***************************************************************************
 SurveyManagement — main plugin entry point
 Digital archiving for Nigerian survey records
 Copyright (C) 2026 ASTROMAT GEO-SERVICES
 Email: ugwusochukwuma@gmail.com
 ***************************************************************************/
"""

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QDialog
from qgis.core import Qgis
import os.path
import sys

try:
    import psycopg2
    from psycopg2 import sql
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None

from .connection_dialog import ConnectionDialog, DatabaseManager


class SurveyManagement:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)

        locale = QSettings().value('locale/userLocale')[0:2]
        locale_path = os.path.join(
            self.plugin_dir, 'i18n',
            'SurveyManagement_{}.qm'.format(locale)
        )
        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        self.actions        = []
        self.menu           = self.tr(u'&Survey Management System')
        self.db_connection  = None
        self.db_manager     = DatabaseManager()
        self.dialog         = None
        self.first_start    = None
        self.session_user   = None      # Set after successful login
        self.settings       = QSettings()

    def tr(self, message):
        return QCoreApplication.translate('SurveyManagement', message)

    def add_action(self, icon_path, text, callback, enabled_flag=True,
                   add_to_menu=True, add_to_toolbar=True,
                   status_tip=None, whats_this=None, parent=None):
        icon   = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if status_tip:
            action.setStatusTip(status_tip)
        if whats_this:
            action.setWhatsThis(whats_this)
        if add_to_toolbar:
            self.iface.addToolBarIcon(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        if not PSYCOPG2_AVAILABLE:
            self.iface.messageBar().pushMessage(
                "Warning",
                "psycopg2 not installed. Database features disabled. "
                "Run: pip install psycopg2-binary",
                level=Qgis.Warning, duration=10
            )

        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        if not os.path.exists(icon_path):
            icon_path = ':/images/themes/default/mAction.png'

        # Main plugin action
        self.add_action(
            icon_path,
            text=self.tr(u'Survey Management System'),
            callback=self.run,
            parent=self.iface.mainWindow()
        )

        # ── Parcellation Module ──────────────────────────────────────────
        parcellation_icon = os.path.join(self.plugin_dir, 'icon.png')
        self.parcellation_action = QAction(
            QIcon(parcellation_icon),
            '🗺  Parcellation Module',
            self.iface.mainWindow()
        )
        self.parcellation_action.setStatusTip(
            "Subdivide a land perimeter into plots with roads")
        self.parcellation_action.triggered.connect(self.open_parcellation)
        self.iface.addToolBarIcon(self.parcellation_action)
        self.iface.addPluginToMenu(self.menu, self.parcellation_action)
        self.actions.append(self.parcellation_action)

        # ── Database Settings ────────────────────────────────────────────
        self.connection_action = QAction(
            '⚙️ Database Settings', self.iface.mainWindow()
        )
        self.connection_action.triggered.connect(self.show_connection_dialog)
        self.iface.addPluginToMenu(self.menu, self.connection_action)
        self.actions.append(self.connection_action)

        self.test_action = QAction(
            '🔌 Test Database Connection', self.iface.mainWindow()
        )
        self.test_action.triggered.connect(self.test_connection)
        self.iface.addPluginToMenu(self.menu, self.test_action)
        self.actions.append(self.test_action)

        # Admin panel (visible to all; access is enforced inside)
        self.admin_action = QAction(
            '👥 User Administration', self.iface.mainWindow()
        )
        self.admin_action.triggered.connect(self.show_user_admin)
        self.iface.addPluginToMenu(self.menu, self.admin_action)
        self.actions.append(self.admin_action)

        self.help_action = QAction(
            '❓ Help & About', self.iface.mainWindow()
        )
        self.help_action.triggered.connect(self.show_help)
        self.iface.addPluginToMenu(self.menu, self.help_action)
        self.actions.append(self.help_action)

        self.first_start = True

    def unload(self):
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)

        if self.db_connection and not self.db_connection.closed:
            self.db_connection.close()

        if self.dialog and self.dialog.isVisible():
            self.dialog.close()

    # ------------------------------------------------------------------
    # Parcellation Module
    # ------------------------------------------------------------------

    def open_parcellation(self):
        """Open the parcellation module dialog."""
        from qgis.core import QgsCoordinateReferenceSystem

        # Inherit CRS from main dialog if available, else default to Nigeria East
        crs = QgsCoordinateReferenceSystem("EPSG:26333")
        if self.dialog and hasattr(self.dialog, 'get_active_crs'):
            try:
                crs = self.dialog.get_active_crs()
            except Exception:
                pass

        # Pre-load perimeter from current survey if one is loaded
        perimeter = None
        if self.dialog and hasattr(self.dialog, 'get_current_survey_perimeter'):
            try:
                perimeter = self.dialog.get_current_survey_perimeter()
            except Exception:
                pass

        try:
            from .parcellation_dialog import ParcellationDialog
            # Keep reference so dialog stays alive and non-modal
            if hasattr(self, '_parc_dlg') and self._parc_dlg is not None:
                try:
                    if self._parc_dlg.isVisible():
                        self._parc_dlg.raise_()
                        self._parc_dlg.activateWindow()
                        return
                except Exception:
                    pass

            dlg = ParcellationDialog(
                iface=self.iface,
                crs=crs,
                parent=None        # No parent = truly independent window
            )
            # Non-modal floating window — user can interact with QGIS while open
            dlg.setWindowFlags(
                Qt.Window |
                Qt.WindowTitleHint |
                Qt.WindowSystemMenuHint |
                Qt.WindowMinimizeButtonHint |
                Qt.WindowMaximizeButtonHint |
                Qt.WindowCloseButtonHint
            )
            dlg.setWindowModality(Qt.NonModal)
            self._parc_dlg = dlg
            dlg.setAttribute(Qt.WA_DeleteOnClose, True)
            dlg.destroyed.connect(lambda: setattr(self, '_parc_dlg', None))
            dlg.show()
        except ImportError as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "Parcellation Module Error",
                f"Could not load parcellation module:\n{str(e)}\n\n"
                "Please ensure parcellation_dialog.py and parcellation_engine.py "
                "are present in the plugin folder."
            )
        except Exception as e:
            import traceback
            QMessageBox.critical(
                self.iface.mainWindow(), "Parcellation Error",
                f"Error opening parcellation module:\n{str(e)}"
            )
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Database connection helpers
    # ------------------------------------------------------------------

    def show_connection_dialog(self):
        dialog = ConnectionDialog(self.iface.mainWindow())
        if dialog.exec_() == QDialog.Accepted:
            self.db_connection = self.db_manager.get_connection_from_settings()
            if self.db_connection:
                QMessageBox.information(
                    self.iface.mainWindow(), "Setting Up Database",
                    "Testing connection and creating tables…\n\nThis may take a moment."
                )
                if self.db_manager.ensure_database_exists():
                    if self.db_manager.create_tables():
                        QMessageBox.information(
                            self.iface.mainWindow(), "Success",
                            "✅ Database configured successfully!\n\n"
                            "• Database: survey_management\n"
                            "• PostGIS extension enabled\n"
                            "• All required tables created\n\n"
                            "You can now use the plugin with full functionality."
                        )
                    else:
                        QMessageBox.warning(
                            self.iface.mainWindow(), "Partial Success",
                            "⚠️ Connected but some tables could not be created.\n"
                            "Please check your database permissions."
                        )
                else:
                    QMessageBox.critical(
                        self.iface.mainWindow(), "Database Error",
                        "❌ Could not create or verify database.\n\n"
                        "Please check:\n"
                        "• PostgreSQL server is running\n"
                        "• You have CREATE DATABASE permissions\n"
                        "• PostGIS is available"
                    )
                return True
            else:
                QMessageBox.critical(
                    self.iface.mainWindow(), "Connection Failed",
                    "❌ Could not connect to database.\n\n"
                    "Please check your connection settings."
                )
                return False
        return False

    def connect_db(self):
        if not PSYCOPG2_AVAILABLE:
            self.iface.messageBar().pushMessage(
                "Error", "psycopg2 not installed. Please install psycopg2-binary",
                level=Qgis.Critical, duration=5
            )
            return False

        host = self.settings.value("survey_management/host", "")
        if not host:
            return self.show_connection_dialog()

        try:
            self.db_connection = self.db_manager.get_connection_from_settings()
            if self.db_connection:
                cur = self.db_connection.cursor()
                cur.execute("SELECT 1")
                cur.close()
                self.db_manager.create_tables()
                return True
            else:
                return self._handle_connection_failure()
        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Database Error", f"Could not connect: {str(e)}",
                level=Qgis.Critical, duration=5
            )
            return self._handle_connection_failure()

    def _handle_connection_failure(self):
        reply = QMessageBox.question(
            self.iface.mainWindow(), "Connection Failed",
            "❌ Could not connect to database.\n\n"
            "Would you like to reconfigure your connection settings?\n\n"
            "• YES — open connection settings\n"
            "• NO  — continue in OFFLINE mode (limited features)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            return self.show_connection_dialog()
        QMessageBox.information(
            self.iface.mainWindow(), "Offline Mode",
            "⚠️ Continuing in OFFLINE mode.\n\n"
            "Data cannot be saved to database.\n"
            "Use Database Settings to reconnect."
        )
        return True

    def test_connection(self):
        if not PSYCOPG2_AVAILABLE:
            QMessageBox.critical(
                self.iface.mainWindow(), "psycopg2 Missing",
                "The psycopg2 library is not installed.\n\n"
                "Install via OSGeo4W Shell:\n"
                "python -m pip install psycopg2-binary"
            )
            return

        host = self.settings.value("survey_management/host", "")
        if not host:
            reply = QMessageBox.question(
                self.iface.mainWindow(), "No Connection Settings",
                "No database connection settings found.\n\n"
                "Would you like to configure them now?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.show_connection_dialog()
            return

        try:
            self.db_connection = self.db_manager.get_connection_from_settings()
            if self.db_connection:
                cur = self.db_connection.cursor()
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
                try:
                    cur.execute("SELECT PostGIS_Version()")
                    postgis_version = cur.fetchone()[0]
                except Exception:
                    postgis_version = "Not available"

                tables = ['surveys', 'survey_points', 'survey_boundaries',
                          'survey_documents', 'app_users', 'audit_log']
                counts = []
                for table in tables:
                    try:
                        from psycopg2 import sql as pgsql
                        cur.execute(pgsql.SQL("SELECT COUNT(*) FROM {}").format(
                            pgsql.Identifier(table)))
                        count = cur.fetchone()[0]
                        counts.append(f"• {table}: {count:,} records")
                    except Exception:
                        counts.append(f"• {table}: (not found)")
                cur.close()

                QMessageBox.information(
                    self.iface.mainWindow(), "Connection Successful",
                    f"✅ Connected!\n\n"
                    f"PostgreSQL: {version[:80]}…\n"
                    f"PostGIS: {postgis_version}\n\n"
                    "Table Statistics:\n" + "\n".join(counts)
                )
            else:
                QMessageBox.critical(
                    self.iface.mainWindow(), "Connection Failed",
                    "❌ Could not connect to database.\n\n"
                    "Please check your settings in Database Settings menu."
                )
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "Connection Error",
                f"❌ Error testing connection:\n\n{str(e)}"
            )

    # ------------------------------------------------------------------
    # User administration
    # ------------------------------------------------------------------

    def show_user_admin(self):
        """Show the user administration dialog — superuser only."""
        if not self.session_user:
            QMessageBox.warning(
                self.iface.mainWindow(), "Not Logged In",
                "You must be logged in to access user administration.\n\n"
                "Please open the plugin first."
            )
            return

        if self.session_user.get("role") != "superuser":
            QMessageBox.warning(
                self.iface.mainWindow(), "Access Denied",
                "User Administration is only available to superusers.\n\n"
                f"Your role: {self.session_user.get('role', 'unknown')}"
            )
            return

        try:
            from .user_admin_dialog import UserAdminDialog
            dlg = UserAdminDialog(
                self.iface.mainWindow(),
                db_connection=self.db_connection,
                session_user=self.session_user
            )
            dlg.exec_()
        except ImportError as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "Import Error",
                f"Could not load user admin dialog:\n{str(e)}"
            )

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def show_api_settings(self):
        """AI extraction is now handled via hosted server — no API key needed."""
        QMessageBox.information(
            self.iface.mainWindow(),
            "AI Extraction — No Setup Required",
            "AI metadata extraction is handled automatically via a hosted server.\n\n"
            "No API key or account is needed.\n\n"
            "Simply import a DXF file and the AI extraction runs automatically."
        )

    def show_help(self):
        help_text = """
        <html><head><style>
          body { font-family: Arial, sans-serif; margin: 20px; }
          h1   { color: #2c3e50; font-size: 16pt; }
          h2   { color: #27ae60; font-size: 13pt; margin-top: 18px; }
          ul, ol { margin-left: 18px; }
          li   { margin: 4px 0; }
          .footer { margin-top: 28px; color: #7f8c8d; font-style: italic; }
        </style></head><body>
          <h1>🏛️ Survey Management System</h1>
          <p><b>Version:</b> 2.0.0 &nbsp;|&nbsp;
             <b>Copyright:</b> 2026 ASTROMAT GEO-SERVICES</p>
          <p><b>Email:</b> ugwusochukwuma@gmail.com</p>

          <h2>📋 About</h2>
          <p>Comprehensive digital archiving for Nigerian survey records with
          PostGIS spatial storage, traverse calculations, document management,
          multi-user access control, and an automated parcellation module.</p>

          <h2>✨ Features</h2>
          <ul>
            <li>📋 Survey metadata management with 36-state dropdown</li>
            <li>📍 Direct coordinate input &amp; plotting</li>
            <li>📐 Traverse calculator — WCB &amp; quadrant bearings,
                closure error with 1:5000 NIS precision standard</li>
            <li>📂 AutoCAD DXF import — reads Boundary polyline directly,
                no re-entry of existing plan data</li>
            <li>🤖 AI metadata extraction — no API key required</li>
            <li>🗺  PlotAccess — automatic road-aware, exact-area
                cadastral subdivision, hard road-access enforcement</li>
            <li>📄 Document management with MD5 checksum verification</li>
            <li>🗄️ Direct PostGIS layer loading</li>
            <li>🔍 Global search across all tables</li>
            <li>👥 Multi-user access control (superuser / surveyor / viewer)</li>
            <li>📋 Full audit trail of all actions</li>
            <li>⚠️ Adjoining survey detector</li>
          </ul>

          <h2>🗺  PlotAccess — Parcellation Module (v2.0)</h2>
          <ul>
            <li>Load perimeter by pasting coordinates or importing a DXF</li>
            <li>Roads are computed automatically by the engine — no
                manual road drawing needed</li>
            <li>Set target plot area, minimum frontage, road width, and
                optional cross-road spacing</li>
            <li>Road access is a hard constraint — every plot must front
                a real road corridor; landlocked plots are resolved
                automatically, never shipped</li>
            <li>Exact target area on 99%+ of plots via a Brent's-method
                root-finding engine, not approximate rounding</li>
            <li>Works on rotated and irregular perimeters</li>
            <li>Colour-coded plan with compliant/edge plot status,
                area schedule, and setting-out coordinate table</li>
            <li>Export to DXF, Excel, or a branded PDF report; print
                directly from the plugin</li>
          </ul>

          <h2>👥 User Roles</h2>
          <ul>
            <li><b>Superuser</b> — full access, create/disable users,
                view audit log</li>
            <li><b>Surveyor</b> — create and edit surveys, upload documents</li>
            <li><b>Viewer</b> — read-only access to all records</li>
          </ul>

          <h2>🔌 First-Time Setup</h2>
          <ol>
            <li>Click ⚙️ Database Settings and configure PostgreSQL</li>
            <li>Click the plugin icon — log in with admin / admin123</li>
            <li>Go to 👥 User Administration to create your real users</li>
            <li>Change the default admin password immediately</li>
          </ol>

          <div class="footer">
            "Preserving Nigeria's surveying heritage, one coordinate at a time."
          </div>
        </body></html>
        """
        QMessageBox.about(
            self.iface.mainWindow(),
            "About Survey Management System",
            help_text
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        """Open the main plugin window — requires login."""
        self.iface.messageBar().pushMessage(
            "Info", "Opening Survey Management System…",
            level=Qgis.Info, duration=2
        )

        # 0. First-run wizard
        from qgis.core import QgsSettings
        QgsSettings().remove("survey_management/gemini_api_key")
        setup_done = QgsSettings().value("survey_management/setup_complete", "")
        if not setup_done:
            try:
                from .setup_wizard import SetupWizard
                wizard = SetupWizard(self.iface.mainWindow())
                wizard.exec_()
                setup_done = QgsSettings().value(
                    "survey_management/setup_complete", "")
            except Exception as e:
                print(f"[SurveyMgmt] Wizard error: {e}")

        # 1. Ensure psycopg2
        psycopg2_ok = False
        try:
            import psycopg2 as _pg  # noqa
            psycopg2_ok = True
        except ImportError:
            lib_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "lib")
            if lib_dir not in sys.path:
                sys.path.append(lib_dir)
            try:
                import psycopg2 as _pg  # noqa
                psycopg2_ok = True
            except ImportError:
                pass

        if not psycopg2_ok:
            reply = QMessageBox.question(
                self.iface.mainWindow(), "psycopg2 Missing",
                "The psycopg2 library is not installed.\n\n"
                "The setup wizard can install it automatically.\n\n"
                "Continue in offline mode? (Database features disabled)",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
            db_connection = None
        else:
            if not self.connect_db():
                return
            db_connection = self.db_connection

        # 2. Login
        try:
            from .login_dialog import LoginDialog
        except ImportError as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "Plugin Error",
                f"Could not load login dialog:\n{str(e)}"
            )
            return

        if (self.dialog is not None and self.dialog.isVisible()
                and self.session_user is not None):
            self.dialog.raise_()
            self.dialog.activateWindow()
            return

        login = LoginDialog(
            self.iface.mainWindow(), db_connection=db_connection)
        if login.exec_() != QDialog.Accepted or not login.session_user:
            return

        self.session_user = login.session_user

        # 3. Load main dialog
        try:
            from .SurveyManagement_dialog import SurveyManagementDialog
        except ImportError as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "Plugin Error",
                f"Failed to load dialog module:\n{str(e)}\n\n"
                "Please reinstall the plugin."
            )
            return

        try:
            self.dialog = SurveyManagementDialog(
                self.iface.mainWindow(),
                db_connection=db_connection,
                session_user=self.session_user
            )
            self.dialog.setWindowModality(Qt.NonModal)
            self.dialog.setWindowFlags(
                Qt.Window |
                Qt.WindowTitleHint |
                Qt.WindowSystemMenuHint |
                Qt.WindowMinimizeButtonHint |
                Qt.WindowMaximizeButtonHint |
                Qt.WindowCloseButtonHint
            )
            self.dialog.finished.connect(self._on_dialog_closed)
            self.dialog.show()

        except Exception as e:
            self.iface.messageBar().pushMessage(
                "Error", f"Dialog error: {str(e)}",
                level=Qgis.Critical, duration=5
            )
            import traceback
            traceback.print_exc()
            QMessageBox.critical(
                self.iface.mainWindow(), "Plugin Error",
                f"An unexpected error occurred:\n\n{str(e)}\n\n"
                "Please check the Python console for details."
            )

    def _on_dialog_closed(self):
        self.dialog = None