# -*- coding: utf-8 -*-
"""
/***************************************************************************
 SurveyManagementDialog
                                 A QGIS plugin
 Digital archiving for Nigerian survey records
                              -------------------
        begin                : 2026-03-14
        copyright            : (C) 2026 by ASTROMAT GEO-SERVICES
        email                : ugwusochukwuma@gmail.com
 ***************************************************************************/
"""

import os
import math
import sys
import re
import subprocess
import hashlib
from datetime import date, datetime
from functools import partial

from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, QDate, QUrl, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QDateEdit, QPushButton,
    QTextEdit, QMessageBox, QGroupBox, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QWidget, QApplication, QStackedWidget,
    QFileDialog, QCheckBox, QProgressDialog, QSpinBox,
    QDoubleSpinBox, QGridLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QProgressBar, QAbstractItemView,
    QStatusBar, QSystemTrayIcon, QMenu, QRadioButton,
    QScrollArea
)
from qgis.PyQt.QtGui import QColor, QFont, QIcon

# QGIS imports
from qgis.core import (
    QgsProject, QgsGeometry, QgsFeature, 
    QgsVectorLayer, QgsPointXY, QgsField,
    QgsCoordinateReferenceSystem, QgsMarkerSymbol,
    QgsLineSymbol, QgsSingleSymbolRenderer, QgsDataSourceUri,
    QgsVectorLayerExporter, QgsWkbTypes, QgsSettings
)
from qgis.PyQt.QtCore import QVariant
from qgis.gui import QgsMessageBar

# Try to import psycopg2
try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None

# This loads your .ui file
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'SurveyManagement_dialog_base.ui'))


class TableLoaderThread(QThread):
    """Background thread for loading tables without freezing UI"""
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, connection_params):
        super().__init__()
        self.connection_params = connection_params
        
    def run(self):
        try:
            conn = psycopg2.connect(**self.connection_params)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            
            # Get all tables in public schema
            cur.execute("""
                SELECT 
                    table_name,
                    (SELECT COUNT(*) FROM information_schema.columns WHERE table_name=t.table_name) as column_count,
                    obj_description(c.oid) as table_description
                FROM information_schema.tables t
                LEFT JOIN pg_class c ON c.relname = t.table_name
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            
            tables = cur.fetchall()
            result = []
            
            for i, (table_name, col_count, description) in enumerate(tables):
                self.progress.emit(i + 1, len(tables), f"Scanning {table_name}...")
                
                # Check if table has geometry column
                cur.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = %s 
                    AND data_type IN ('USER-DEFINED', 'geometry')
                """, (table_name,))
                has_geometry = cur.fetchone() is not None
                
                # Get row count
                cur.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table_name)))
                row_count = cur.fetchone()[0]
                
                # Get column info
                cur.execute("""
                    SELECT column_name, data_type 
                    FROM information_schema.columns 
                    WHERE table_name = %s
                    ORDER BY ordinal_position
                    LIMIT 5
                """, (table_name,))
                columns = cur.fetchall()
                
                result.append({
                    'name': table_name,
                    'columns': col_count,
                    'rows': row_count,
                    'has_geometry': has_geometry,
                    'description': description or '',
                    'sample_columns': [f"{c[0]} ({c[1]})" for c in columns]
                })
            
            cur.close()
            conn.close()
            self.finished.emit(result)
            
        except Exception as e:
            self.error.emit(str(e))


class DataPreviewThread(QThread):
    """Background thread for loading data preview"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list, list)
    error = pyqtSignal(str)
    
    def __init__(self, connection_params, table_name, limit=100):
        super().__init__()
        self.connection_params = connection_params
        self.table_name = table_name
        self.limit = limit
        
    def run(self):
        try:
            conn = psycopg2.connect(**self.connection_params)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            
            # Get column names
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = %s
                ORDER BY ordinal_position
            """, (self.table_name,))
            columns = [c[0] for c in cur.fetchall()]
            
            # Get sample data
            self.progress.emit(f"Loading data from {self.table_name}...")
            cur.execute(sql.SQL('SELECT * FROM {} LIMIT %s').format(sql.Identifier(self.table_name)), (self.limit,))
            data = cur.fetchall()
            
            cur.close()
            conn.close()
            self.finished.emit(columns, data)
            
        except Exception as e:
            self.error.emit(str(e))


class BearingInputWidget(QWidget):
    """Custom widget for bearing input with multiple entry methods"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        
        # Input method selection
        method_layout = QHBoxLayout()
        method_layout.addWidget(QLabel("Input Method:"))
        
        self.input_method = QComboBox()
        self.input_method.addItems([
            "1. Spin Boxes (Easy)",
            "2. Text Entry (Type with shortcuts)",
            "3. Decimal Degrees"
        ])
        self.input_method.currentIndexChanged.connect(self.on_method_changed)
        method_layout.addWidget(self.input_method)
        method_layout.addStretch()
        layout.addLayout(method_layout)
        
        # ===== METHOD 1: Spin Boxes =====
        self.spin_widget = QWidget()
        spin_layout = QHBoxLayout()
        spin_layout.setContentsMargins(5, 5, 5, 5)
        
        # Quadrant/Type selection
        self.bearing_type = QComboBox()
        self.bearing_type.addItems([
            "Whole Circle (0-360°)",
            "Quadrant (NE, SE, SW, NW)"
        ])
        self.bearing_type.setFixedWidth(150)
        self.bearing_type.currentIndexChanged.connect(self.on_bearing_type_changed)
        spin_layout.addWidget(self.bearing_type)
        
        # Degrees
        spin_layout.addWidget(QLabel("Deg:"))
        self.degrees = QSpinBox()
        self.degrees.setRange(0, 359)
        self.degrees.setValue(0)
        self.degrees.setFixedWidth(70)
        self.degrees.setSuffix("°")
        spin_layout.addWidget(self.degrees)
        
        # Minutes
        spin_layout.addWidget(QLabel("Min:"))
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 59)
        self.minutes.setValue(0)
        self.minutes.setFixedWidth(60)
        self.minutes.setSuffix("'")
        spin_layout.addWidget(self.minutes)
        
        # Seconds
        spin_layout.addWidget(QLabel("Sec:"))
        self.seconds = QDoubleSpinBox()
        self.seconds.setRange(0, 59.999)
        self.seconds.setValue(0)
        self.seconds.setFixedWidth(80)
        self.seconds.setSuffix('"')
        self.seconds.setDecimals(3)
        spin_layout.addWidget(self.seconds)
        
        # Quadrant direction (for quadrant bearings)
        spin_layout.addWidget(QLabel("Quad:"))
        self.quadrant = QComboBox()
        self.quadrant.addItems(["NE", "SE", "SW", "NW"])
        self.quadrant.setFixedWidth(60)
        self.quadrant.setEnabled(False)
        spin_layout.addWidget(self.quadrant)
        
        spin_layout.addStretch()
        self.spin_widget.setLayout(spin_layout)
        
        # ===== METHOD 2: Text Entry =====
        self.text_widget = QWidget()
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(5, 5, 5, 5)
        
        # Instructions
        instructions = QLabel(
            "Type bearing using these formats:\n"
            "• 45°30'15\" - Use 'd' for °, 'm' for ', 's' for \"\n"
            "• 45.5042° - Decimal degrees\n"
            "• N45°30'E - Quadrant format\n\n"
            "Examples: 45d30m15s, N45d30mE, 45.5042"
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("background-color: #ecf0f1; padding: 5px;")
        text_layout.addWidget(instructions)
        
        text_input_layout = QHBoxLayout()
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("e.g., 45d30m15s  or  N45d30mE  or  45.5042")
        self.text_input.setMinimumWidth(400)
        self.text_input.textChanged.connect(self.on_text_changed)
        text_input_layout.addWidget(self.text_input)
        
        # Quick buttons for symbols
        btn_layout = QVBoxLayout()
        
        deg_btn = QPushButton("° (d)")
        deg_btn.setFixedWidth(60)
        deg_btn.clicked.connect(lambda: self.insert_symbol("d"))
        btn_layout.addWidget(deg_btn)
        
        min_btn = QPushButton("' (m)")
        min_btn.setFixedWidth(60)
        min_btn.clicked.connect(lambda: self.insert_symbol("m"))
        btn_layout.addWidget(min_btn)
        
        sec_btn = QPushButton('" (s)')
        sec_btn.setFixedWidth(60)
        sec_btn.clicked.connect(lambda: self.insert_symbol("s"))
        btn_layout.addWidget(sec_btn)
        
        text_input_layout.addLayout(btn_layout)
        text_input_layout.addStretch()
        text_layout.addLayout(text_input_layout)
        
        # Preview
        preview_layout = QHBoxLayout()
        preview_layout.addWidget(QLabel("Preview:"))
        self.text_preview = QLabel("0°00'00\"")
        self.text_preview.setStyleSheet("font-weight: bold; color: #27ae60; font-size: 12pt;")
        preview_layout.addWidget(self.text_preview)
        preview_layout.addStretch()
        text_layout.addLayout(preview_layout)
        
        self.text_widget.setLayout(text_layout)
        
        # ===== METHOD 3: Decimal Degrees =====
        self.decimal_widget = QWidget()
        decimal_layout = QHBoxLayout()
        decimal_layout.setContentsMargins(5, 5, 5, 5)
        
        decimal_layout.addWidget(QLabel("Decimal Degrees:"))
        self.decimal_input = QDoubleSpinBox()
        self.decimal_input.setRange(0, 359.999999)
        self.decimal_input.setValue(0)
        self.decimal_input.setSuffix("°")
        self.decimal_input.setDecimals(6)
        self.decimal_input.setFixedWidth(150)
        self.decimal_input.valueChanged.connect(self.on_decimal_changed)
        decimal_layout.addWidget(self.decimal_input)
        
        decimal_layout.addWidget(QLabel("="))
        self.decimal_preview = QLabel("0°00'00\"")
        self.decimal_preview.setStyleSheet("font-weight: bold; color: #27ae60; font-size: 12pt;")
        decimal_layout.addWidget(self.decimal_preview)
        decimal_layout.addStretch()
        
        self.decimal_widget.setLayout(decimal_layout)
        
        # Stack widget for methods
        self.stack = QStackedWidget()
        self.stack.addWidget(self.spin_widget)
        self.stack.addWidget(self.text_widget)
        self.stack.addWidget(self.decimal_widget)
        
        layout.addWidget(self.stack)
        
        # Current values
        self.current_decimal = 0.0
        self.current_dms = "0°00'00\""
        
        self.setLayout(layout)
        
    def on_method_changed(self, index):
        """Handle input method change"""
        self.stack.setCurrentIndex(index)
        
    def on_bearing_type_changed(self, index):
        """Handle bearing type change (WCB vs Quadrant)"""
        self.quadrant.setEnabled(index == 1)
        if index == 1:  # Quadrant mode
            self.degrees.setRange(0, 89)
        else:
            self.degrees.setRange(0, 359)
    
    def insert_symbol(self, symbol):
        """Insert symbol at cursor position"""
        if self.text_input.hasFocus():
            cursor_pos = self.text_input.cursorPosition()
            current_text = self.text_input.text()
            new_text = current_text[:cursor_pos] + symbol + current_text[cursor_pos:]
            self.text_input.setText(new_text)
            self.text_input.setCursorPosition(cursor_pos + 1)
    
    def on_text_changed(self, text):
        """Handle text input"""
        if not text:
            self.text_preview.setText("0°00'00\"")
            self.current_decimal = 0
            return
            
        try:
            decimal = self.parse_text_to_decimal(text)
            self.current_decimal = decimal
            dms = self.decimal_to_dms(decimal)
            self.text_preview.setText(dms)
        except:
            self.text_preview.setText("Invalid format")
    
    def on_decimal_changed(self, value):
        """Handle decimal input change"""
        self.current_decimal = value
        dms = self.decimal_to_dms(value)
        self.decimal_preview.setText(dms)
    
    def parse_text_to_decimal(self, text):
        """Parse text input to decimal degrees"""
        text = text.strip().upper()
        
        # Try quadrant format first (N45°30'E or N45d30mE)
        quad_match = re.match(r'^([NSEW])(\d+)[°d](\d+)[\'m]?([\d.]*)[\"s]?([NSEW])?$', text)
        if quad_match:
            quad1 = quad_match.group(1)
            degrees = float(quad_match.group(2))
            minutes = float(quad_match.group(3))
            seconds = float(quad_match.group(4) or 0)
            quad2 = quad_match.group(5) or ""
            
            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
            
            if quad1 == 'N' and (quad2 == 'E' or quad2 == ''):
                return decimal
            elif quad1 == 'N' and quad2 == 'W':
                return 360 - decimal
            elif quad1 == 'S' and quad2 == 'E':
                return 180 - decimal
            elif quad1 == 'S' and quad2 == 'W':
                return 180 + decimal
            elif quad1 == 'E':
                return decimal
            elif quad1 == 'W':
                return 360 - decimal
        
        # Try DMS format (45°30'15" or 45d30m15s)
        dms_match = re.search(r'(\d+)[°d](\d+)[\'m]?([\d.]*)[\"s]?', text)
        if dms_match:
            degrees = float(dms_match.group(1))
            minutes = float(dms_match.group(2))
            seconds = float(dms_match.group(3) or 0)
            return degrees + (minutes / 60.0) + (seconds / 3600.0)
        
        # Try decimal
        try:
            return float(text)
        except:
            raise ValueError(f"Could not parse bearing: {text}")
    
    def decimal_to_dms(self, decimal):
        """Convert decimal degrees to DMS string"""
        degrees = int(decimal)
        minutes_float = (decimal - degrees) * 60
        minutes = int(minutes_float)
        seconds = (minutes_float - minutes) * 60
        return f"{degrees}°{minutes:02d}'{seconds:05.2f}\""
    
    def get_decimal_degrees(self):
        """Get current value in decimal degrees"""
        method = self.input_method.currentIndex()
        
        if method == 0:  # Spin boxes
            if self.bearing_type.currentIndex() == 0:  # Whole Circle
                return self.degrees.value() + (self.minutes.value() / 60.0) + (self.seconds.value() / 3600.0)
            else:  # Quadrant
                angle = self.degrees.value() + (self.minutes.value() / 60.0) + (self.seconds.value() / 3600.0)
                quad = self.quadrant.currentText()
                if quad == "NE":
                    return angle
                elif quad == "SE":
                    return 180 - angle
                elif quad == "SW":
                    return 180 + angle
                else:  # NW
                    return 360 - angle
        elif method == 1:  # Text entry
            return self.current_decimal
        else:  # Decimal
            return self.decimal_input.value()
    
    def get_dms_string(self):
        """Get formatted DMS string"""
        decimal = self.get_decimal_degrees()
        return self.decimal_to_dms(decimal)
    
    def get_quadrant_string(self):
        """Get quadrant format if applicable"""
        decimal = self.get_decimal_degrees()
        
        if decimal <= 90:
            return f"N{self.decimal_to_dms(decimal)}E"
        elif decimal <= 180:
            return f"S{self.decimal_to_dms(180 - decimal)}E"
        elif decimal <= 270:
            return f"S{self.decimal_to_dms(decimal - 180)}W"
        else:
            return f"N{self.decimal_to_dms(360 - decimal)}W"
    
    def set_from_decimal(self, decimal):
        """Set widget values from decimal degrees"""
        self.input_method.setCurrentIndex(2)
        self.decimal_input.setValue(decimal)
    
    def clear(self):
        """Clear all inputs"""
        self.input_method.setCurrentIndex(0)
        self.degrees.setValue(0)
        self.minutes.setValue(0)
        self.seconds.setValue(0)
        self.quadrant.setCurrentIndex(0)
        self.bearing_type.setCurrentIndex(0)
        self.text_input.clear()
        self.decimal_input.setValue(0)
        self.current_decimal = 0


class SurveyManagementDialog(QDialog, FORM_CLASS):
    """Main dialog for Survey Management System"""
    
    def __init__(self, parent=None, db_connection=None, session_user=None):
        """Constructor."""
        super(SurveyManagementDialog, self).__init__(parent)

        # Set up the user interface from Designer
        self.setupUi(self)

        # Store database connection
        self.db_connection = db_connection
        self.session_user  = session_user or {"username": "guest", "role": "viewer", "user_id": None}
        self.current_survey_id = None
        self.current_srid = 26332  # Default to Nigeria Mid Belt
        self.pdf_base_path = "C:\\SurveyRecords\\"
        self.minimize_to_tray = True
        self.tray_icon = None
        self.table_data = None
        self.all_surveys = []
        self.global_survey_results = []
        self.global_point_results  = []
        self.global_doc_results    = []

        # Tab widget references (set in setup methods, used for safe indexOf)
        self._documents_tab_widget = None
        self._traverse_tab_widget  = None

        # Store reference to iface
        self.iface = parent
        
        # Show database status
        self.db_available = (db_connection is not None and 
                            PSYCOPG2_AVAILABLE and 
                            not db_connection.closed)
        
        # Set window properties - NON-MODAL TOOL WINDOW
        role  = self.session_user.get("role", "viewer")
        uname = self.session_user.get("username", "guest")
        self.setWindowTitle(f"Survey Management System  |  {uname}  [{role.upper()}]")
        self.setMinimumWidth(1200)
        self.setMinimumHeight(800)
        
        # Set window flags for non-modal operation
        self.setWindowFlags(
            Qt.Window |
            Qt.WindowTitleHint |
            Qt.WindowSystemMenuHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.WindowCloseButtonHint
        )
        
        # Make it non-modal
        self.setWindowModality(Qt.NonModal)
        
        # Center the dialog
        self.center_on_screen()
        
        # Clear any existing layout
        if self.layout() is not None:
            QWidget().setLayout(self.layout())
        
        # Create main layout (scrollable)
        self.create_main_layout()
        
        # Create status bar for silent messages
        self.status_bar = QStatusBar()
        self.layout().addWidget(self.status_bar)
        
        # Create system tray icon
        self.create_tray_icon()
        
        # Load initial data if database available
        if self.db_available:
            self.ensure_document_table_exists()
            self.refresh_table_list()  # Load tables in PostgreSQL tab
            self.load_all_surveys()

    def center_on_screen(self):
        """Center the dialog on the screen"""
        frame_geometry = self.frameGeometry()
        screen_center = QApplication.primaryScreen().availableGeometry().center()
        frame_geometry.moveCenter(screen_center)
        self.move(frame_geometry.topLeft())
        
    def show_status(self, message, timeout=3000):
        """Show message in status bar instead of popup"""
        self.status_bar.showMessage(message, timeout)

    # ------------------------------------------------------------------
    # Access control helpers
    # ------------------------------------------------------------------

    def require_role(self, minimum_role):
        """Return True if the current session user meets the minimum role.
        Roles in ascending order: viewer < surveyor < superuser."""
        hierarchy = {'viewer': 0, 'surveyor': 1, 'superuser': 2}
        user_level     = hierarchy.get(self.session_user.get('role', 'viewer'), 0)
        required_level = hierarchy.get(minimum_role, 99)
        if user_level < required_level:
            QMessageBox.warning(
                self, "Access Denied",
                f"This action requires '{minimum_role}' access.\n\n"
                f"Your current role: {self.session_user.get('role', 'viewer')}"
            )
            return False
        return True

    def write_audit(self, action, table_name=None, record_id=None,
                    old_values=None, new_values=None):
        """Write a row to audit_log. Silently skips if DB not available."""
        if not self.db_available:
            return
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("""
                INSERT INTO audit_log
                    (user_id, username, action, table_name,
                     record_id, old_values, new_values)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                self.session_user.get("user_id"),
                self.session_user.get("username"),
                action, table_name, record_id,
                str(old_values) if old_values else None,
                str(new_values) if new_values else None
            ))
            cur.close()
        except Exception as e:
            print(f"[Audit] write_audit: {e}")

    def create_tray_icon(self):
        """Create system tray icon for background operation"""
        # Check if system tray is supported
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.png')
        if os.path.exists(icon_path):
            self.tray_icon = QSystemTrayIcon(self)
            self.tray_icon.setIcon(QIcon(icon_path))
            self.tray_icon.setToolTip("Survey Management System")
            
            # Create tray menu
            tray_menu = QMenu()
            
            show_action = tray_menu.addAction("Show Window")
            show_action.triggered.connect(self.show_window)
            
            hide_action = tray_menu.addAction("Hide to Tray")
            hide_action.triggered.connect(self.hide)
            
            tray_menu.addSeparator()
            
            quit_action = tray_menu.addAction("Exit")
            quit_action.triggered.connect(self.close)
            
            self.tray_icon.setContextMenu(tray_menu)
            self.tray_icon.activated.connect(self.on_tray_activated)
            
            self.tray_icon.show()

    def on_tray_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_window()

    def show_window(self):
        """Show and restore window"""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _open_user_admin(self):
        """Open the user administration dialog (superuser only)."""
        if self.session_user.get("role") != "superuser":
            QMessageBox.warning(self, "Access Denied",
                                "User Administration requires superuser access.")
            return
        try:
            from .user_admin_dialog import UserAdminDialog
            dlg = UserAdminDialog(self, db_connection=self.db_connection,
                                  session_user=self.session_user)
            dlg.exec_()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open User Admin:\n{str(e)}")

    def changeEvent(self, event):
        """Handle window state changes"""
        if event.type() == event.WindowStateChange:
            if self.windowState() & Qt.WindowMinimized and self.minimize_to_tray and self.tray_icon:
                self.hide()
                self.tray_icon.showMessage(
                    "Survey Management System",
                    "Application minimized to system tray",
                    QSystemTrayIcon.Information,
                    2000
                )
        super().changeEvent(event)

    def closeEvent(self, event):
        """Handle close event"""
        if self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.hide()
        
        # Clean up database connection
        if self.db_connection and not self.db_connection.closed:
            self.db_connection.close()
        
        event.accept()

    def toggle_always_on_top(self, checked):
        """Toggle always on top behavior"""
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def hide_to_tray(self):
        """Hide window to system tray"""
        self.hide()
        if self.tray_icon:
            self.tray_icon.showMessage(
                "Survey Management System",
                "Application running in background.\nDouble-click tray icon to show.",
                QSystemTrayIcon.Information,
                3000
            )

    def float_window(self):
        """Float the window (remove any docking behavior)"""
        self.setWindowFlags(Qt.Window)
        self.show()
        self.center_on_screen()

    def ensure_document_table_exists(self):
        """Ensure the survey_documents table exists"""
        if not self.db_available:
            return
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            
            # Check if table exists
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'survey_documents'
                )
            """)
            table_exists = cur.fetchone()[0]
            
            if not table_exists:
                # Create the survey_documents table
                cur.execute("""
                    CREATE TABLE survey_documents (
                        document_id SERIAL PRIMARY KEY,
                        survey_id INTEGER REFERENCES surveys(survey_id) ON DELETE CASCADE,
                        file_path TEXT NOT NULL,
                        file_name VARCHAR(255),
                        file_size INTEGER,
                        checksum VARCHAR(64),
                        checksum_algorithm VARCHAR(10) DEFAULT 'MD5',
                        is_primary BOOLEAN DEFAULT TRUE,
                        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_verified DATE,
                        description TEXT
                    )
                """)
                print("Created survey_documents table")
            
            cur.close()
            
        except Exception as e:
            print(f"Error ensuring document table: {e}")

    def create_main_layout(self):
        """Create the main layout with all tabs in a scrollable area"""
        # Create a scroll area for the entire dialog
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # Create main widget that will go inside scroll area
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        
        # Header
        header_layout = QHBoxLayout()
        title = QLabel("🏛️ NIGERIAN SURVEY MANAGEMENT SYSTEM")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #2c3e50;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Logged-in user badge
        role  = self.session_user.get("role", "viewer")
        uname = self.session_user.get("full_name") or self.session_user.get("username", "guest")
        role_colors = {"superuser": "#8e44ad", "surveyor": "#27ae60", "viewer": "#2980b9"}
        role_color  = role_colors.get(role, "#2980b9")
        user_lbl = QLabel(f"👤  {uname}  [{role.upper()}]")
        user_lbl.setStyleSheet(
            f"color:{role_color}; font-weight:bold; font-size:10pt; "
            "background:#f0f0f0; padding:4px 10px; border-radius:4px;"
        )
        header_layout.addWidget(user_lbl)

        # User admin shortcut (superusers only)
        if role == "superuser":
            admin_btn = QPushButton("👥 User Admin")
            admin_btn.setStyleSheet(
                "background-color:#8e44ad; color:white; font-weight:bold;"
                " padding:4px 10px; border-radius:4px;"
            )
            admin_btn.clicked.connect(self._open_user_admin)
            header_layout.addWidget(admin_btn)

        # Database status
        if self.db_available:
            status_text = "✅ DATABASE CONNECTED"
            status_color = "green"
        else:
            status_text = "⚠️ OFFLINE MODE"
            status_color = "orange"

        status_label = QLabel(status_text)
        status_label.setStyleSheet(f"color: {status_color}; font-weight: bold;")
        header_layout.addWidget(status_label)
        main_layout.addLayout(header_layout)

        # Separator
        separator = QLabel()
        separator.setFrameStyle(QLabel.HLine)
        separator.setStyleSheet("background-color: #bdc3c7; max-height: 1px;")
        main_layout.addWidget(separator)
        
        # CRS Selection
        crs_group = QGroupBox("1. SELECT COORDINATE REFERENCE SYSTEM (CRS)")
        crs_group.setStyleSheet("QGroupBox { font-weight: bold; color: #27ae60; }")
        crs_layout = QHBoxLayout()
        
        self.crs_combo = QComboBox()
        self.crs_combo.setMinimumWidth(400)
        crs_list = [
            "EPSG:26331 - Minna / Nigeria West",
            "EPSG:26332 - Minna / Nigeria Mid Belt",
            "EPSG:26333 - Minna / Nigeria East",
            "EPSG:4326 - WGS 84 (Lat/Lon)",
            "EPSG:32631 - WGS 84 / UTM zone 31N",
            "EPSG:32632 - WGS 84 / UTM zone 32N",
            "Custom EPSG (specify below)"
        ]
        self.crs_combo.addItems(crs_list)
        self.crs_combo.setCurrentIndex(1)
        self.crs_combo.currentTextChanged.connect(self.on_crs_changed)
        crs_layout.addWidget(QLabel("Select CRS:"))
        crs_layout.addWidget(self.crs_combo)
        
        self.custom_crs = QLineEdit()
        self.custom_crs.setPlaceholderText("Enter EPSG code (e.g., 26332)")
        self.custom_crs.setEnabled(False)
        self.custom_crs.setMaximumWidth(150)
        self.custom_crs.textChanged.connect(self._on_custom_crs_changed)
        crs_layout.addWidget(self.custom_crs)

        self.custom_crs_apply_btn = QPushButton("Apply")
        self.custom_crs_apply_btn.setMaximumWidth(60)
        self.custom_crs_apply_btn.setEnabled(False)
        self.custom_crs_apply_btn.setStyleSheet(
            "background-color: #1A5C38; color: white; font-weight: bold;"
        )
        self.custom_crs_apply_btn.clicked.connect(self._apply_custom_crs)
        crs_layout.addWidget(self.custom_crs_apply_btn)

        self.custom_crs_status = QLabel("")
        self.custom_crs_status.setStyleSheet("font-size: 10pt;")
        crs_layout.addWidget(self.custom_crs_status)
        
        crs_layout.addStretch()
        crs_group.setLayout(crs_layout)
        main_layout.addWidget(crs_group)
        
        # Current survey indicator
        self.current_survey_label = QLabel("📋 Current Survey: None")
        self.current_survey_label.setStyleSheet("font-weight: bold; color: #2980b9; font-size: 11pt;")
        main_layout.addWidget(self.current_survey_label)
        
        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabBar::tab { height: 35px; min-width: 180px; font-weight: bold; }
            QTabBar::tab:selected { background-color: #3498db; color: white; }
        """)
        main_layout.addWidget(self.tab_widget)
        
        # Create tabs
        self.setup_survey_tab()
        self.setup_documents_tab()
        self.setup_coordinate_tab()
        self.setup_traverse_tab()
        self.setup_postgis_tab()
        self.setup_global_search_tab()
        
        # Window behavior options
        float_group = QGroupBox("Window Behavior")
        float_layout = QHBoxLayout()
        
        self.always_on_top_cb = QCheckBox("Always on Top")
        self.always_on_top_cb.toggled.connect(self.toggle_always_on_top)
        float_layout.addWidget(self.always_on_top_cb)
        
        self.minimize_to_tray_cb = QCheckBox("Minimize to System Tray")
        self.minimize_to_tray_cb.setChecked(True)
        self.minimize_to_tray_cb.toggled.connect(lambda x: setattr(self, 'minimize_to_tray', x))
        float_layout.addWidget(self.minimize_to_tray_cb)
        
        float_layout.addStretch()
        float_group.setLayout(float_layout)
        main_layout.addWidget(float_group)
        
        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.minimize_btn = QPushButton("➖ Minimize")
        self.minimize_btn.setMinimumWidth(100)
        self.minimize_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        self.minimize_btn.clicked.connect(self.showMinimized)
        button_layout.addWidget(self.minimize_btn)
        
        self.hide_btn = QPushButton("⬇️ Hide to Tray")
        self.hide_btn.setMinimumWidth(120)
        self.hide_btn.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold;")
        self.hide_btn.clicked.connect(self.hide_to_tray)
        button_layout.addWidget(self.hide_btn)
        
        self.float_btn = QPushButton("🪟 Float")
        self.float_btn.setMinimumWidth(100)
        self.float_btn.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold;")
        self.float_btn.clicked.connect(self.float_window)
        button_layout.addWidget(self.float_btn)
        
        close_btn = QPushButton("✖ Close")
        close_btn.setMinimumWidth(100)
        close_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)
        
        main_layout.addLayout(button_layout)
        
        # Hint about non-modal mode
        hint = QLabel("💡 This window is NON-MODAL - you can work in QGIS while keeping it open!")
        hint.setStyleSheet("color: #27ae60; font-weight: bold;")
        main_layout.addWidget(hint)
        
        # Set the main widget as the scroll area's widget
        scroll.setWidget(main_widget)
        
        # Create a layout for the dialog and add the scroll area
        dialog_layout = QVBoxLayout()
        dialog_layout.addWidget(scroll)
        self.setLayout(dialog_layout)

    def on_crs_changed(self, text):
        """Handle CRS selection change"""
        is_custom = (text == "Custom EPSG (specify below)")
        self.custom_crs.setEnabled(is_custom)
        self.custom_crs_apply_btn.setEnabled(is_custom)
        self.custom_crs_status.setText("")

        if not is_custom:
            try:
                epsg_code = text.split(" - ")[0].replace("EPSG:", "")
                self.current_srid = int(epsg_code)
                self.show_status(f"CRS set to EPSG:{epsg_code}")
            except:
                self.current_srid = 26332
        else:
            # Keep previous srid until user confirms custom code
            self.custom_crs.setFocus()

    def _on_custom_crs_changed(self, text):
        """Validate EPSG code as user types."""
        text = text.strip()
        if not text:
            self.custom_crs_status.setText("")
            self.custom_crs_apply_btn.setEnabled(True)
            return
        try:
            epsg = int(text)
            crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
            if crs.isValid():
                self.custom_crs_status.setText(f"✅ {crs.description()[:35]}")
                self.custom_crs_status.setStyleSheet("color: #1A5C38; font-size: 10pt;")
            else:
                self.custom_crs_status.setText("❌ Unknown EPSG code")
                self.custom_crs_status.setStyleSheet("color: #C62828; font-size: 10pt;")
        except ValueError:
            self.custom_crs_status.setText("❌ Numbers only")
            self.custom_crs_status.setStyleSheet("color: #C62828; font-size: 10pt;")
        self.custom_crs_apply_btn.setEnabled(True)

    def _apply_custom_crs(self):
        """Apply the typed custom EPSG code."""
        text = self.custom_crs.text().strip()
        if not text:
            QMessageBox.warning(self, "No EPSG Code", "Please enter an EPSG code first.")
            return
        try:
            epsg = int(text)
            crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
            if crs.isValid():
                self.current_srid = epsg
                self.custom_crs_status.setText(f"✅ Active: {crs.description()[:35]}")
                self.custom_crs_status.setStyleSheet(
                    "color: #1A5C38; font-weight: bold; font-size: 10pt;"
                )
                self.show_status(f"Custom CRS set: EPSG:{epsg} — {crs.description()}")
            else:
                QMessageBox.warning(
                    self, "Invalid EPSG Code",
                    f"EPSG:{epsg} is not recognised by QGIS.\n\n"
                    "Check the code at epsg.io or spatialreference.org"
                )
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter numbers only.")

    def get_current_crs(self):
        """Get the currently active CRS. Always returns a valid CRS."""
        if self.crs_combo.currentText() == "Custom EPSG (specify below)":
            if self.current_srid:
                crs = QgsCoordinateReferenceSystem(f"EPSG:{self.current_srid}")
                if crs.isValid():
                    return crs
            # Not yet applied — prompt
            QMessageBox.warning(
                self, "Custom CRS Not Applied",
                "Please enter your EPSG code and click the Apply button\n"
                "before saving coordinates."
            )
            return None
        else:
            text = self.crs_combo.currentText()
            epsg_code = text.split(" - ")[0].replace("EPSG:", "")
            self.current_srid = int(epsg_code)
            return QgsCoordinateReferenceSystem(f"EPSG:{epsg_code}")

    # ========== SURVEY METADATA TAB ==========
    def setup_survey_tab(self):
        """Tab for survey metadata entry with searchable table for loading surveys"""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        # ===== SEARCHABLE SURVEY LOADER =====
        load_group = QGroupBox("🔍 SEARCH & LOAD EXISTING SURVEY")
        load_group.setStyleSheet("QGroupBox { font-weight: bold; color: #2980b9; }")
        load_layout = QVBoxLayout()
        
        # Search bar
        search_layout = QHBoxLayout()
        self.survey_search_input = QLineEdit()
        self.survey_search_input.setPlaceholderText("Type to search by Plan Number, Owner Name, Surveyor, LGA, or State...")
        self.survey_search_input.setMinimumHeight(35)
        self.survey_search_input.textChanged.connect(self.filter_survey_table)
        search_layout.addWidget(self.survey_search_input, 3)
        
        refresh_btn = QPushButton("🔄 Refresh List")
        refresh_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px;")
        refresh_btn.clicked.connect(self.load_all_surveys)
        search_layout.addWidget(refresh_btn, 1)
        
        load_layout.addLayout(search_layout)
        
        # Search options
        options_layout = QHBoxLayout()
        options_layout.addWidget(QLabel("Search in:"))
        
        self.search_field_combo = QComboBox()
        self.search_field_combo.addItems([
            "All Fields",
            "Plan Number",
            "Owner Name",
            "Surveyor Name",
            "LGA",
            "State",
            "Survey ID"
        ])
        options_layout.addWidget(self.search_field_combo)
        
        options_layout.addStretch()
        options_layout.addWidget(QLabel("Show:"))
        
        self.limit_combo = QComboBox()
        self.limit_combo.addItems(["50", "100", "250", "500", "1000", "All"])
        self.limit_combo.setCurrentIndex(2)  # Default to 250
        self.limit_combo.currentTextChanged.connect(self.load_all_surveys)
        options_layout.addWidget(self.limit_combo)
        
        load_layout.addLayout(options_layout)
        
        # Results count
        self.survey_count_label = QLabel("Loading surveys...")
        self.survey_count_label.setStyleSheet("color: #7f8c8d; font-style: italic;")
        load_layout.addWidget(self.survey_count_label)
        
        # Survey table
        self.survey_table = QTableWidget()
        self.survey_table.setColumnCount(7)
        self.survey_table.setHorizontalHeaderLabels([
            "ID", "Plan Number", "Owner Name", "Surveyor", "LGA", "State", "Date"
        ])
        self.survey_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.survey_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.survey_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.survey_table.setAlternatingRowColors(True)
        self.survey_table.setMinimumHeight(200)
        self.survey_table.itemDoubleClicked.connect(self.load_survey_from_table)
        load_layout.addWidget(self.survey_table)
        
        # Load button
        load_btn_layout = QHBoxLayout()
        load_btn_layout.addStretch()
        
        load_selected_btn = QPushButton("📂 Load Selected Survey")
        load_selected_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        load_selected_btn.clicked.connect(self.load_selected_survey_from_table)
        load_btn_layout.addWidget(load_selected_btn)
        
        load_layout.addLayout(load_btn_layout)
        
        load_group.setLayout(load_layout)
        layout.addWidget(load_group)
        
        # ===== SURVEY METADATA FORM =====
        form_group = QGroupBox("SURVEY METADATA")
        form_group.setStyleSheet("QGroupBox { font-weight: bold; color: #27ae60; }")
        form_layout = QFormLayout()
        form_layout.setSpacing(10)
        
        self.survey_id_display = QLineEdit()
        self.survey_id_display.setReadOnly(True)
        self.survey_id_display.setPlaceholderText("Auto-generated when saved")
        self.survey_id_display.setStyleSheet("background-color: #f0f0f0;")
        form_layout.addRow("Survey ID:", self.survey_id_display)
        
        self.plan_number = QLineEdit()
        self.plan_number.setPlaceholderText("e.g., PLT-2024-001")
        form_layout.addRow("Plan Number:*", self.plan_number)
        
        self.owner_name = QLineEdit()
        self.owner_name.setPlaceholderText("Full name of land owner")
        form_layout.addRow("Owner Name:*", self.owner_name)
        
        self.survey_date = QDateEdit()
        self.survey_date.setDate(QDate.currentDate())
        self.survey_date.setCalendarPopup(True)
        form_layout.addRow("Survey Date:", self.survey_date)
        
        self.surveyor = QLineEdit()
        self.surveyor.setPlaceholderText("Name of surveyor")
        form_layout.addRow("Surveyor:", self.surveyor)
        
        self.lga = QLineEdit()
        self.lga.setPlaceholderText("Local Government Area")
        form_layout.addRow("LGA:", self.lga)
        
        self.state = QComboBox()
        states = ["Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", 
                  "Benue", "Borno", "Cross River", "Delta", "Ebonyi", "Edo", 
                  "Ekiti", "Enugu", "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", 
                  "Katsina", "Kebbi", "Kogi", "Kwara", "Lagos", "Nasarawa", "Niger", 
                  "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers", "Sokoto", 
                  "Taraba", "Yobe", "Zamfara", "FCT - Abuja"]
        self.state.addItems(states)
        form_layout.addRow("State:", self.state)
        
        self.notes = QTextEdit()
        self.notes.setMaximumHeight(80)
        form_layout.addRow("Notes:", self.notes)
        
        form_group.setLayout(form_layout)
        layout.addWidget(form_group)
        
        # Action buttons
        button_group = QGroupBox("Actions")
        button_layout = QHBoxLayout()
        
        self.save_survey_btn = QPushButton("💾 Save New Survey")
        self.save_survey_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        self.save_survey_btn.clicked.connect(self.save_survey_metadata)
        button_layout.addWidget(self.save_survey_btn)
        
        self.update_survey_btn = QPushButton("🔄 Update Current Survey")
        self.update_survey_btn.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 8px;")
        self.update_survey_btn.clicked.connect(self.update_survey_metadata)
        self.update_survey_btn.setEnabled(False)
        button_layout.addWidget(self.update_survey_btn)
        
        self.clear_survey_btn = QPushButton("🗑️ Clear Form")
        self.clear_survey_btn.setStyleSheet("background-color: #3498db; color: white; padding: 8px;")
        self.clear_survey_btn.clicked.connect(self.clear_survey_metadata)
        button_layout.addWidget(self.clear_survey_btn)
        
        button_group.setLayout(button_layout)
        layout.addWidget(button_group)
        
        layout.addStretch()
        tab.setLayout(layout)
        self.tab_widget.addTab(tab, "📋 Survey Metadata")

    # ========== SURVEY TABLE METHODS ==========
    def load_all_surveys(self):
        """Load all surveys into the table"""
        if not self.db_available:
            return
        
        try:
            self.survey_count_label.setText("Loading surveys...")
            QApplication.processEvents()
            
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            
            # Get limit
            limit_text = self.limit_combo.currentText()
            if limit_text == "All":
                limit_clause = ""
            else:
                limit_clause = f"LIMIT {limit_text}"
            
            base_query = """
                SELECT survey_id, plan_number, owner_name, surveyor_name, 
                       local_government, state, survey_date
                FROM surveys 
                ORDER BY survey_date DESC NULLS LAST, survey_id DESC
            """
            if limit_clause:
                cur.execute(base_query + " LIMIT %s", (int(limit_text),))
            else:
                cur.execute(base_query)
            
            results = cur.fetchall()
            cur.close()
            
            self.all_surveys = results  # Store for filtering
            self.display_survey_table(results)
            self.survey_count_label.setText(f"📊 Found {len(results)} surveys")
            
        except Exception as e:
            print(f"Error loading surveys: {e}")
            self.survey_count_label.setText(f"❌ Error loading surveys")

    def display_survey_table(self, surveys):
        """Display surveys in the table"""
        self.survey_table.setRowCount(len(surveys))
        
        for row, survey in enumerate(surveys):
            survey_id, plan_number, owner_name, surveyor_name, lga, state, survey_date = survey
            
            self.survey_table.setItem(row, 0, QTableWidgetItem(str(survey_id)))
            self.survey_table.setItem(row, 1, QTableWidgetItem(plan_number or ""))
            self.survey_table.setItem(row, 2, QTableWidgetItem(owner_name or ""))
            self.survey_table.setItem(row, 3, QTableWidgetItem(surveyor_name or ""))
            self.survey_table.setItem(row, 4, QTableWidgetItem(lga or ""))
            self.survey_table.setItem(row, 5, QTableWidgetItem(state or ""))
            
            date_str = survey_date.strftime("%Y-%m-%d") if survey_date else ""
            self.survey_table.setItem(row, 6, QTableWidgetItem(date_str))
            
            # Store survey_id in first column for easy access
            self.survey_table.item(row, 0).setData(Qt.UserRole, survey_id)

    def filter_survey_table(self):
        """Filter the survey table based on search input"""
        if not hasattr(self, 'all_surveys') or not self.all_surveys:
            return
        
        search_text = self.survey_search_input.text().strip().lower()
        search_field = self.search_field_combo.currentText()
        
        if not search_text:
            # Show all
            self.display_survey_table(self.all_surveys)
            self.survey_count_label.setText(f"📊 Found {len(self.all_surveys)} surveys")
            return
        
        filtered = []
        for survey in self.all_surveys:
            survey_id, plan_number, owner_name, surveyor_name, lga, state, survey_date = survey
            
            # Convert to strings for searching
            id_str = str(survey_id)
            plan_str = (plan_number or "").lower()
            owner_str = (owner_name or "").lower()
            surveyor_str = (surveyor_name or "").lower()
            lga_str = (lga or "").lower()
            state_str = (state or "").lower()
            
            # Search based on selected field
            if search_field == "All Fields":
                if (search_text in id_str or
                    search_text in plan_str or
                    search_text in owner_str or
                    search_text in surveyor_str or
                    search_text in lga_str or
                    search_text in state_str):
                    filtered.append(survey)
            elif search_field == "Plan Number":
                if search_text in plan_str:
                    filtered.append(survey)
            elif search_field == "Owner Name":
                if search_text in owner_str:
                    filtered.append(survey)
            elif search_field == "Surveyor Name":
                if search_text in surveyor_str:
                    filtered.append(survey)
            elif search_field == "LGA":
                if search_text in lga_str:
                    filtered.append(survey)
            elif search_field == "State":
                if search_text in state_str:
                    filtered.append(survey)
            elif search_field == "Survey ID":
                if search_text in id_str:
                    filtered.append(survey)
        
        self.display_survey_table(filtered)
        self.survey_count_label.setText(f"📊 Found {len(filtered)} matching surveys")

    def load_survey_from_table(self, item):
        """Load survey when double-clicking a row"""
        row = item.row()
        self.load_survey_from_row(row)

    def load_selected_survey_from_table(self):
        """Load the currently selected survey from the table"""
        current_row = self.survey_table.currentRow()
        if current_row >= 0:
            self.load_survey_from_row(current_row)
        else:
            QMessageBox.warning(self, "No Selection", "Please select a survey to load")

    def load_survey_from_row(self, row):
        """Load survey from the specified row"""
        survey_id_item = self.survey_table.item(row, 0)
        if survey_id_item:
            survey_id = int(survey_id_item.text())
            self.load_survey_by_id(survey_id)

    def load_survey_by_id(self, survey_id):
        """Load survey by ID into form"""
        if not self.db_available:
            return
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("""
                SELECT survey_id, plan_number, owner_name, survey_date, 
                       original_crs, surveyor_name, local_government, state, notes
                FROM surveys 
                WHERE survey_id = %s
            """, (survey_id,))
            
            result = cur.fetchone()
            cur.close()
            
            if not result:
                QMessageBox.warning(self, "Not Found", f"Survey ID {survey_id} not found")
                return
            
            sid, plan_number, owner_name, survey_date, original_crs, surveyor_name, lga, state, notes = result
            
            self.survey_id_display.setText(str(sid))
            self.plan_number.setText(plan_number or "")
            self.owner_name.setText(owner_name or "")
            
            if survey_date:
                self.survey_date.setDate(survey_date)
            
            if original_crs:
                found = False
                for i in range(self.crs_combo.count()):
                    if original_crs in self.crs_combo.itemText(i):
                        self.crs_combo.setCurrentIndex(i)
                        found = True
                        break
                if not found:
                    if original_crs.startswith("EPSG:"):
                        epsg_code = original_crs.replace("EPSG:", "")
                        self.crs_combo.setCurrentText("Custom EPSG (specify below)")
                        self.custom_crs.setText(epsg_code)
                    else:
                        self.crs_combo.setCurrentText("Custom EPSG (specify below)")
                        self.custom_crs.setText(original_crs)
            
            self.surveyor.setText(surveyor_name or "")
            self.lga.setText(lga or "")
            
            if state:
                index = self.state.findText(state)
                if index >= 0:
                    self.state.setCurrentIndex(index)
            
            self.notes.setPlainText(notes or "")
            
            self.current_survey_id = sid
            self.current_survey_label.setText(
                f"📋 Current Survey: {plan_number} (ID: {sid})"
            )
            
            self.update_survey_btn.setEnabled(True)
            self.save_survey_btn.setEnabled(True)
            self.refresh_documents_list()
            
            self.show_status(f"Loaded survey: {plan_number}")
            
            # Switch to form tab
            self.tab_widget.setCurrentIndex(0)
            
        except Exception as e:
            print(f"Error loading survey: {e}")
            self.db_connection.rollback()
            QMessageBox.critical(self, "Database Error", str(e))

    # ========== GLOBAL SEARCH TAB ==========
    def setup_global_search_tab(self):
        """Tab for searching across ALL database tables"""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        # Header
        header = QLabel("🔍 GLOBAL DATABASE SEARCH")
        header.setStyleSheet("font-size: 14pt; font-weight: bold; color: #9b59b6;")
        layout.addWidget(header)
        
        # Description
        desc = QLabel(
            "Search across ALL tables in the database:\n"
            "• Survey metadata (plan numbers, owners, surveyors)\n"
            "• Point descriptions and coordinates\n"
            "• Boundary information\n"
            "• Document names and descriptions\n"
            "• Traverse leg descriptions"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("background-color: #ecf0f1; padding: 10px; border-radius: 5px;")
        layout.addWidget(desc)
        
        # Search input
        search_layout = QHBoxLayout()
        self.global_search_input = QLineEdit()
        self.global_search_input.setPlaceholderText("Enter search term (e.g., 'Boundary', 'Beacon', 'Okonkwo')...")
        self.global_search_input.setMinimumHeight(35)
        self.global_search_input.returnPressed.connect(self.perform_global_search)
        search_layout.addWidget(self.global_search_input, 3)
        
        search_btn = QPushButton("🔍 Search All Tables")
        search_btn.setMinimumWidth(150)
        search_btn.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; padding: 8px;")
        search_btn.clicked.connect(self.perform_global_search)
        search_layout.addWidget(search_btn, 1)
        
        layout.addLayout(search_layout)
        
        # Search options
        options_group = QGroupBox("Search Options")
        options_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        options_layout = QHBoxLayout()
        
        self.search_surveys_cb = QCheckBox("Surveys")
        self.search_surveys_cb.setChecked(True)
        options_layout.addWidget(self.search_surveys_cb)
        
        self.search_points_cb = QCheckBox("Points")
        self.search_points_cb.setChecked(True)
        options_layout.addWidget(self.search_points_cb)
        
        self.search_boundaries_cb = QCheckBox("Boundaries")
        self.search_boundaries_cb.setChecked(True)
        options_layout.addWidget(self.search_boundaries_cb)
        
        self.search_documents_cb = QCheckBox("Documents")
        self.search_documents_cb.setChecked(True)
        options_layout.addWidget(self.search_documents_cb)
        
        self.search_traverses_cb = QCheckBox("Traverses")
        self.search_traverses_cb.setChecked(True)
        options_layout.addWidget(self.search_traverses_cb)
        
        options_layout.addStretch()
        options_group.setLayout(options_layout)
        layout.addWidget(options_group)
        
        # Match type
        match_group = QGroupBox("Match Type")
        match_layout = QHBoxLayout()
        
        self.match_exact = QRadioButton("Exact match")
        self.match_exact.setChecked(False)
        match_layout.addWidget(self.match_exact)
        
        self.match_contains = QRadioButton("Contains")
        self.match_contains.setChecked(True)
        match_layout.addWidget(self.match_contains)
        
        self.match_start = QRadioButton("Starts with")
        match_layout.addWidget(self.match_start)
        
        self.match_end = QRadioButton("Ends with")
        match_layout.addWidget(self.match_end)
        
        match_layout.addStretch()
        match_group.setLayout(match_layout)
        layout.addWidget(match_group)
        
        # Results tabs
        self.global_results_tabs = QTabWidget()
        
        # Tab 1: Combined Results
        combined_tab = QWidget()
        combined_layout = QVBoxLayout()
        
        self.combined_results_table = QTableWidget()
        self.combined_results_table.setColumnCount(6)
        self.combined_results_table.setHorizontalHeaderLabels([
            "Table", "ID", "Field", "Value", "Linked Survey", "Actions"
        ])
        self.combined_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.combined_results_table.setAlternatingRowColors(True)
        self.combined_results_table.setSelectionBehavior(QTableWidget.SelectRows)
        combined_layout.addWidget(self.combined_results_table)
        
        combined_tab.setLayout(combined_layout)
        self.global_results_tabs.addTab(combined_tab, "📊 All Results")
        
        # Tab 2: Surveys Only
        surveys_tab = QWidget()
        surveys_layout = QVBoxLayout()
        
        self.surveys_results_table = QTableWidget()
        self.surveys_results_table.setColumnCount(8)
        self.surveys_results_table.setHorizontalHeaderLabels([
            "ID", "Plan #", "Owner", "Surveyor", "LGA", "State", "Date", "Actions"
        ])
        self.surveys_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        surveys_layout.addWidget(self.surveys_results_table)
        
        surveys_tab.setLayout(surveys_layout)
        self.global_results_tabs.addTab(surveys_tab, "📋 Surveys")
        
        # Tab 3: Points Only
        points_tab = QWidget()
        points_layout = QVBoxLayout()
        
        self.points_results_table = QTableWidget()
        self.points_results_table.setColumnCount(7)
        self.points_results_table.setHorizontalHeaderLabels([
            "Point ID", "Survey ID", "Point #", "Description", "Coordinates", "Raw CRS", "Actions"
        ])
        self.points_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        points_layout.addWidget(self.points_results_table)
        
        points_tab.setLayout(points_layout)
        self.global_results_tabs.addTab(points_tab, "📍 Points")
        
        # Tab 4: Documents Only
        docs_tab = QWidget()
        docs_layout = QVBoxLayout()
        
        self.docs_results_table = QTableWidget()
        self.docs_results_table.setColumnCount(6)
        self.docs_results_table.setHorizontalHeaderLabels([
            "Doc ID", "Survey ID", "File Name", "Description", "Size (KB)", "Actions"
        ])
        self.docs_results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        docs_layout.addWidget(self.docs_results_table)
        
        docs_tab.setLayout(docs_layout)
        self.global_results_tabs.addTab(docs_tab, "📄 Documents")
        
        layout.addWidget(self.global_results_tabs)
        
        # Status bar
        self.global_search_status = QLabel("Ready to search")
        self.global_search_status.setStyleSheet("color: #7f8c8d; font-style: italic;")
        layout.addWidget(self.global_search_status)
        
        tab.setLayout(layout)
        self.tab_widget.addTab(tab, "🔍 Global Search")

    # ========== GLOBAL SEARCH METHODS ==========
    def perform_global_search(self):
        """Search across ALL database tables"""
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return
        
        search_term = self.global_search_input.text().strip()
        
        if not search_term:
            QMessageBox.warning(self, "Empty Search", "Please enter a search term")
            return
        
        self.global_search_status.setText(f"🔍 Searching for '{search_term}'...")
        QApplication.processEvents()
        
        try:
            # Build search pattern based on match type
            if self.match_exact.isChecked():
                pattern = search_term
            elif self.match_contains.isChecked():
                pattern = f"%{search_term}%"
            elif self.match_start.isChecked():
                pattern = f"{search_term}%"
            elif self.match_end.isChecked():
                pattern = f"%{search_term}"
            else:
                pattern = f"%{search_term}%"
            
            # Use autocommit to avoid transaction issues
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            all_results = []
            
            # ===== SEARCH SURVEYS TABLE =====
            if self.search_surveys_cb.isChecked():
                survey_query = """
                    SELECT 'surveys' as table_name, survey_id, 
                           'plan_number' as field, plan_number as value,
                           survey_id as linked_id
                    FROM surveys WHERE plan_number ILIKE %s
                    UNION ALL
                    SELECT 'surveys', survey_id, 'owner_name', owner_name, survey_id
                    FROM surveys WHERE owner_name ILIKE %s
                    UNION ALL
                    SELECT 'surveys', survey_id, 'surveyor_name', surveyor_name, survey_id
                    FROM surveys WHERE surveyor_name ILIKE %s
                    UNION ALL
                    SELECT 'surveys', survey_id, 'local_government', local_government, survey_id
                    FROM surveys WHERE local_government ILIKE %s
                    UNION ALL
                    SELECT 'surveys', survey_id, 'state', state, survey_id
                    FROM surveys WHERE state ILIKE %s
                    UNION ALL
                    SELECT 'surveys', survey_id, 'notes', notes, survey_id
                    FROM surveys WHERE notes ILIKE %s
                """
                cur.execute(survey_query, [pattern] * 6)
                survey_results = cur.fetchall()
                all_results.extend(survey_results)
                
                # Also get full survey records for dedicated tab
                cur.execute("""
                    SELECT survey_id, plan_number, owner_name, surveyor_name, 
                           local_government, state, survey_date
                    FROM surveys 
                    WHERE plan_number ILIKE %s 
                       OR owner_name ILIKE %s 
                       OR surveyor_name ILIKE %s
                       OR local_government ILIKE %s
                       OR state ILIKE %s
                       OR notes ILIKE %s
                    ORDER BY survey_date DESC
                    LIMIT 200
                """, [pattern] * 6)
                self.global_survey_results = cur.fetchall()
            else:
                self.global_survey_results = []
            
            # ===== SEARCH POINTS TABLE =====
            if self.search_points_cb.isChecked():
                points_query = """
                    SELECT 'points' as table_name, point_id, 
                           'description' as field, description as value,
                           survey_id as linked_id
                    FROM survey_points WHERE description ILIKE %s
                    UNION ALL
                    SELECT 'points', point_id, 'notes', notes, survey_id
                    FROM survey_points WHERE notes ILIKE %s
                    UNION ALL
                    SELECT 'points', point_id, 'raw_coordinates', raw_coordinates, survey_id
                    FROM survey_points WHERE raw_coordinates ILIKE %s
                """
                cur.execute(points_query, [pattern] * 3)
                point_results = cur.fetchall()
                all_results.extend(point_results)
                
                # Get full point records for dedicated tab
                cur.execute("""
                    SELECT point_id, survey_id, point_number, description, 
                           ST_AsText(geometry) as wkt, raw_crs
                    FROM survey_points 
                    WHERE description ILIKE %s 
                       OR notes ILIKE %s
                       OR raw_coordinates ILIKE %s
                    ORDER BY point_id DESC
                    LIMIT 200
                """, [pattern] * 3)
                self.global_point_results = cur.fetchall()
            else:
                self.global_point_results = []
            
            # ===== SEARCH BOUNDARIES TABLE =====
            if self.search_boundaries_cb.isChecked():
                boundaries_query = """
                    SELECT 'boundaries' as table_name, boundary_id, 
                           'verified' as field, CAST(verified AS TEXT) as value,
                           survey_id as linked_id
                    FROM survey_boundaries 
                    WHERE CAST(verified AS TEXT) ILIKE %s
                """
                cur.execute(boundaries_query, [pattern])
                boundary_results = cur.fetchall()
                all_results.extend(boundary_results)
            
            # ===== SEARCH DOCUMENTS TABLE =====
            if self.search_documents_cb.isChecked():
                docs_query = """
                    SELECT 'documents' as table_name, document_id, 
                           'file_name' as field, file_name as value,
                           survey_id as linked_id
                    FROM survey_documents WHERE file_name ILIKE %s
                    UNION ALL
                    SELECT 'documents', document_id, 'description', description, survey_id
                    FROM survey_documents WHERE description ILIKE %s
                """
                cur.execute(docs_query, [pattern] * 2)
                doc_results = cur.fetchall()
                all_results.extend(doc_results)
                
                # Get full document records for dedicated tab
                cur.execute("""
                    SELECT document_id, survey_id, file_name, description, 
                           file_size, is_primary
                    FROM survey_documents 
                    WHERE file_name ILIKE %s 
                       OR description ILIKE %s
                    ORDER BY uploaded_at DESC
                    LIMIT 200
                """, [pattern] * 2)
                self.global_doc_results = cur.fetchall()
            else:
                self.global_doc_results = []
            
            # ===== SEARCH TRAVERSES TABLE =====
            if self.search_traverses_cb.isChecked():
                traverses_query = """
                    SELECT 'traverses' as table_name, traverse_id, 
                           'traverse_name' as field, traverse_name as value,
                           survey_id as linked_id
                    FROM survey_traverses WHERE traverse_name ILIKE %s
                """
                cur.execute(traverses_query, [pattern])
                traverse_results = cur.fetchall()
                all_results.extend(traverse_results)
            
            cur.close()
            
            # Display results in all tabs
            self.display_global_results(all_results)
            self.display_survey_results()
            self.display_point_results()
            self.display_document_results()
            
            total = len(all_results)
            self.global_search_status.setText(f"✅ Found {total} matches across all tables")
            
        except Exception as e:
            self.global_search_status.setText(f"❌ Search failed: {str(e)}")
            print(f"Search error: {e}")
            self.db_connection.rollback()

    def display_global_results(self, results):
        """Display combined search results"""
        self.combined_results_table.setRowCount(len(results))
        
        for row, result in enumerate(results):
            table_name, item_id, field, value, linked_id = result
            
            # Table name with icon
            table_icon = {
                'surveys': '📋',
                'points': '📍',
                'boundaries': '🗺️',
                'documents': '📄',
                'traverses': '📐'
            }.get(table_name, '📁')
            
            self.combined_results_table.setItem(row, 0, QTableWidgetItem(f"{table_icon} {table_name}"))
            self.combined_results_table.setItem(row, 1, QTableWidgetItem(str(item_id)))
            self.combined_results_table.setItem(row, 2, QTableWidgetItem(field))
            self.combined_results_table.setItem(row, 3, QTableWidgetItem(str(value)[:100]))
            self.combined_results_table.setItem(row, 4, QTableWidgetItem(str(linked_id) if linked_id else ""))
            
            # Actions
            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(2, 2, 2, 2)
            
            if table_name == 'surveys':
                view_btn = QPushButton("👁️ View")
                view_btn.setMaximumWidth(60)
                view_btn.setStyleSheet("background-color: #27ae60; color: white;")
                view_btn.clicked.connect(lambda checked, sid=linked_id: self.load_survey_by_id(sid))
                action_layout.addWidget(view_btn)
            elif table_name == 'points':
                view_btn = QPushButton("📍 Show")
                view_btn.setMaximumWidth(60)
                view_btn.setStyleSheet("background-color: #3498db; color: white;")
                view_btn.clicked.connect(lambda checked, pid=item_id: self.show_point_on_map(pid))
                action_layout.addWidget(view_btn)
                
                survey_btn = QPushButton("📋 Survey")
                survey_btn.setMaximumWidth(60)
                survey_btn.setStyleSheet("background-color: #f39c12; color: white;")
                survey_btn.clicked.connect(lambda checked, sid=linked_id: self.load_survey_by_id(sid))
                action_layout.addWidget(survey_btn)
            elif table_name == 'documents':
                view_btn = QPushButton("📄 Open")
                view_btn.setMaximumWidth(60)
                view_btn.setStyleSheet("background-color: #e67e22; color: white;")
                view_btn.clicked.connect(lambda checked, did=item_id: self.open_document_by_id(did))
                action_layout.addWidget(view_btn)
            
            action_widget.setLayout(action_layout)
            self.combined_results_table.setCellWidget(row, 5, action_widget)

    def display_survey_results(self):
        """Display survey search results in dedicated tab"""
        self.surveys_results_table.setRowCount(len(self.global_survey_results))
        
        for row, result in enumerate(self.global_survey_results):
            survey_id, plan_number, owner_name, surveyor_name, lga, state, survey_date = result
            
            self.surveys_results_table.setItem(row, 0, QTableWidgetItem(str(survey_id)))
            self.surveys_results_table.setItem(row, 1, QTableWidgetItem(plan_number or ""))
            self.surveys_results_table.setItem(row, 2, QTableWidgetItem(owner_name or ""))
            self.surveys_results_table.setItem(row, 3, QTableWidgetItem(surveyor_name or ""))
            self.surveys_results_table.setItem(row, 4, QTableWidgetItem(lga or ""))
            self.surveys_results_table.setItem(row, 5, QTableWidgetItem(state or ""))
            
            date_str = survey_date.strftime("%Y-%m-%d") if survey_date else ""
            self.surveys_results_table.setItem(row, 6, QTableWidgetItem(date_str))
            
            # Actions
            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(2, 2, 2, 2)
            
            view_btn = QPushButton("👁️ Load")
            view_btn.setMaximumWidth(60)
            view_btn.setStyleSheet("background-color: #27ae60; color: white;")
            view_btn.clicked.connect(lambda checked, sid=survey_id: self.load_survey_by_id(sid))
            action_layout.addWidget(view_btn)
            
            docs_btn = QPushButton("📄 Docs")
            docs_btn.setMaximumWidth(60)
            docs_btn.setStyleSheet("background-color: #3498db; color: white;")
            docs_btn.clicked.connect(lambda checked, sid=survey_id: self.show_survey_docs(sid))
            action_layout.addWidget(docs_btn)
            
            action_widget.setLayout(action_layout)
            self.surveys_results_table.setCellWidget(row, 7, action_widget)

    def display_point_results(self):
        """Display point search results in dedicated tab"""
        self.points_results_table.setRowCount(len(self.global_point_results))
        
        for row, result in enumerate(self.global_point_results):
            point_id, survey_id, point_number, description, wkt, raw_crs = result
            
            self.points_results_table.setItem(row, 0, QTableWidgetItem(str(point_id)))
            self.points_results_table.setItem(row, 1, QTableWidgetItem(str(survey_id)))
            self.points_results_table.setItem(row, 2, QTableWidgetItem(str(point_number) if point_number else ""))
            self.points_results_table.setItem(row, 3, QTableWidgetItem(description or ""))
            self.points_results_table.setItem(row, 4, QTableWidgetItem(wkt[:50] + "..." if wkt else ""))
            self.points_results_table.setItem(row, 5, QTableWidgetItem(raw_crs or ""))
            
            # Actions
            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(2, 2, 2, 2)
            
            show_btn = QPushButton("📍 Show")
            show_btn.setMaximumWidth(60)
            show_btn.setStyleSheet("background-color: #27ae60; color: white;")
            show_btn.clicked.connect(lambda checked, pid=point_id: self.show_point_on_map(pid))
            action_layout.addWidget(show_btn)
            
            survey_btn = QPushButton("📋 Survey")
            survey_btn.setMaximumWidth(60)
            survey_btn.setStyleSheet("background-color: #3498db; color: white;")
            survey_btn.clicked.connect(lambda checked, sid=survey_id: self.load_survey_by_id(sid))
            action_layout.addWidget(survey_btn)
            
            action_widget.setLayout(action_layout)
            self.points_results_table.setCellWidget(row, 6, action_widget)

    def display_document_results(self):
        """Display document search results in dedicated tab"""
        self.docs_results_table.setRowCount(len(self.global_doc_results))
        
        for row, result in enumerate(self.global_doc_results):
            doc_id, survey_id, file_name, description, file_size, is_primary = result
            
            self.docs_results_table.setItem(row, 0, QTableWidgetItem(str(doc_id)))
            self.docs_results_table.setItem(row, 1, QTableWidgetItem(str(survey_id)))
            self.docs_results_table.setItem(row, 2, QTableWidgetItem(file_name or ""))
            self.docs_results_table.setItem(row, 3, QTableWidgetItem(description or ""))
            
            size_kb = file_size / 1024 if file_size else 0
            self.docs_results_table.setItem(row, 4, QTableWidgetItem(f"{size_kb:.1f}"))
            
            # Actions
            action_widget = QWidget()
            action_layout = QHBoxLayout()
            action_layout.setContentsMargins(2, 2, 2, 2)
            
            open_btn = QPushButton("📄 Open")
            open_btn.setMaximumWidth(60)
            open_btn.setStyleSheet("background-color: #27ae60; color: white;")
            open_btn.clicked.connect(lambda checked, did=doc_id: self.open_document_by_id(did))
            action_layout.addWidget(open_btn)
            
            survey_btn = QPushButton("📋 Survey")
            survey_btn.setMaximumWidth(60)
            survey_btn.setStyleSheet("background-color: #3498db; color: white;")
            survey_btn.clicked.connect(lambda checked, sid=survey_id: self.load_survey_by_id(sid))
            action_layout.addWidget(survey_btn)
            
            action_widget.setLayout(action_layout)
            self.docs_results_table.setCellWidget(row, 5, action_widget)

    def show_point_on_map(self, point_id):
        """Show a specific point on the QGIS map"""
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("""
                SELECT ST_AsText(geometry), survey_id, description 
                FROM survey_points WHERE point_id = %s
            """, (point_id,))
            result = cur.fetchone()
            cur.close()
            
            if result and result[0]:
                wkt, survey_id, description = result
                
                # Create temporary layer
                from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsProject
                
                layer = QgsVectorLayer("Point?crs=EPSG:26332", f"Point {point_id}", "memory")
                provider = layer.dataProvider()
                
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromWkt(wkt))
                provider.addFeature(feat)
                
                layer.updateExtents()
                QgsProject.instance().addMapLayer(layer)
                
                # Zoom to point
                if self.iface:
                    self.iface.setActiveLayer(layer)
                    self.iface.zoomToActiveLayer()
                
                QMessageBox.information(
                    self, "Point Found",
                    f"📍 Point {point_id} added to map\n"
                    f"Description: {description}\n"
                    f"Survey ID: {survey_id}"
                )
        except Exception as e:
            print(f"Error showing point: {e}")
            QMessageBox.critical(self, "Error", f"Could not show point: {str(e)}")

    def open_document_by_id(self, document_id):
        """Open a document by its ID"""
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("SELECT file_path, file_name FROM survey_documents WHERE document_id = %s", (document_id,))
            result = cur.fetchone()
            cur.close()
            
            if result:
                file_path, file_name = result
                if os.path.exists(file_path):
                    self.open_file(file_path)
                else:
                    QMessageBox.warning(
                        self, "File Not Found",
                        f"Cannot find file:\n{file_path}\n\n"
                        f"Document: {file_name}\n"
                        f"ID: {document_id}"
                    )
        except Exception as e:
            print(f"Error opening document: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def show_survey_docs(self, survey_id):
        """Show documents for a survey"""
        self.load_survey_by_id(survey_id)
        if self._documents_tab_widget is not None:
            idx = self.tab_widget.indexOf(self._documents_tab_widget)
            if idx >= 0:
                self.tab_widget.setCurrentIndex(idx)

    # ========== DOCUMENTS TAB ==========
    def setup_documents_tab(self):
        """Tab for document management"""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        instructions = QLabel(
            "📄 DOCUMENT MANAGEMENT\n"
            "Upload and manage multiple documents for each survey. "
            "Documents are verified with checksums for integrity."
        )
        instructions.setStyleSheet("background-color: #ecf0f1; padding: 8px; font-weight: bold;")
        layout.addWidget(instructions)
        
        self.doc_survey_label = QLabel("Current Survey: None")
        self.doc_survey_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        layout.addWidget(self.doc_survey_label)
        
        # Upload section
        upload_group = QGroupBox("Upload New Document")
        upload_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        upload_layout = QFormLayout()
        
        file_layout = QHBoxLayout()
        self.doc_file_path = QLineEdit()
        self.doc_file_path.setPlaceholderText("Select a file to upload...")
        file_layout.addWidget(self.doc_file_path)
        
        browse_doc_btn = QPushButton("📁 Browse")
        browse_doc_btn.setMaximumWidth(100)
        browse_doc_btn.clicked.connect(self.browse_document_file)
        file_layout.addWidget(browse_doc_btn)
        upload_layout.addRow("File:", file_layout)
        
        self.doc_description = QLineEdit()
        self.doc_description.setPlaceholderText("Document description")
        upload_layout.addRow("Description:", self.doc_description)
        
        self.doc_is_primary = QCheckBox("Set as primary document")
        self.doc_is_primary.setChecked(True)
        upload_layout.addRow("", self.doc_is_primary)
        
        upload_btn = QPushButton("📤 Upload Document")
        upload_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        upload_btn.clicked.connect(self.upload_document)
        upload_layout.addRow("", upload_btn)
        
        upload_group.setLayout(upload_layout)
        layout.addWidget(upload_group)
        
        # Documents list
        list_group = QGroupBox("Survey Documents")
        list_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        list_layout = QVBoxLayout()
        
        self.documents_table = QTableWidget()
        self.documents_table.setColumnCount(8)
        self.documents_table.setHorizontalHeaderLabels([
            "ID", "File Name", "Description", "Size (KB)", "Primary", "Verified", "Uploaded", "Actions"
        ])
        self.documents_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.documents_table.setAlternatingRowColors(True)
        list_layout.addWidget(self.documents_table)
        
        list_group.setLayout(list_layout)
        layout.addWidget(list_group)
        
        # Action buttons
        action_group = QGroupBox("Document Actions")
        action_layout = QHBoxLayout()
        
        refresh_docs_btn = QPushButton("🔄 Refresh List")
        refresh_docs_btn.clicked.connect(self.refresh_documents_list)
        action_layout.addWidget(refresh_docs_btn)
        
        verify_all_btn = QPushButton("✅ Verify All")
        verify_all_btn.clicked.connect(self.verify_all_documents)
        action_layout.addWidget(verify_all_btn)
        
        open_selected_btn = QPushButton("📂 Open Selected")
        open_selected_btn.setStyleSheet("background-color: #3498db; color: white;")
        open_selected_btn.clicked.connect(self.open_selected_document)
        action_layout.addWidget(open_selected_btn)
        
        action_group.setLayout(action_layout)
        layout.addWidget(action_group)
        
        tab.setLayout(layout)
        self.tab_widget.addTab(tab, "📄 Documents")
        self._documents_tab_widget = tab

    # ========== DOCUMENT METHODS ==========
    def browse_document_file(self):
        """Browse for document file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Document File",
            self.pdf_base_path,
            "All Files (*.*);;PDF Files (*.pdf);;Image Files (*.png *.jpg *.jpeg)"
        )
        
        if file_path:
            self.doc_file_path.setText(file_path)
            self.pdf_base_path = os.path.dirname(file_path)
            if not self.doc_description.text():
                self.doc_description.setText(os.path.basename(file_path))

    def calculate_file_checksum(self, file_path, algorithm='MD5'):
        """Calculate checksum of a file"""
        hash_func = hashlib.md5(usedforsecurity=False) if algorithm == 'MD5' else hashlib.sha256()
        
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b''):
                    hash_func.update(chunk)
            return hash_func.hexdigest()
        except Exception as e:
            print(f"Error calculating checksum: {e}")
            return None

    def upload_document(self):
        """Upload a document to the database"""
        if not self.require_role('surveyor'):
            return
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return

        if not self.current_survey_id:
            QMessageBox.warning(self, "No Survey", "Please select or create a survey first")
            self.tab_widget.setCurrentIndex(0)
            return

        file_path = self.doc_file_path.text().strip()
        if not file_path:
            QMessageBox.warning(self, "No File", "Please select a file to upload")
            return
        
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "File Not Found", f"Cannot find file:\n{file_path}")
            return
        
        try:
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            description = self.doc_description.text().strip() or file_name
            
            progress = QProgressDialog("Calculating file checksum...", "Cancel", 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            
            checksum = self.calculate_file_checksum(file_path)
            progress.close()
            
            if not checksum:
                QMessageBox.critical(self, "Error", "Failed to calculate file checksum")
                return
            
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            
            if self.doc_is_primary.isChecked():
                cur.execute("""
                    UPDATE survey_documents 
                    SET is_primary = FALSE 
                    WHERE survey_id = %s
                """, (self.current_survey_id,))
            
            cur.execute("""
                INSERT INTO survey_documents 
                (survey_id, file_path, file_name, file_size, checksum, 
                 checksum_algorithm, is_primary, description, last_verified)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING document_id
            """, (
                self.current_survey_id,
                file_path,
                file_name,
                file_size,
                checksum,
                'MD5',
                self.doc_is_primary.isChecked(),
                description,
                date.today()
            ))
            
            document_id = cur.fetchone()[0]
            self.write_audit("DOC_UPLOAD", table_name="survey_documents",
                             record_id=document_id,
                             new_values=f"survey={self.current_survey_id} file={file_name}")
            cur.close()

            QMessageBox.information(self, "Success", f"✅ Document uploaded successfully!")

            self.doc_file_path.clear()
            self.doc_description.clear()
            self.doc_is_primary.setChecked(True)
            self.refresh_documents_list()
            
        except Exception as e:
            print(f"Error uploading document: {e}")
            QMessageBox.critical(self, "Database Error", str(e))

    def refresh_documents_list(self):
        """Refresh the documents list"""
        if not self.db_available or not self.current_survey_id:
            self.documents_table.setRowCount(0)
            self.doc_survey_label.setText("Current Survey: None")
            return
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("""
                SELECT document_id, file_name, description, file_size, 
                       is_primary, last_verified, uploaded_at, file_path
                FROM survey_documents 
                WHERE survey_id = %s
                ORDER BY is_primary DESC, uploaded_at DESC
            """, (self.current_survey_id,))
            
            documents = cur.fetchall()
            cur.close()
            
            self.doc_survey_label.setText(
                f"Current Survey ID: {self.current_survey_id} - {len(documents)} document(s)"
            )
            
            self.documents_table.setRowCount(len(documents))
            
            for row, doc in enumerate(documents):
                doc_id, file_name, description, file_size, is_primary, last_verified, uploaded_at, file_path = doc
                
                self.documents_table.setItem(row, 0, QTableWidgetItem(str(doc_id)))
                self.documents_table.setItem(row, 1, QTableWidgetItem(file_name or ""))
                self.documents_table.setItem(row, 2, QTableWidgetItem(description or ""))
                
                size_kb = file_size / 1024 if file_size else 0
                self.documents_table.setItem(row, 3, QTableWidgetItem(f"{size_kb:.1f}"))
                
                primary_item = QTableWidgetItem("✅" if is_primary else "")
                primary_item.setTextAlignment(Qt.AlignCenter)
                self.documents_table.setItem(row, 4, primary_item)
                
                verified_status = ""
                if last_verified:
                    if os.path.exists(file_path):
                        verified_status = f"✅ {last_verified}"
                    else:
                        verified_status = "❌ Missing"
                self.documents_table.setItem(row, 5, QTableWidgetItem(verified_status))
                
                uploaded_str = uploaded_at.strftime("%Y-%m-%d") if uploaded_at else ""
                self.documents_table.setItem(row, 6, QTableWidgetItem(uploaded_str))
                
                action_widget = QWidget()
                action_layout = QHBoxLayout()
                action_layout.setContentsMargins(2, 2, 2, 2)
                
                view_btn = QPushButton("👁️ View")
                view_btn.setMaximumWidth(60)
                view_btn.clicked.connect(lambda checked, r=row: self.view_document(r))
                action_layout.addWidget(view_btn)
                
                verify_btn = QPushButton("✓ Verify")
                verify_btn.setMaximumWidth(60)
                verify_btn.clicked.connect(lambda checked, r=row: self.verify_document(r))
                action_layout.addWidget(verify_btn)
                
                set_primary_btn = QPushButton("⭐ Primary")
                set_primary_btn.setMaximumWidth(70)
                set_primary_btn.clicked.connect(lambda checked, r=row: self.set_primary_document(r))
                action_layout.addWidget(set_primary_btn)
                
                action_widget.setLayout(action_layout)
                self.documents_table.setCellWidget(row, 7, action_widget)
                self.documents_table.item(row, 0).setData(Qt.UserRole, file_path)
            
        except Exception as e:
            print(f"Error refreshing documents: {e}")

    def view_document(self, row):
        """View a document"""
        doc_id_item = self.documents_table.item(row, 0)
        if not doc_id_item:
            return
        
        file_path = doc_id_item.data(Qt.UserRole)
        if file_path and os.path.exists(file_path):
            self.open_file(file_path)
        else:
            QMessageBox.warning(self, "File Not Found", f"Cannot find file:\n{file_path}")

    def verify_document(self, row):
        """Verify document integrity"""
        doc_id_item = self.documents_table.item(row, 0)
        if not doc_id_item:
            return
        
        doc_id = int(doc_id_item.text())
        file_path = doc_id_item.data(Qt.UserRole)
        
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "File Missing", f"File not found:\n{file_path}")
            return
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("SELECT checksum, checksum_algorithm FROM survey_documents WHERE document_id = %s", (doc_id,))
            result = cur.fetchone()
            cur.close()
            
            if not result:
                return
            
            stored_checksum, algorithm = result
            
            progress = QProgressDialog("Verifying file integrity...", "Cancel", 0, 0, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.show()
            
            current_checksum = self.calculate_file_checksum(file_path, algorithm)
            progress.close()
            
            if current_checksum == stored_checksum:
                cur = self.db_connection.cursor()
                cur.execute("UPDATE survey_documents SET last_verified = %s WHERE document_id = %s", (date.today(), doc_id))
                cur.close()
                QMessageBox.information(self, "Success", "✅ Document verified successfully!")
            else:
                QMessageBox.critical(self, "Verification Failed", "❌ Document has been modified!")
            
            self.refresh_documents_list()
            
        except Exception as e:
            print(f"Error verifying document: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def set_primary_document(self, row):
        """Set document as primary"""
        doc_id_item = self.documents_table.item(row, 0)
        if not doc_id_item:
            return
        
        doc_id = int(doc_id_item.text())
        
        reply = QMessageBox.question(self, "Set Primary", "Set this as primary document?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.No:
            return
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("UPDATE survey_documents SET is_primary = FALSE WHERE survey_id = %s", (self.current_survey_id,))
            cur.execute("UPDATE survey_documents SET is_primary = TRUE WHERE document_id = %s", (doc_id,))
            cur.close()
            QMessageBox.information(self, "Success", "Primary document updated")
            self.refresh_documents_list()
        except Exception as e:
            print(f"Error setting primary document: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def verify_all_documents(self):
        """Verify all documents"""
        if not self.current_survey_id:
            return
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("SELECT document_id, file_path, checksum, checksum_algorithm FROM survey_documents WHERE survey_id = %s", (self.current_survey_id,))
            documents = cur.fetchall()
            cur.close()
            
            if not documents:
                QMessageBox.information(self, "No Documents", "No documents to verify")
                return
            
            progress = QProgressDialog("Verifying documents...", "Cancel", 0, len(documents), self)
            progress.setWindowModality(Qt.WindowModal)
            
            verified = 0
            failed = 0
            missing = 0
            
            for i, (doc_id, file_path, stored_checksum, algorithm) in enumerate(documents):
                progress.setValue(i)
                progress.setLabelText(f"Verifying {os.path.basename(file_path)}...")
                
                if progress.wasCanceled():
                    break
                
                if not os.path.exists(file_path):
                    missing += 1
                    continue
                
                current_checksum = self.calculate_file_checksum(file_path, algorithm)
                if current_checksum == stored_checksum:
                    verified += 1
                    cur = self.db_connection.cursor()
                    cur.execute("UPDATE survey_documents SET last_verified = %s WHERE document_id = %s", (date.today(), doc_id))
                    cur.close()
                else:
                    failed += 1
            
            progress.close()
            
            QMessageBox.information(self, "Verification Complete", 
                f"📊 Results:\n✅ Verified: {verified}\n❌ Failed: {failed}\n⚠️ Missing: {missing}")
            
            self.refresh_documents_list()
            
        except Exception as e:
            print(f"Error verifying all documents: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def open_selected_document(self):
        """Open selected document"""
        current_row = self.documents_table.currentRow()
        if current_row >= 0:
            self.view_document(current_row)

    def open_file(self, file_path):
        """Open file with default viewer"""
        try:
            if sys.platform == 'win32':
                os.startfile(file_path)
            elif sys.platform == 'darwin':
                subprocess.run(['open', file_path])
            else:
                subprocess.run(['xdg-open', file_path])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open file:\n{str(e)}")

    # ========== COORDINATE INPUT TAB ==========
    def setup_coordinate_tab(self):
        """Tab for direct coordinate input"""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        instructions = QLabel(
            "📍 ENTER POINT COORDINATES DIRECTLY\n"
            "Add points one by one. All coordinates will be stored with the selected CRS."
        )
        instructions.setStyleSheet("background-color: #ecf0f1; padding: 8px; font-weight: bold;")
        layout.addWidget(instructions)
        
        table_group = QGroupBox("Point Coordinates")
        table_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        table_layout = QVBoxLayout()
        
        self.coord_table = QTableWidget()
        self.coord_table.setColumnCount(5)
        self.coord_table.setHorizontalHeaderLabels(["Point #", "Easting/X", "Northing/Y", "Description", "Actions"])
        self.coord_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table_layout.addWidget(self.coord_table)
        
        input_layout = QHBoxLayout()
        self.coord_easting = QLineEdit()
        self.coord_easting.setPlaceholderText("Easting / X coordinate")
        input_layout.addWidget(self.coord_easting)
        
        self.coord_northing = QLineEdit()
        self.coord_northing.setPlaceholderText("Northing / Y coordinate")
        input_layout.addWidget(self.coord_northing)
        
        self.coord_desc = QLineEdit()
        self.coord_desc.setPlaceholderText("Description")
        input_layout.addWidget(self.coord_desc)
        
        add_coord_btn = QPushButton("➕ Add Point")
        add_coord_btn.setStyleSheet("background-color: #3498db; color: white;")
        add_coord_btn.clicked.connect(self.add_coordinate_point)
        input_layout.addWidget(add_coord_btn)
        
        table_layout.addLayout(input_layout)
        table_group.setLayout(table_layout)
        layout.addWidget(table_group)
        
        button_group = QGroupBox("Actions")
        button_layout = QHBoxLayout()
        
        import_dxf_btn = QPushButton("📐 Import from DXF/DWG")
        import_dxf_btn.setStyleSheet("background-color: #1565C0; color: white; font-weight: bold; padding: 8px;")
        import_dxf_btn.setToolTip("Import beacon coordinates directly from an AutoCAD DXF or DWG file")
        import_dxf_btn.clicked.connect(self.import_from_dxf)
        button_layout.addWidget(import_dxf_btn)

        plot_coord_btn = QPushButton("🗺️ Plot Points")
        plot_coord_btn.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; padding: 8px;")
        plot_coord_btn.clicked.connect(self.plot_coordinates)
        button_layout.addWidget(plot_coord_btn)

        save_coord_btn = QPushButton("💾 Save to PostGIS")
        save_coord_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        save_coord_btn.clicked.connect(self.save_coordinates_to_postgis)
        button_layout.addWidget(save_coord_btn)

        clear_coord_btn = QPushButton("🗑️ Clear All")
        clear_coord_btn.setStyleSheet("background-color: #e74c3c; color: white; padding: 8px;")
        clear_coord_btn.clicked.connect(self.clear_coordinate_table)
        button_layout.addWidget(clear_coord_btn)
        
        button_group.setLayout(button_layout)
        layout.addWidget(button_group)
        
        tab.setLayout(layout)
        self.tab_widget.addTab(tab, "📍 Coordinate Input")

    # ========== COORDINATE METHODS ==========
    def add_coordinate_point(self):
        """Add coordinate point"""
        if not self.coord_easting.text() or not self.coord_northing.text():
            QMessageBox.warning(self, "Input Error", "Please enter both coordinates")
            return
        
        try:
            easting = float(self.coord_easting.text())
            northing = float(self.coord_northing.text())
            
            row = self.coord_table.rowCount()
            self.coord_table.insertRow(row)
            
            self.coord_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.coord_table.setItem(row, 1, QTableWidgetItem(f"{easting:.3f}"))
            self.coord_table.setItem(row, 2, QTableWidgetItem(f"{northing:.3f}"))
            self.coord_table.setItem(row, 3, QTableWidgetItem(self.coord_desc.text()))
            
            delete_btn = QPushButton("❌")
            delete_btn.setMaximumWidth(30)
            delete_btn.clicked.connect(lambda checked, r=row: self.delete_coordinate_row(r))
            self.coord_table.setCellWidget(row, 4, delete_btn)
            
            self.coord_easting.clear()
            self.coord_northing.clear()
            self.coord_desc.clear()
            
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Please enter valid numbers")

    def delete_coordinate_row(self, row):
        """Delete coordinate row"""
        self.coord_table.removeRow(row)
        for i in range(self.coord_table.rowCount()):
            self.coord_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))

    def clear_coordinate_table(self):
        """Clear all coordinates"""
        self.coord_table.setRowCount(0)

    def plot_coordinates(self):
        """Plot coordinates on map - silent version with no popups"""
        if self.coord_table.rowCount() == 0:
            self.show_status("No coordinates to plot")
            return
        
        crs = self.get_current_crs()
        if not crs:
            return
        
        points = []
        point_data = []
        
        for i in range(self.coord_table.rowCount()):
            try:
                e = float(self.coord_table.item(i, 1).text())
                n = float(self.coord_table.item(i, 2).text())
                points.append(QgsPointXY(e, n))
                desc = self.coord_table.item(i, 3).text() if self.coord_table.item(i, 3) else ""
                point_data.append((e, n, desc))
            except:
                continue
        
        if len(points) < 1:
            self.show_status("No valid points to plot")
            return
        
        try:
            # Create point layer
            point_layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "Survey Points", "memory")
            provider = point_layer.dataProvider()
            provider.addAttributes([
                QgsField("point_id", QVariant.Int), 
                QgsField("easting", QVariant.Double),
                QgsField("northing", QVariant.Double),
                QgsField("description", QVariant.String)
            ])
            point_layer.updateFields()
            
            features = []
            for i, (e, n, desc) in enumerate(point_data):
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(e, n)))
                feat.setAttributes([i+1, e, n, desc])
                features.append(feat)
            
            provider.addFeatures(features)
            point_layer.updateExtents()
            
            # Style
            symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': '#FF0000', 'size': '4'})
            point_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            QgsProject.instance().addMapLayer(point_layer)
            
            # SAFE ZOOM - with error handling
            if self.iface and point_layer.featureCount() > 0:
                try:
                    extent = point_layer.extent()
                    if not extent.isNull():
                        extent.scale(1.1)
                        self.iface.mapCanvas().setExtent(extent)
                        self.iface.mapCanvas().refresh()
                    else:
                        self.iface.setActiveLayer(point_layer)
                except Exception as e:
                    pass  # zoom not available in this window state
                    try:
                        self.iface.setActiveLayer(point_layer)
                    except:
                        pass
            
            self.show_status(f"✅ Plotted {len(points)} points")
            
        except Exception as e:
            self.show_status(f"❌ Plot error: {str(e)[:50]}")

    # ========== TRAVERSE TAB ==========
    def setup_traverse_tab(self):
        """Tab for bearing/distance input with DMS support"""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        instructions = QLabel(
            "📐 NIGERIAN SURVEY TRAVERSE INPUT\n"
            "• Bearings can be entered as:\n"
            "  - Whole Circle: 45°30'15\" (0-360° from North)\n"
            "  - Quadrant: N45°30'E, S30°15'W, etc.\n"
            "• Distances in meters\n"
            "• Three input methods: Spin boxes, Text with shortcuts, or Decimal"
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("background-color: #ecf0f1; padding: 8px; font-weight: bold;")
        layout.addWidget(instructions)
        
        self.traverse_survey_label = QLabel("📋 Linked Survey: None")
        self.traverse_survey_label.setStyleSheet("font-weight: bold; color: #2980b9;")
        layout.addWidget(self.traverse_survey_label)
        
        # Starting point
        start_group = QGroupBox("1. STARTING POINT COORDINATES")
        start_group.setStyleSheet("QGroupBox { font-weight: bold; color: #27ae60; }")
        start_layout = QGridLayout()
        
        start_layout.addWidget(QLabel("Easting/X:"), 0, 0)
        self.start_easting = QLineEdit()
        self.start_easting.setPlaceholderText("e.g., 534200.45")
        start_layout.addWidget(self.start_easting, 0, 1)
        
        start_layout.addWidget(QLabel("Northing/Y:"), 0, 2)
        self.start_northing = QLineEdit()
        self.start_northing.setPlaceholderText("e.g., 872345.67")
        start_layout.addWidget(self.start_northing, 0, 3)
        
        start_group.setLayout(start_layout)
        layout.addWidget(start_group)
        
        # Traverse legs input
        legs_group = QGroupBox("2. ADD TRAVERSE LEGS")
        legs_group.setStyleSheet("QGroupBox { font-weight: bold; color: #27ae60; }")
        legs_layout = QVBoxLayout()
        
        # Bearing input (using custom widget)
        bearing_label = QLabel("Bearing:")
        bearing_label.setStyleSheet("font-weight: bold;")
        legs_layout.addWidget(bearing_label)
        self.bearing_input = BearingInputWidget()
        legs_layout.addWidget(self.bearing_input)
        
        # Distance input
        distance_layout = QHBoxLayout()
        distance_layout.addWidget(QLabel("Distance (m):"))
        self.distance_input = QLineEdit()
        self.distance_input.setPlaceholderText("e.g., 120.50")
        self.distance_input.setFixedWidth(150)
        distance_layout.addWidget(self.distance_input)
        distance_layout.addStretch()
        legs_layout.addLayout(distance_layout)
        
        # Description
        desc_layout = QHBoxLayout()
        desc_layout.addWidget(QLabel("Description:"))
        self.leg_desc = QLineEdit()
        self.leg_desc.setPlaceholderText("e.g., Boundary beacon, Corner post")
        desc_layout.addWidget(self.leg_desc)
        desc_layout.addStretch()
        legs_layout.addLayout(desc_layout)
        
        # Add button
        add_btn_layout = QHBoxLayout()
        self.add_leg_btn = QPushButton("➕ Add Leg to Traverse")
        self.add_leg_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px;")
        self.add_leg_btn.clicked.connect(self.add_traverse_leg)
        add_btn_layout.addWidget(self.add_leg_btn)
        add_btn_layout.addStretch()
        legs_layout.addLayout(add_btn_layout)
        
        legs_group.setLayout(legs_layout)
        layout.addWidget(legs_group)
        
        # Traverse legs table
        table_group = QGroupBox("3. TRAVERSE LEGS ENTERED")
        table_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        table_layout = QVBoxLayout()
        
        self.traverse_table = QTableWidget()
        self.traverse_table.setColumnCount(7)
        self.traverse_table.setHorizontalHeaderLabels([
            "Leg #", "Bearing (DMS)", "Bearing (°)", "Distance (m)", 
            "Easting", "Northing", "Actions"
        ])
        self.traverse_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.traverse_table.setAlternatingRowColors(True)
        table_layout.addWidget(self.traverse_table)
        
        table_group.setLayout(table_layout)
        layout.addWidget(table_group)
        
        # Action buttons
        button_group = QGroupBox("4. TRAVERSE ACTIONS")
        button_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        button_layout = QHBoxLayout()
        
        import_dxf_trav_btn = QPushButton("📐 Import from DXF/DWG")
        import_dxf_trav_btn.setStyleSheet("background-color: #1565C0; color: white; font-weight: bold; padding: 8px;")
        import_dxf_trav_btn.setToolTip("Extract bearing and distance legs from an AutoCAD DXF or DWG file")
        import_dxf_trav_btn.clicked.connect(self.import_from_dxf)
        button_layout.addWidget(import_dxf_trav_btn)

        self.calc_btn = QPushButton("🧮 Calculate All")
        self.calc_btn.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; padding: 8px;")
        self.calc_btn.clicked.connect(self.calculate_traverse)
        button_layout.addWidget(self.calc_btn)
        
        self.plot_traverse_btn = QPushButton("🗺️ Plot on Map")
        self.plot_traverse_btn.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; padding: 8px;")
        self.plot_traverse_btn.clicked.connect(self.plot_traverse)
        button_layout.addWidget(self.plot_traverse_btn)
        
        self.save_traverse_btn = QPushButton("💾 Save to Database")
        self.save_traverse_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        self.save_traverse_btn.clicked.connect(self.save_traverse_to_postgis)
        self.save_traverse_btn.setEnabled(self.db_available)
        button_layout.addWidget(self.save_traverse_btn)
        
        self.clear_traverse_btn = QPushButton("🗑️ Clear All")
        self.clear_traverse_btn.setStyleSheet("background-color: #e74c3c; color: white; padding: 8px;")
        self.clear_traverse_btn.clicked.connect(self.clear_traverse_data)
        button_layout.addWidget(self.clear_traverse_btn)
        
        button_group.setLayout(button_layout)
        layout.addWidget(button_group)
        
        # Calculated results
        result_group = QGroupBox("5. CALCULATION RESULTS")
        result_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        result_layout = QVBoxLayout()
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(120)
        self.result_text.setStyleSheet("font-family: monospace; background-color: #f8f9f9;")
        result_layout.addWidget(self.result_text)
        
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        tab.setLayout(layout)
        self.tab_widget.addTab(tab, "📐 Bearing/Distance")

    # ========== TRAVERSE METHODS ==========
    def add_traverse_leg(self):
        """Add a bearing/distance leg with DMS support"""
        if not self.start_easting.text() or not self.start_northing.text():
            QMessageBox.warning(
                self, "Missing Start Point", 
                "Please enter starting point coordinates first."
            )
            return
        
        try:
            bearing_decimal = self.bearing_input.get_decimal_degrees()
            bearing_dms = self.bearing_input.get_dms_string()
        except Exception as e:
            QMessageBox.warning(self, "Invalid Bearing", f"Could not parse bearing: {str(e)}")
            return
        
        try:
            distance = float(self.distance_input.text())
            if distance <= 0:
                QMessageBox.warning(self, "Invalid Distance", "Distance must be positive")
                return
        except ValueError:
            QMessageBox.warning(self, "Invalid Distance", "Please enter a valid number for distance")
            return
        
        try:
            start_e = float(self.start_easting.text())
            start_n = float(self.start_northing.text())
            
            e, n = start_e, start_n
            for i in range(self.traverse_table.rowCount()):
                prev_bearing = float(self.traverse_table.item(i, 2).text())
                prev_dist = float(self.traverse_table.item(i, 3).text())
                e += prev_dist * math.sin(math.radians(prev_bearing))
                n += prev_dist * math.cos(math.radians(prev_bearing))
            
            e += distance * math.sin(math.radians(bearing_decimal))
            n += distance * math.cos(math.radians(bearing_decimal))
            
        except ValueError as e:
            QMessageBox.warning(self, "Calculation Error", f"Error calculating coordinates: {str(e)}")
            return
        
        row = self.traverse_table.rowCount()
        self.traverse_table.insertRow(row)
        
        self.traverse_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.traverse_table.setItem(row, 1, QTableWidgetItem(bearing_dms))
        self.traverse_table.setItem(row, 2, QTableWidgetItem(f"{bearing_decimal:.6f}"))
        self.traverse_table.setItem(row, 3, QTableWidgetItem(f"{distance:.3f}"))
        self.traverse_table.setItem(row, 4, QTableWidgetItem(f"{e:.3f}"))
        self.traverse_table.setItem(row, 5, QTableWidgetItem(f"{n:.3f}"))
        
        delete_btn = QPushButton("❌")
        delete_btn.setMaximumWidth(30)
        delete_btn.clicked.connect(lambda checked: self.delete_traverse_leg(
            self.traverse_table.currentRow()
        ))
        self.traverse_table.setCellWidget(row, 6, delete_btn)
        
        self.bearing_input.clear()
        self.distance_input.clear()
        self.leg_desc.clear()
        
        self.calculate_traverse()

    def delete_traverse_leg(self, row):
        """Delete a traverse leg"""
        if row < 0:
            return
        self.traverse_table.removeRow(row)
        for i in range(self.traverse_table.rowCount()):
            self.traverse_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
        self.calculate_traverse()

    def clear_traverse_data(self):
        """Clear all traverse data"""
        self.traverse_table.setRowCount(0)
        self.start_easting.clear()
        self.start_northing.clear()
        self.bearing_input.clear()
        self.distance_input.clear()
        self.leg_desc.clear()
        self.result_text.clear()
        self.show_status("Traverse data cleared")

    def calculate_traverse(self):
        """Calculate all traverse coordinates"""
        if self.traverse_table.rowCount() == 0:
            self.result_text.setText("No traverse legs to calculate")
            return
        
        try:
            start_e = float(self.start_easting.text())
            start_n = float(self.start_northing.text())
            
            points = [(start_e, start_n)]
            e, n = start_e, start_n
            
            result = "📐 TRAVERSE CALCULATION RESULTS\n"
            result += "=" * 60 + "\n"
            result += f"START POINT: E = {e:.3f}m, N = {n:.3f}m\n\n"
            result += "LEGS:\n"
            result += "-" * 60 + "\n"
            
            for i in range(self.traverse_table.rowCount()):
                bearing_dms = self.traverse_table.item(i, 1).text()
                bearing = float(self.traverse_table.item(i, 2).text())
                distance = float(self.traverse_table.item(i, 3).text())
                
                e += distance * math.sin(math.radians(bearing))
                n += distance * math.cos(math.radians(bearing))
                points.append((e, n))
                
                result += f"Leg {i+1}: {bearing_dms}  {distance:.3f}m → "
                result += f"E = {e:.3f}m, N = {n:.3f}m\n"
                
                self.traverse_table.setItem(i, 4, QTableWidgetItem(f"{e:.3f}"))
                self.traverse_table.setItem(i, 5, QTableWidgetItem(f"{n:.3f}"))
            
            result += "\n" + "=" * 60 + "\n"
            result += f"FINAL POINT: E = {e:.3f}m, N = {n:.3f}m\n"
            
            if len(points) > 2:
                dx = points[-1][0] - points[0][0]
                dy = points[-1][1] - points[0][1]
                error = math.sqrt(dx*dx + dy*dy)
                
                result += f"\nCLOSING ERROR:\n"
                result += f"ΔE = {dx:.3f}m, ΔN = {dy:.3f}m\n"
                result += f"Linear Error = {error:.3f}m\n"
                
                # Total traverse perimeter
                total_length = sum(
                    float(self.traverse_table.item(i, 3).text())
                    for i in range(self.traverse_table.rowCount())
                )
                result += f"Total Traverse Length = {total_length:.3f}m\n"

                if error > 0.001:
                    precision_ratio = int(total_length / error)
                    result += f"Precision Ratio      = 1:{precision_ratio:,}\n"
                    result += "-" * 60 + "\n"
                    # Nigerian Survey Regulations: 1:5000 minimum for cadastral
                    if precision_ratio >= 5000:
                        result += "✅ MEETS Nigerian cadastral standard (min 1:5000)\n"
                    elif precision_ratio >= 2000:
                        result += "⚠️ Below cadastral standard — acceptable for general surveys only\n"
                    else:
                        result += "❌ FAILS minimum survey standard — re-measure\n"
                else:
                    result += "✅ Perfect closure (error < 1mm)\n"

            self.result_text.setText(result)
            self.show_status("Traverse calculated")
            
        except Exception as e:
            self.result_text.setText(f"Error calculating traverse: {str(e)}")

    def plot_traverse(self):
        """Plot traverse on map - silent version with no popups"""
        if not self.start_easting.text() or not self.start_northing.text():
            self.show_status("No start point for traverse plotting")
            return
        
        if self.traverse_table.rowCount() == 0:
            self.show_status("No traverse legs to plot")
            return
        
        crs = self.get_current_crs()
        if not crs:
            return
        
        try:
            start_e = float(self.start_easting.text())
            start_n = float(self.start_northing.text())
            
            points = [QgsPointXY(start_e, start_n)]
            e, n = start_e, start_n
            
            for i in range(self.traverse_table.rowCount()):
                bearing = float(self.traverse_table.item(i, 2).text())
                distance = float(self.traverse_table.item(i, 3).text())
                
                e += distance * math.sin(math.radians(bearing))
                n += distance * math.cos(math.radians(bearing))
                points.append(QgsPointXY(e, n))
            
            # Create line layer
            line_layer = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Traverse Line", "memory")
            provider = line_layer.dataProvider()
            
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromPolylineXY(points))
            provider.addFeature(feat)
            line_layer.updateExtents()
            
            # Style
            symbol = QgsLineSymbol.createSimple({'color': '#FF0000', 'width': '0.8'})
            line_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            QgsProject.instance().addMapLayer(line_layer)
            
            # Create point layer
            point_layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "Traverse Points", "memory")
            provider = point_layer.dataProvider()
            provider.addAttributes([QgsField("point_id", QVariant.Int)])
            point_layer.updateFields()
            
            features = []
            for i, point in enumerate(points):
                feat = QgsFeature()
                feat.setGeometry(QgsGeometry.fromPointXY(point))
                feat.setAttributes([i+1])
                features.append(feat)
            
            provider.addFeatures(features)
            point_layer.updateExtents()
            
            # Style
            point_symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': '#0000FF', 'size': '4'})
            point_layer.setRenderer(QgsSingleSymbolRenderer(point_symbol))
            QgsProject.instance().addMapLayer(point_layer)
            
            # SAFE ZOOM - with error handling
            if self.iface:
                try:
                    combined_extent = line_layer.extent()
                    combined_extent.combineExtentWith(point_layer.extent())
                    
                    if not combined_extent.isNull():
                        combined_extent.scale(1.1)
                        self.iface.mapCanvas().setExtent(combined_extent)
                        self.iface.mapCanvas().refresh()
                    else:
                        self.iface.setActiveLayer(line_layer)
                except Exception as e:
                    pass  # zoom not available in this window state
                    try:
                        self.iface.setActiveLayer(line_layer)
                    except:
                        pass
            
            self.show_status(f"✅ Plotted traverse with {len(points)} points")
            
        except Exception as e:
            self.show_status(f"❌ Plot error: {str(e)[:50]}")

    # ========== POSTGIS TAB ==========
    def setup_postgis_tab(self):
        """Enhanced tab for PostgreSQL access with ALL tables and non-blocking UI"""
        tab = QWidget()
        layout = QVBoxLayout()
        
        if not self.db_available:
            warning = QLabel("⚠️ Database not available. Please check connection.")
            warning.setStyleSheet("color: red; font-weight: bold; padding: 20px;")
            layout.addWidget(warning)
            tab.setLayout(layout)
            self.tab_widget.addTab(tab, "🗄️ PostgreSQL")
            return
        
        # Splitter for resizable sections
        splitter = QSplitter(Qt.Vertical)
        
        # ===== TOP SECTION: TABLE LIST =====
        top_widget = QWidget()
        top_layout = QVBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("📋 Database Tables"))
        
        self.refresh_tables_btn = QPushButton("🔄 Refresh Table List")
        self.refresh_tables_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 5px;")
        self.refresh_tables_btn.clicked.connect(self.refresh_table_list)
        toolbar.addWidget(self.refresh_tables_btn)
        
        toolbar.addStretch()
        
        # Progress bar
        self.table_progress = QProgressBar()
        self.table_progress.setVisible(False)
        toolbar.addWidget(self.table_progress)
        
        top_layout.addLayout(toolbar)
        
        # Table list with details
        self.table_tree = QTreeWidget()
        self.table_tree.setHeaderLabels(["Table Name", "Type", "Rows", "Columns", "Description"])
        self.table_tree.setAlternatingRowColors(True)
        self.table_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_tree.itemClicked.connect(self.on_table_selected)
        top_layout.addWidget(self.table_tree)
        
        top_widget.setLayout(top_layout)
        splitter.addWidget(top_widget)
        
        # ===== BOTTOM SECTION: DATA PREVIEW =====
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        
        # Preview toolbar
        preview_toolbar = QHBoxLayout()
        preview_toolbar.addWidget(QLabel("📊 Data Preview"))
        
        self.load_preview_btn = QPushButton("🔍 Load Preview")
        self.load_preview_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 5px;")
        self.load_preview_btn.clicked.connect(self.load_table_preview)
        self.load_preview_btn.setEnabled(False)
        preview_toolbar.addWidget(self.load_preview_btn)
        
        self.load_layer_btn = QPushButton("🗺️ Load as QGIS Layer")
        self.load_layer_btn.setStyleSheet("background-color: #9b59b6; color: white; font-weight: bold; padding: 5px;")
        self.load_layer_btn.clicked.connect(self.load_selected_as_layer)
        self.load_layer_btn.setEnabled(False)
        preview_toolbar.addWidget(self.load_layer_btn)
        
        self.preview_limit = QSpinBox()
        self.preview_limit.setRange(10, 1000)
        self.preview_limit.setValue(100)
        self.preview_limit.setSuffix(" rows")
        preview_toolbar.addWidget(QLabel("Limit:"))
        preview_toolbar.addWidget(self.preview_limit)
        
        preview_toolbar.addStretch()
        bottom_layout.addLayout(preview_toolbar)
        
        # Table preview
        self.preview_table = QTableWidget()
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        bottom_layout.addWidget(self.preview_table)
        
        bottom_widget.setLayout(bottom_layout)
        splitter.addWidget(bottom_widget)
        
        # Set initial splitter sizes (60% top, 40% bottom)
        splitter.setSizes([400, 300])
        
        layout.addWidget(splitter)
        tab.setLayout(layout)
        self.tab_widget.addTab(tab, "🗄️ PostgreSQL")

    # ========== POSTGIS METHODS ==========
    def refresh_table_list(self):
        """Refresh the list of all tables in the database"""
        if not self.db_available:
            return
        
        self.table_tree.clear()
        self.refresh_tables_btn.setEnabled(False)
        self.table_progress.setVisible(True)
        self.table_progress.setRange(0, 0)  # Indeterminate
        
        # Get connection parameters from settings
        from qgis.core import QgsSettings
        settings = QgsSettings()
        params = {
            "host": settings.value("survey_management/host", "localhost"),
            "port": int(settings.value("survey_management/port", "5432")),
            "database": settings.value("survey_management/database", "survey_management"),
            "user": settings.value("survey_management/user", "postgres"),
            "password": settings.value("survey_management/password", ""),
            "connect_timeout": 10
        }
        
        # Start background thread
        self.loader_thread = TableLoaderThread(params)
        self.loader_thread.progress.connect(self.on_table_load_progress)
        self.loader_thread.finished.connect(self.on_tables_loaded)
        self.loader_thread.error.connect(self.on_table_load_error)
        self.loader_thread.start()

    def on_table_load_progress(self, current, total, message):
        """Update progress during table loading"""
        self.table_progress.setRange(0, total)
        self.table_progress.setValue(current)
        self.table_progress.setFormat(f"{message} (%v/%m)")

    def on_tables_loaded(self, tables):
        """Handle completed table list loading"""
        self.table_progress.setVisible(False)
        self.refresh_tables_btn.setEnabled(True)
        
        # Populate tree
        spatial_count = 0
        nonspatial_count = 0
        
        for table in tables:
            item = QTreeWidgetItem(self.table_tree)
            item.setText(0, table['name'])
            
            # Table type with icon
            if table['has_geometry']:
                table_type = "📍 SPATIAL"
                spatial_count += 1
                item.setForeground(1, QColor("#27ae60"))  # Green
                font = QFont()
                font.setBold(True)
                item.setFont(1, font)
            else:
                table_type = "📋 Non-Spatial"
                nonspatial_count += 1
                item.setForeground(1, QColor("#3498db"))  # Blue
            
            item.setText(1, table_type)
            item.setText(2, f"{table['rows']:,}")
            item.setText(3, str(table['columns']))
            item.setText(4, table['description'][:50] if table['description'] else "")
            
            # Store data for later use
            item.setData(0, Qt.UserRole, table)
        
        # Add summary to header
        self.table_tree.setHeaderLabel(
            f"Table Name (📍 {spatial_count} spatial, 📋 {nonspatial_count} non-spatial)"
        )
        
        # Auto-expand
        self.table_tree.expandAll()

    def on_table_load_error(self, error_msg):
        """Handle table loading error"""
        self.table_progress.setVisible(False)
        self.refresh_tables_btn.setEnabled(True)
        QMessageBox.critical(self, "Database Error", f"Failed to load tables:\n{error_msg}")

    def on_table_selected(self, item, column):
        """Handle table selection"""
        self.table_data = item.data(0, Qt.UserRole)
        self.load_preview_btn.setEnabled(True)
        self.load_layer_btn.setEnabled(self.table_data['has_geometry'])
        
        # Clear previous preview
        self.preview_table.setRowCount(0)
        self.preview_table.setColumnCount(0)

    def load_table_preview(self):
        """Load preview of selected table"""
        if not hasattr(self, 'table_data'):
            return
        
        self.load_preview_btn.setEnabled(False)
        
        # Get connection parameters from settings
        from qgis.core import QgsSettings
        settings = QgsSettings()
        params = {
            "host": settings.value("survey_management/host", "localhost"),
            "port": int(settings.value("survey_management/port", "5432")),
            "database": settings.value("survey_management/database", "survey_management"),
            "user": settings.value("survey_management/user", "postgres"),
            "password": settings.value("survey_management/password", ""),
            "connect_timeout": 10
        }
        
        # Start preview thread
        self.preview_thread = DataPreviewThread(
            params, 
            self.table_data['name'],
            self.preview_limit.value()
        )
        self.preview_thread.progress.connect(lambda msg: self.load_preview_btn.setText(msg))
        self.preview_thread.finished.connect(self.on_preview_loaded)
        self.preview_thread.error.connect(self.on_preview_error)
        self.preview_thread.start()

    def on_preview_loaded(self, columns, data):
        """Handle loaded preview data"""
        self.load_preview_btn.setEnabled(True)
        self.load_preview_btn.setText("🔍 Load Preview")
        
        # Setup table
        self.preview_table.setColumnCount(len(columns))
        self.preview_table.setHorizontalHeaderLabels(columns)
        self.preview_table.setRowCount(len(data))
        
        # Fill data
        for row_idx, row in enumerate(data):
            for col_idx, value in enumerate(row):
                if value is None:
                    display = "NULL"
                elif isinstance(value, datetime):
                    display = value.strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(value, date):
                    display = value.strftime("%Y-%m-%d")
                else:
                    display = str(value)[:50]  # Truncate long values
                
                item = QTableWidgetItem(display)
                if value is None:
                    item.setForeground(QColor("#7f8c8d"))
                    font = QFont()
                    font.setItalic(True)
                    item.setFont(font)
                self.preview_table.setItem(row_idx, col_idx, item)
        
        # Resize columns
        self.preview_table.resizeColumnsToContents()
        self.show_status(f"Loaded {len(data)} rows from {self.table_data['name']}")

    def on_preview_error(self, error_msg):
        """Handle preview error"""
        self.load_preview_btn.setEnabled(True)
        self.load_preview_btn.setText("🔍 Load Preview")
        QMessageBox.critical(self, "Preview Error", f"Failed to load preview:\n{error_msg}")

    def load_selected_as_layer(self):
        """Load selected table as QGIS layer (spatial only)"""
        if not hasattr(self, 'table_data') or not self.table_data['has_geometry']:
            QMessageBox.information(self, "Info", "Selected table has no geometry column")
            return
        
        table_name = self.table_data['name']
        
        # Build connection URI
        from qgis.core import QgsSettings
        settings = QgsSettings()
        uri = QgsDataSourceUri()
        uri.setConnection(
            settings.value("survey_management/host", "localhost"),
            settings.value("survey_management/port", "5432"),
            settings.value("survey_management/database", "survey_management"),
            settings.value("survey_management/user", "postgres"),
            settings.value("survey_management/password", "")
        )
        
        # Find geometry column
        try:
            conn = psycopg2.connect(
                host=settings.value("survey_management/host", "localhost"),
                port=int(settings.value("survey_management/port", "5432")),
                database=settings.value("survey_management/database", "survey_management"),
                user=settings.value("survey_management/user", "postgres"),
                password=settings.value("survey_management/password", "")
            )
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute("""
                SELECT f_geometry_column, type, srid
                FROM geometry_columns 
                WHERE f_table_name = %s
            """, (table_name,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            
            if result:
                geom_col, geom_type, srid = result
                uri.setDataSource("public", table_name, geom_col)
                
                # Create layer
                layer_name = f"{table_name} (PostGIS)"
                layer = QgsVectorLayer(uri.uri(), layer_name, "postgres")
                
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    self.show_status(f"✅ Loaded {table_name} as QGIS layer")
                else:
                    QMessageBox.critical(self, "Error", "Failed to load layer")
            else:
                QMessageBox.warning(self, "No Geometry", "Could not find geometry column")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ========== DATABASE SAVE METHODS ==========
    def save_coordinates_to_postgis(self):
        """Save coordinates to PostGIS"""
        if not self.require_role('surveyor'):
            return
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return

        if not self.current_survey_id:
            reply = QMessageBox.question(self, "No Survey", "Create new survey?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.tab_widget.setCurrentIndex(0)
            return
        
        if self.coord_table.rowCount() == 0:
            QMessageBox.warning(self, "No Data", "No coordinates to save")
            return
        
        crs = self.get_current_crs()
        if not crs:
            return
        
        srid = self.current_srid
        
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("DELETE FROM survey_points WHERE survey_id = %s", (self.current_survey_id,))
            
            for i in range(self.coord_table.rowCount()):
                e = float(self.coord_table.item(i, 1).text())
                n = float(self.coord_table.item(i, 2).text())
                desc = self.coord_table.item(i, 3).text() or ""
                
                cur.execute("""
                    INSERT INTO survey_points 
                    (survey_id, point_number, geometry, description)
                    VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), %s), %s)
                """, (self.current_survey_id, i+1, e, n, srid, desc))
            
            if self.coord_table.rowCount() >= 3:
                points = []
                for i in range(self.coord_table.rowCount()):
                    e = float(self.coord_table.item(i, 1).text())
                    n = float(self.coord_table.item(i, 2).text())
                    points.append(f"{e} {n}")
                
                points.append(points[0])
                polygon_wkt = f"POLYGON(({', '.join(points)}))"
                area = self.calculate_polygon_area_from_table()
                
                cur.execute("""
                    INSERT INTO survey_boundaries 
                    (survey_id, geometry, calculated_area_sqm, verified)
                    VALUES (%s, ST_SetSRID(ST_GeomFromText(%s), %s), %s, %s)
                    ON CONFLICT (survey_id) DO UPDATE 
                    SET geometry = EXCLUDED.geometry, calculated_area_sqm = EXCLUDED.calculated_area_sqm
                """, (self.current_survey_id, polygon_wkt, srid, area, False))
            
            cur.close()
            self.write_audit("COORD_SAVE", table_name="survey_points",
                             record_id=self.current_survey_id,
                             new_values=f"{self.coord_table.rowCount()} points saved")

            QMessageBox.information(self, "Success", f"✅ Saved {self.coord_table.rowCount()} points")
            self.show_status(f"Saved {self.coord_table.rowCount()} points to database")
            self.check_adjoining_surveys()

        except Exception as e:
            print(f"Error saving coordinates: {e}")
            QMessageBox.critical(self, "Database Error", str(e))

    def save_traverse_to_postgis(self):
        """Save traverse to PostGIS"""
        if not self.require_role('surveyor'):
            return
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return
        
        if not self.current_survey_id:
            QMessageBox.warning(self, "No Survey", "Please create/select a survey first")
            self.tab_widget.setCurrentIndex(0)
            return
        
        if not self.start_easting.text() or not self.start_northing.text():
            QMessageBox.warning(self, "No Start Point", "Please enter start point")
            return
        
        if self.traverse_table.rowCount() == 0:
            QMessageBox.warning(self, "No Data", "No traverse legs to save")
            return
        
        crs = self.get_current_crs()
        if not crs:
            return
        
        srid = self.current_srid
        
        try:
            start_e = float(self.start_easting.text())
            start_n = float(self.start_northing.text())
            
            points = [(start_e, start_n)]
            e, n = start_e, start_n
            
            for i in range(self.traverse_table.rowCount()):
                bearing = float(self.traverse_table.item(i, 2).text())
                distance = float(self.traverse_table.item(i, 3).text())
                e += distance * math.sin(math.radians(bearing))
                n += distance * math.cos(math.radians(bearing))
                points.append((e, n))
            
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("DELETE FROM survey_points WHERE survey_id = %s", (self.current_survey_id,))
            
            for i, (e, n) in enumerate(points):
                cur.execute("""
                    INSERT INTO survey_points 
                    (survey_id, point_number, geometry, description)
                    VALUES (%s, %s, ST_SetSRID(ST_MakePoint(%s, %s), %s), %s)
                """, (self.current_survey_id, i+1, e, n, srid, f"Traverse point {i+1}"))
            
            if len(points) >= 3:
                first = points[0]
                last = points[-1]
                dx = last[0] - first[0]
                dy = last[1] - first[1]
                distance = math.sqrt(dx*dx + dy*dy)
                
                if distance < 0.1:
                    polygon_points = [f"{p[0]} {p[1]}" for p in points]
                    polygon_points.append(polygon_points[0])
                    polygon_wkt = f"POLYGON(({', '.join(polygon_points)}))"
                    area = self.calculate_polygon_area(points)
                    
                    cur.execute("""
                        INSERT INTO survey_boundaries 
                        (survey_id, geometry, calculated_area_sqm, verified)
                        VALUES (%s, ST_SetSRID(ST_GeomFromText(%s), %s), %s, %s)
                        ON CONFLICT (survey_id) DO UPDATE 
                        SET geometry = EXCLUDED.geometry, calculated_area_sqm = EXCLUDED.calculated_area_sqm
                    """, (self.current_survey_id, polygon_wkt, srid, area, False))
            
            cur.close()
            self.write_audit("TRAVERSE_SAVE", table_name="survey_points",
                             record_id=self.current_survey_id,
                             new_values=f"{len(points)} traverse points saved")

            QMessageBox.information(self, "Success", f"✅ Saved {len(points)} traverse points")
            self.show_status(f"Saved {len(points)} traverse points to database")
            self.check_adjoining_surveys()

        except Exception as e:
            print(f"Error saving traverse: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def calculate_polygon_area_from_table(self):
        """Calculate area from coordinate table"""
        if self.coord_table.rowCount() < 3:
            return 0
        
        points = []
        for i in range(self.coord_table.rowCount()):
            e = float(self.coord_table.item(i, 1).text())
            n = float(self.coord_table.item(i, 2).text())
            points.append((e, n))
        
        return self.calculate_polygon_area(points)

    # ========== DXF / DWG IMPORT ==========

    def import_from_dxf(self):
        """
        Main entry point for DXF/DWG import.
        Opens file browser → checks API key → runs extraction →
        shows preview dialog → loads accepted data into form.
        """
        # Ensure ezdxf is available — installs automatically if missing
        from .dependency_manager import ensure_ezdxf
        ok, err_msg = ensure_ezdxf(self)
        if not ok:
            if err_msg and err_msg != "Installation cancelled by user.":
                QMessageBox.critical(self, "ezdxf Required", err_msg)
            return


        # File browser — AI extraction runs automatically via hosted server, no key needed
        from qgis.PyQt.QtWidgets import QFileDialog
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select AutoCAD DXF or DWG File",
            "",
            "AutoCAD Files (*.dxf *.DXF *.dwg *.DWG);;DXF Files (*.dxf);;DWG Files (*.dwg);;All Files (*.*)"
        )
        if not filepath:
            return

        self.show_status("Importing from DXF — please wait...")
        QApplication.setOverrideCursor(Qt.WaitCursor)

        try:
            from .dxf_importer import DXFImporter
            importer = DXFImporter()
            result   = importer.import_file(filepath)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Import Error",
                f"An unexpected error occurred during import:\n\n{str(e)}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        # Fatal errors — nothing to show
        if result.errors and not result.points and not result.polylines and not result.legs:
            QMessageBox.critical(
                self, "Import Failed",
                "Could not extract data from the file:\n\n" +
                "\n".join(result.errors)
            )
            return

        # Show preview dialog
        try:
            from .dxf_import_dialog import DXFImportDialog
            dlg = DXFImportDialog(self, import_result=result, filepath=filepath)
        except Exception as e:
            QMessageBox.critical(self, "Dialog Error",
                f"Could not open import preview:\n{str(e)}")
            return

        if dlg.exec_() != QDialog.Accepted:
            return

        # Store result so _apply_dxf_result can access start_point and beacon_map
        self._last_dxf_result = result

        # Apply accepted data
        self._apply_dxf_result(
            points   = dlg.accepted_points,
            legs     = dlg.accepted_legs,
            metadata = dlg.accepted_metadata
        )

    def _apply_dxf_result(self, points, legs, metadata):
        """
        Load DXF import data into the plugin form.
        points   – list of {"x", "y", "desc"}
        legs     – list of {"bearing_dms", "bearing_decimal", "distance"}
        metadata – dict of field→value
        """
        loaded = []

        # ── Metadata ─────────────────────────────────────────────────────────
        if metadata:
            def _get(keys):
                """Get first non-empty value from a list of possible keys."""
                for k in keys if isinstance(keys, list) else [keys]:
                    v = metadata.get(k, "")
                    if v and str(v).strip() not in ("", "null", "None"):
                        return str(v).strip()
                return ""

            plan = _get("plan_number")
            if plan:
                self.plan_number.setText(plan)
                loaded.append("plan number")

            owner = _get(["owner", "owner_name"])
            if owner:
                self.owner_name.setText(owner)
                loaded.append("owner name")

            surveyor = _get(["surveyor", "surveyor_name"])
            if surveyor:
                self.surveyor.setText(surveyor)
                loaded.append("surveyor name")

            lga = _get("lga")
            if lga:
                self.lga.setText(lga)
                loaded.append("LGA")

            state = _get("state")
            if state:
                # Try exact match first, then case-insensitive
                idx = self.state.findText(state, Qt.MatchFixedString)
                if idx < 0:
                    idx = self.state.findText(state, Qt.MatchContains)
                if idx >= 0:
                    self.state.setCurrentIndex(idx)
                    loaded.append("state")

            date_str = _get("survey_date")
            if date_str:
                from qgis.PyQt.QtCore import QDate
                from datetime import datetime
                for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%B %Y",
                            "%Y-%m-%d", "%d %b %Y"]:
                    try:
                        dt = datetime.strptime(date_str, fmt)
                        self.survey_date.setDate(QDate(dt.year, dt.month, dt.day))
                        loaded.append("survey date")
                        break
                    except ValueError:
                        continue

            desc = _get("description")
            if desc:
                current_notes = self.notes.toPlainText().strip()
                if current_notes:
                    self.notes.setPlainText(current_notes + "\n" + desc)
                else:
                    self.notes.setPlainText(desc)
                loaded.append("description/notes")

            # Area — append to notes if no area field in DB
            area_sqm = metadata.get("area_sqm")
            area_acres = metadata.get("area_acres")
            area_ha = metadata.get("area_hectares")
            if area_sqm or area_acres or area_ha:
                area_parts = []
                if area_sqm:
                    try:
                        area_parts.append(f"{float(area_sqm):,.3f} sq m")
                    except (ValueError, TypeError):
                        pass
                if area_acres:
                    try:
                        area_parts.append(f"{float(area_acres):.4f} acres")
                    except (ValueError, TypeError):
                        pass
                if area_ha:
                    try:
                        area_parts.append(f"{float(area_ha):.4f} ha")
                    except (ValueError, TypeError):
                        pass
                if area_parts:
                    area_str = "Area: " + "  /  ".join(area_parts)
                    current = self.notes.toPlainText().strip()
                    self.notes.setPlainText(
                        (current + "\n" + area_str) if current else area_str
                    )
                    loaded.append("area")

            if loaded:
                self.tab_widget.setCurrentIndex(0)

        # ── Coordinates ───────────────────────────────────────────────────────
        if points:
            # Ask whether to replace or append
            if self.coord_table.rowCount() > 0:
                reply = QMessageBox.question(
                    self, "Existing Coordinates",
                    f"The coordinate table already has {self.coord_table.rowCount()} points.\n\n"
                    "Replace all existing points with the imported data?\n\n"
                    "• YES — clear the table and load imported points\n"
                    "• NO  — append imported points to existing table",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self.coord_table.setRowCount(0)

            start_row = self.coord_table.rowCount()
            for i, pt in enumerate(points):
                row = start_row + i
                self.coord_table.insertRow(row)
                self.coord_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
                self.coord_table.setItem(row, 1, QTableWidgetItem(f"{pt['x']:.3f}"))
                self.coord_table.setItem(row, 2, QTableWidgetItem(f"{pt['y']:.3f}"))
                self.coord_table.setItem(row, 3, QTableWidgetItem(pt.get("desc", "")))

                delete_btn = QPushButton("❌")
                delete_btn.setMaximumWidth(30)
                delete_btn.clicked.connect(
                    lambda checked, r=row: self.delete_coordinate_row(r)
                )
                self.coord_table.setCellWidget(row, 4, delete_btn)

            loaded.append(f"{len(points)} coordinate points")

            # Switch to Coordinate Input tab
            coord_tab_idx = 2  # default position
            for i in range(self.tab_widget.count()):
                if "Coordinate" in self.tab_widget.tabText(i):
                    coord_tab_idx = i
                    break
            self.tab_widget.setCurrentIndex(coord_tab_idx)

        # ── Traverse legs ─────────────────────────────────────────────────────
        if legs:
            if self.traverse_table.rowCount() > 0:
                reply = QMessageBox.question(
                    self, "Existing Traverse",
                    f"The traverse table already has {self.traverse_table.rowCount()} legs.\n\n"
                    "Replace all existing legs with the imported data?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self.traverse_table.setRowCount(0)
                    self.result_text.clear()

            # Auto-fill starting point from extracted beacon coordinate
            # result.start_point is set by _sort_legs_by_points using actual P1 coordinate
            start_point = getattr(self._last_dxf_result, 'start_point', None) \
                          if hasattr(self, '_last_dxf_result') else None

            if start_point and start_point.get('x') and start_point.get('y'):
                self.start_easting.setText(f"{start_point['x']:.3f}")
                self.start_northing.setText(f"{start_point['y']:.3f}")
                label = start_point.get('label', '')
                loaded.append(f"starting point {('(' + label + ')') if label else ''}")
            elif not self.start_easting.text() or not self.start_northing.text():
                # No start_point extracted — try to use first imported coordinate point
                if points:
                    first = points[0]
                    self.start_easting.setText(f"{first['x']:.3f}")
                    self.start_northing.setText(f"{first['y']:.3f}")
                    loaded.append("starting point (from first coordinate)")

            import math
            try:
                start_e = float(self.start_easting.text()) if self.start_easting.text() else 0.0
                start_n = float(self.start_northing.text()) if self.start_northing.text() else 0.0
            except ValueError:
                start_e, start_n = 0.0, 0.0

            e, n = start_e, start_n
            # Accumulate from existing legs if appending
            for i in range(self.traverse_table.rowCount()):
                try:
                    b = float(self.traverse_table.item(i, 2).text())
                    d = float(self.traverse_table.item(i, 3).text())
                    e += d * math.sin(math.radians(b))
                    n += d * math.cos(math.radians(b))
                except Exception:
                    pass

            for leg in legs:
                bearing_decimal = leg["bearing_decimal"]
                bearing_dms     = leg["bearing_dms"]
                distance        = leg["distance"]

                e += distance * math.sin(math.radians(bearing_decimal))
                n += distance * math.cos(math.radians(bearing_decimal))

                row = self.traverse_table.rowCount()
                self.traverse_table.insertRow(row)
                self.traverse_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
                self.traverse_table.setItem(row, 1, QTableWidgetItem(bearing_dms))
                self.traverse_table.setItem(row, 2, QTableWidgetItem(f"{bearing_decimal:.6f}"))
                self.traverse_table.setItem(row, 3, QTableWidgetItem(f"{distance:.3f}"))
                self.traverse_table.setItem(row, 4, QTableWidgetItem(f"{e:.3f}"))
                self.traverse_table.setItem(row, 5, QTableWidgetItem(f"{n:.3f}"))

                del_btn = QPushButton("❌")
                del_btn.setMaximumWidth(30)
                del_btn.clicked.connect(
                    lambda checked: self.delete_traverse_leg(
                        self.traverse_table.currentRow()
                    )
                )
                self.traverse_table.setCellWidget(row, 6, del_btn)

            loaded.append(f"{len(legs)} traverse legs")
            self.calculate_traverse()

            # Switch to traverse tab
            for i in range(self.tab_widget.count()):
                if "Bearing" in self.tab_widget.tabText(i) or "Distance" in self.tab_widget.tabText(i):
                    self.tab_widget.setCurrentIndex(i)
                    break

        # ── Summary message ───────────────────────────────────────────────────
        if loaded:
            self.show_status(f"✅ DXF import loaded: {', '.join(loaded)}")
            self.write_audit("DXF_IMPORT", new_values=f"loaded={','.join(loaded)}")
        else:
            self.show_status("DXF import: nothing was loaded")

    def calculate_polygon_area(self, points):
        """Calculate polygon area using shoelace formula"""
        if len(points) < 3:
            return 0

        area = 0
        n = len(points)
        for i in range(n):
            j = (i + 1) % n
            area += points[i][0] * points[j][1]
            area -= points[j][0] * points[i][1]

        return abs(area) / 2.0

    def check_adjoining_surveys(self):
        """Check if the saved boundary overlaps or is within 1 m of existing surveys.
        Called automatically after saving coordinates or traverse."""
        if not self.db_available or not self.current_survey_id:
            return
        try:
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            cur.execute("""
                SELECT s.plan_number, s.owner_name,
                       ROUND(ST_Distance(b1.geometry, b2.geometry)::numeric, 3) AS dist
                FROM   survey_boundaries b1
                JOIN   survey_boundaries b2
                       ON b1.survey_id != b2.survey_id
                JOIN   surveys s ON s.survey_id = b2.survey_id
                WHERE  b1.survey_id = %s
                  AND  ST_DWithin(b1.geometry, b2.geometry, 1.0)
                ORDER  BY dist
                LIMIT  10
            """, (self.current_survey_id,))
            neighbours = cur.fetchall()
            cur.close()

            if not neighbours:
                return

            lines = ["⚠️  ADJOINING SURVEYS DETECTED\n"]
            lines.append("The following surveys are within 1 m of this boundary:\n")
            for plan_no, owner, dist in neighbours:
                dist_f = float(dist)
                if dist_f < 0.01:
                    lines.append(f"❌  OVERLAP : {plan_no}  ({owner})")
                else:
                    lines.append(f"📍  {dist_f:.3f} m away : {plan_no}  ({owner})")
            lines.append("\nPlease verify boundary accuracy before finalising.")
            QMessageBox.warning(self, "Boundary Check", "\n".join(lines))

        except Exception as e:
            # PostGIS may not be available in offline mode — fail silently
            print(f"[check_adjoining_surveys] {e}")
    def save_survey_metadata(self):
        """Save new survey to database"""
        if not self.require_role('surveyor'):
            return
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return

        if not self.plan_number.text() or not self.owner_name.text():
            QMessageBox.warning(self, "Validation Error", "Plan Number and Owner Name are required")
            return
        
        # Check if we're overwriting
        if self.current_survey_id:
            reply = QMessageBox.question(
                self, "Confirm Save",
                f"A survey is currently loaded (ID: {self.current_survey_id}).\n"
                f"Do you want to SAVE AS NEW SURVEY?\n\n"
                f"Click 'Yes' to create a new survey.\n"
                f"Click 'No' to cancel.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                return
        
        try:
            # Get CRS value
            crs_text = self.crs_combo.currentText()
            if crs_text == "Custom EPSG (specify below)":
                crs_value = self.custom_crs.text().strip()
                if crs_value:
                    crs_value = f"EPSG:{crs_value}"
                else:
                    crs_value = None
            else:
                crs_value = crs_text.split(" - ")[0]
            
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            
            # Insert new survey
            cur.execute("""
                INSERT INTO surveys 
                (plan_number, owner_name, survey_date, original_crs, 
                 surveyor_name, local_government, state, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING survey_id
            """, (
                self.plan_number.text().strip(),
                self.owner_name.text().strip(),
                self.survey_date.date().toPyDate(),
                crs_value,
                self.surveyor.text().strip() or None,
                self.lga.text().strip() or None,
                self.state.currentText(),
                self.notes.toPlainText().strip() or None
            ))
            
            survey_id = cur.fetchone()[0]
            self.write_audit("SURVEY_CREATE", table_name="surveys",
                             record_id=survey_id,
                             new_values=f"plan={self.plan_number.text().strip()}")
            cur.close()

            # Update current survey
            self.current_survey_id = survey_id
            self.survey_id_display.setText(str(survey_id))
            self.current_survey_label.setText(
                f"📋 Current Survey: {self.plan_number.text()} (ID: {survey_id})"
            )
            self.update_survey_btn.setEnabled(True)

            # Success message
            QMessageBox.information(
                self, "Success",
                f"✅ New survey saved successfully!\n\nSurvey ID: {survey_id}"
            )

            # Refresh lists
            self.load_all_surveys()
            self.refresh_documents_list()
            
        except psycopg2.IntegrityError as e:
            QMessageBox.critical(
                self, "Duplicate Plan Number",
                f"Plan number '{self.plan_number.text()}' already exists.\n"
                f"Please use a unique plan number."
            )
        except Exception as e:
            print(f"Error saving survey: {e}")
            QMessageBox.critical(self, "Database Error", str(e))

    def update_survey_metadata(self):
        """Update existing survey in database"""
        if not self.require_role('surveyor'):
            return
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return

        if not self.current_survey_id:
            QMessageBox.warning(
                self, "No Survey",
                "No survey is currently loaded for editing.\n"
                "Please search and load a survey first."
            )
            return
        
        if not self.plan_number.text() or not self.owner_name.text():
            QMessageBox.warning(self, "Validation Error", "Plan Number and Owner Name are required")
            return
        
        # Confirm update
        reply = QMessageBox.question(
            self, "Confirm Update",
            f"Are you sure you want to UPDATE Survey ID {self.current_survey_id}?\n\n"
            f"Plan: {self.plan_number.text()}\n"
            f"Owner: {self.owner_name.text()}\n\n"
            f"This will overwrite the existing record.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.No:
            return
        
        try:
            # Get CRS value
            crs_text = self.crs_combo.currentText()
            if crs_text == "Custom EPSG (specify below)":
                crs_value = self.custom_crs.text().strip()
                if crs_value:
                    crs_value = f"EPSG:{crs_value}"
                else:
                    crs_value = None
            else:
                crs_value = crs_text.split(" - ")[0]
            
            self.db_connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = self.db_connection.cursor()
            
            # Update survey
            cur.execute("""
                UPDATE surveys 
                SET plan_number = %s, owner_name = %s, survey_date = %s, 
                    original_crs = %s, surveyor_name = %s, local_government = %s, 
                    state = %s, notes = %s
                WHERE survey_id = %s
            """, (
                self.plan_number.text().strip(),
                self.owner_name.text().strip(),
                self.survey_date.date().toPyDate(),
                crs_value,
                self.surveyor.text().strip() or None,
                self.lga.text().strip() or None,
                self.state.currentText(),
                self.notes.toPlainText().strip() or None,
                self.current_survey_id
            ))

            cur.close()
            self.write_audit("SURVEY_UPDATE", table_name="surveys",
                             record_id=self.current_survey_id,
                             new_values=f"plan={self.plan_number.text().strip()}")

            # Update label
            self.current_survey_label.setText(
                f"📋 Current Survey: {self.plan_number.text()} (ID: {self.current_survey_id}) - UPDATED"
            )

            QMessageBox.information(
                self, "Success",
                f"✅ Survey ID {self.current_survey_id} updated successfully!"
            )

            # Refresh lists
            self.load_all_surveys()
            self.refresh_documents_list()
            
        except psycopg2.IntegrityError as e:
            QMessageBox.critical(
                self, "Duplicate Plan Number",
                f"Plan number '{self.plan_number.text()}' already exists.\n"
                f"Please use a unique plan number."
            )
        except Exception as e:
            print(f"Error updating survey: {e}")
            QMessageBox.critical(self, "Database Error", str(e))

    def clear_survey_metadata(self):
        """Clear survey form"""
        self.survey_id_display.clear()
        self.plan_number.clear()
        self.owner_name.clear()
        self.survey_date.setDate(QDate.currentDate())
        self.surveyor.clear()
        self.lga.clear()
        self.state.setCurrentIndex(0)
        self.notes.clear()
        self.crs_combo.setCurrentIndex(1)  # Reset to default
        self.custom_crs.clear()
        self.current_survey_id = None
        self.current_survey_label.setText("📋 Current Survey: None")
        self.update_survey_btn.setEnabled(False)
        self.show_status("Form cleared")