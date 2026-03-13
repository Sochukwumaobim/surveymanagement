# -*- coding: utf-8 -*-
"""
/***************************************************************************
 SurveyManagementDialog
                                 A QGIS plugin
 Digital archiving for Nigerian survey records
                              -------------------
        begin                : 2026-03-12
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
# Add QAbstractItemView to the imports at the top of the file
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QDateEdit, QPushButton,
    QTextEdit, QMessageBox, QGroupBox, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QWidget, QApplication, QStackedWidget,
    QFileDialog, QCheckBox, QProgressDialog, QSpinBox,
    QDoubleSpinBox, QGridLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QProgressBar, QAbstractItemView  # <-- ADD THIS
)
from qgis.PyQt.QtGui import QColor, QFont

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
                cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
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
            cur.execute(f'SELECT * FROM "{self.table_name}" LIMIT {self.limit}')
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
    def __init__(self, parent=None, db_connection=None):
        """Constructor."""
        super(SurveyManagementDialog, self).__init__(parent)
        
        # Set up the user interface from Designer
        self.setupUi(self)
        
        # Store database connection
        self.db_connection = db_connection
        self.current_survey_id = None
        self.current_srid = 26332  # Default to Nigeria Mid Belt
        self.pdf_base_path = "C:\\SurveyRecords\\"  # Default base path for PDFs
        
        # Store reference to iface
        self.iface = parent
        
        # Show database status
        self.db_available = (db_connection is not None and 
                            PSYCOPG2_AVAILABLE and 
                            not db_connection.closed)
        
        # Set window properties - MAKE IT NON-MODAL!
        self.setWindowTitle("Survey Management System - Nigerian Survey Records")
        self.setMinimumWidth(1200)
        self.setMinimumHeight(800)
        self.setWindowFlags(Qt.Window)  # Allow interaction with QGIS
        
        # Center the dialog
        self.center_on_screen()
        
        # Clear any existing layout
        if self.layout() is not None:
            QWidget().setLayout(self.layout())
        
        # Create main layout
        self.create_main_layout()
        
        # Load initial data if database available
        if self.db_available:
            self.refresh_search()
            self.ensure_document_table_exists()
            self.refresh_table_list()  # Load tables in PostgreSQL tab

    def center_on_screen(self):
        """Center the dialog on the screen"""
        frame_geometry = self.frameGeometry()
        screen_center = QApplication.primaryScreen().availableGeometry().center()
        frame_geometry.moveCenter(screen_center)
        self.move(frame_geometry.topLeft())

    def ensure_document_table_exists(self):
        """Ensure the survey_documents table exists"""
        if not self.db_available:
            return
        
        try:
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
                self.db_connection.commit()
                print("Created survey_documents table")
            
            cur.close()
            
        except Exception as e:
            print(f"Error ensuring document table: {e}")

    def create_main_layout(self):
        """Create the main layout with all tabs"""
        main_layout = QVBoxLayout()
        
        # Header
        header_layout = QHBoxLayout()
        title = QLabel("🏛️ NIGERIAN SURVEY MANAGEMENT SYSTEM")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #2c3e50;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        
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
        crs_layout.addWidget(self.custom_crs)
        
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
        
        # Bottom buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.minimize_btn = QPushButton("⏱️ Keep Open")
        self.minimize_btn.setMinimumWidth(120)
        self.minimize_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        self.minimize_btn.clicked.connect(self.showMinimized)
        button_layout.addWidget(self.minimize_btn)
        
        self.hide_btn = QPushButton("👁️ Hide to Tray")
        self.hide_btn.setMinimumWidth(120)
        self.hide_btn.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold;")
        self.hide_btn.clicked.connect(self.hide)
        button_layout.addWidget(self.hide_btn)
        
        close_btn = QPushButton("Close")
        close_btn.setMinimumWidth(100)
        close_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        close_btn.clicked.connect(self.close)
        button_layout.addWidget(close_btn)
        
        main_layout.addLayout(button_layout)
        
        # Hint about non-modal mode
        hint = QLabel("💡 This window stays open while you work in QGIS. Click 'Hide to Tray' to temporarily hide it.")
        hint.setStyleSheet("color: #7f8c8d; font-style: italic;")
        main_layout.addWidget(hint)
        
        self.setLayout(main_layout)

    def on_crs_changed(self, text):
        """Handle CRS selection change"""
        self.custom_crs.setEnabled(text == "Custom EPSG (specify below)")
        if text != "Custom EPSG (specify below)":
            try:
                epsg_code = text.split(" - ")[0].replace("EPSG:", "")
                self.current_srid = int(epsg_code)
            except:
                self.current_srid = 26332
        else:
            self.current_srid = None

    def get_current_crs(self):
        """Get the currently selected CRS"""
        if self.crs_combo.currentText() == "Custom EPSG (specify below)":
            try:
                epsg = int(self.custom_crs.text().strip())
                self.current_srid = epsg
                return QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
            except:
                QMessageBox.warning(self, "Invalid CRS", "Please enter a valid EPSG code")
                return None
        else:
            text = self.crs_combo.currentText()
            epsg_code = text.split(" - ")[0].replace("EPSG:", "")
            self.current_srid = int(epsg_code)
            return QgsCoordinateReferenceSystem(f"EPSG:{epsg_code}")

    # ========== SURVEY METADATA TAB ==========
    def setup_survey_tab(self):
        """Tab for survey metadata"""
        tab = QWidget()
        layout = QVBoxLayout()
        layout.setSpacing(10)
        
        # Search section
        search_group = QGroupBox("🔍 SEARCH AND LOAD EXISTING SURVEY")
        search_group.setStyleSheet("QGroupBox { font-weight: bold; color: #2980b9; }")
        search_layout = QHBoxLayout()
        
        self.survey_search_input = QLineEdit()
        self.survey_search_input.setPlaceholderText("Enter Plan Number, Owner Name, or Survey ID...")
        self.survey_search_input.setMinimumWidth(400)
        self.survey_search_input.returnPressed.connect(self.search_surveys_for_load)
        search_layout.addWidget(self.survey_search_input)
        
        search_survey_btn = QPushButton("🔍 Search")
        search_survey_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 5px;")
        search_survey_btn.clicked.connect(self.search_surveys_for_load)
        search_layout.addWidget(search_survey_btn)
        
        self.survey_search_results = QComboBox()
        self.survey_search_results.setMinimumWidth(500)
        self.survey_search_results.setPlaceholderText("Select a survey to load...")
        search_layout.addWidget(self.survey_search_results)
        
        load_survey_btn = QPushButton("📂 Load Selected")
        load_survey_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 5px;")
        load_survey_btn.clicked.connect(self.load_selected_survey_for_edit)
        search_layout.addWidget(load_survey_btn)
        
        refresh_btn = QPushButton("🔄 Recent")
        refresh_btn.setStyleSheet("background-color: #95a5a6; color: white; padding: 5px;")
        refresh_btn.clicked.connect(lambda: self.load_recent_surveys())
        search_layout.addWidget(refresh_btn)
        
        search_layout.addStretch()
        search_group.setLayout(search_layout)
        layout.addWidget(search_group)
        
        # Current survey indicator
        self.current_survey_label = QLabel("📋 Current Survey: None")
        self.current_survey_label.setStyleSheet("font-weight: bold; color: #2980b9; font-size: 11pt;")
        layout.addWidget(self.current_survey_label)
        
        # Form group
        form_group = QGroupBox("2. SURVEY METADATA")
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

    # ========== SURVEY SEARCH METHODS ==========
    def search_surveys_for_load(self):
        """Search for surveys to load"""
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return
        
        search_term = self.survey_search_input.text().strip()
        if not search_term:
            self.load_recent_surveys()
            return
        
        try:
            cur = self.db_connection.cursor()
            cur.execute("""
                SELECT survey_id, plan_number, owner_name, survey_date 
                FROM surveys 
                WHERE plan_number ILIKE %s 
                   OR owner_name ILIKE %s 
                   OR CAST(survey_id AS TEXT) ILIKE %s
                ORDER BY survey_date DESC
                LIMIT 50
            """, (f'%{search_term}%', f'%{search_term}%', f'%{search_term}%'))
            
            results = cur.fetchall()
            cur.close()
            
            self.survey_search_results.clear()
            
            if not results:
                self.survey_search_results.addItem("No surveys found", None)
                return
            
            for row in results:
                survey_id, plan_number, owner_name, survey_date = row
                date_str = survey_date.strftime('%Y-%m-%d') if survey_date else 'No date'
                display_text = f"ID:{survey_id} | {plan_number} | {owner_name} | {date_str}"
                self.survey_search_results.addItem(display_text, survey_id)
            
            QMessageBox.information(
                self, "Search Complete",
                f"Found {len(results)} survey(s). Select one from the dropdown."
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Database Error", str(e))

    def load_recent_surveys(self):
        """Load recent surveys into dropdown"""
        if not self.db_available:
            return
        
        try:
            cur = self.db_connection.cursor()
            cur.execute("""
                SELECT survey_id, plan_number, owner_name, survey_date 
                FROM surveys 
                ORDER BY survey_date DESC
                LIMIT 20
            """)
            
            results = cur.fetchall()
            cur.close()
            
            self.survey_search_results.clear()
            self.survey_search_results.addItem("-- Recent Surveys --", None)
            
            for row in results:
                survey_id, plan_number, owner_name, survey_date = row
                date_str = survey_date.strftime('%Y-%m-%d') if survey_date else 'No date'
                display_text = f"ID:{survey_id} | {plan_number} | {owner_name} | {date_str}"
                self.survey_search_results.addItem(display_text, survey_id)
            
        except Exception as e:
            QMessageBox.critical(self, "Database Error", str(e))

    def load_selected_survey_for_edit(self):
        """Load selected survey for editing"""
        survey_id = self.survey_search_results.currentData()
        if not survey_id:
            QMessageBox.warning(self, "No Selection", "Please select a survey to load")
            return
        
        self.load_survey_by_id(survey_id)

    def load_survey_by_id(self, survey_id):
        """Load survey by ID into form"""
        if not self.db_available:
            return
        
        try:
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
                f"📋 Current Survey: {plan_number} (ID: {sid}) - READY FOR EDITING"
            )
            
            self.update_survey_btn.setEnabled(True)
            self.save_survey_btn.setEnabled(True)
            self.refresh_documents_list()
            
            QMessageBox.information(
                self, "Survey Loaded",
                f"✅ Survey loaded successfully!\n\n"
                f"ID: {sid}\n"
                f"Plan: {plan_number}\n"
                f"Owner: {owner_name}"
            )
            
        except Exception as e:
            QMessageBox.critical(self, "Database Error", str(e))

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
        hash_func = hashlib.md5() if algorithm == 'MD5' else hashlib.sha256()
        
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
            self.db_connection.commit()
            cur.close()
            
            QMessageBox.information(self, "Success", f"✅ Document uploaded successfully!")
            
            self.doc_file_path.clear()
            self.doc_description.clear()
            self.doc_is_primary.setChecked(True)
            self.refresh_documents_list()
            
        except Exception as e:
            self.db_connection.rollback()
            QMessageBox.critical(self, "Database Error", str(e))

    def refresh_documents_list(self):
        """Refresh the documents list"""
        if not self.db_available or not self.current_survey_id:
            self.documents_table.setRowCount(0)
            self.doc_survey_label.setText("Current Survey: None")
            return
        
        try:
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
            QMessageBox.critical(self, "Database Error", str(e))

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
                self.db_connection.commit()
                cur.close()
                QMessageBox.information(self, "Success", "✅ Document verified successfully!")
            else:
                QMessageBox.critical(self, "Verification Failed", "❌ Document has been modified!")
            
            self.refresh_documents_list()
            
        except Exception as e:
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
            cur = self.db_connection.cursor()
            cur.execute("UPDATE survey_documents SET is_primary = FALSE WHERE survey_id = %s", (self.current_survey_id,))
            cur.execute("UPDATE survey_documents SET is_primary = TRUE WHERE document_id = %s", (doc_id,))
            self.db_connection.commit()
            cur.close()
            QMessageBox.information(self, "Success", "Primary document updated")
            self.refresh_documents_list()
        except Exception as e:
            self.db_connection.rollback()
            QMessageBox.critical(self, "Error", str(e))

    def verify_all_documents(self):
        """Verify all documents"""
        if not self.current_survey_id:
            return
        
        try:
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
                    self.db_connection.commit()
                    cur.close()
                else:
                    failed += 1
            
            progress.close()
            
            QMessageBox.information(self, "Verification Complete", 
                f"📊 Results:\n✅ Verified: {verified}\n❌ Failed: {failed}\n⚠️ Missing: {missing}")
            
            self.refresh_documents_list()
            
        except Exception as e:
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
        """Plot coordinates on map"""
        if self.coord_table.rowCount() == 0:
            QMessageBox.warning(self, "No Data", "No coordinates to plot")
            return
        
        crs = self.get_current_crs()
        if not crs:
            return
        
        points = []
        for i in range(self.coord_table.rowCount()):
            try:
                e = float(self.coord_table.item(i, 1).text())
                n = float(self.coord_table.item(i, 2).text())
                points.append(QgsPointXY(e, n))
            except:
                continue
        
        if len(points) < 1:
            return
        
        point_layer = QgsVectorLayer(f"Point?crs={crs.authid()}", "Survey Points", "memory")
        provider = point_layer.dataProvider()
        provider.addAttributes([QgsField("point_id", QVariant.Int), QgsField("description", QVariant.String)])
        point_layer.updateFields()
        
        features = []
        for i, point in enumerate(points):
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromPointXY(point))
            desc = self.coord_table.item(i, 3).text() if self.coord_table.item(i, 3) else ""
            feat.setAttributes([i+1, desc])
            features.append(feat)
        
        provider.addFeatures(features)
        point_layer.updateExtents()
        
        symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': '#FF0000', 'size': '4'})
        point_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        QgsProject.instance().addMapLayer(point_layer)
        
        if self.iface:
            self.iface.setActiveLayer(point_layer)
            self.iface.zoomToActiveLayer()
        
        QMessageBox.information(self, "Success", f"✅ Plotted {len(points)} points")

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
        delete_btn.clicked.connect(lambda checked, r=row: self.delete_traverse_leg(r))
        self.traverse_table.setCellWidget(row, 6, delete_btn)
        
        self.bearing_input.clear()
        self.distance_input.clear()
        self.leg_desc.clear()
        
        self.calculate_traverse()

    def delete_traverse_leg(self, row):
        """Delete a traverse leg"""
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
                
                if error < 0.1:
                    result += "✅ Traverse CLOSES within tolerance (0.1m)\n"
                elif error < 0.5:
                    result += "⚠️ Traverse CLOSES but error > 0.1m - Check measurements\n"
                else:
                    result += "❌ Traverse does NOT close - Error too large\n"
            
            self.result_text.setText(result)
            
        except Exception as e:
            self.result_text.setText(f"Error calculating traverse: {str(e)}")

    def plot_traverse(self):
        """Plot traverse on map"""
        if not self.start_easting.text() or not self.start_northing.text():
            QMessageBox.warning(self, "No Start Point", "Please enter start point coordinates")
            return
        
        if self.traverse_table.rowCount() == 0:
            QMessageBox.warning(self, "No Data", "No traverse legs to plot")
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
            
            line_layer = QgsVectorLayer(f"LineString?crs={crs.authid()}", "Traverse Line", "memory")
            provider = line_layer.dataProvider()
            
            feat = QgsFeature()
            feat.setGeometry(QgsGeometry.fromPolylineXY(points))
            provider.addFeature(feat)
            line_layer.updateExtents()
            
            symbol = QgsLineSymbol.createSimple({'color': '#FF0000', 'width': '0.8'})
            line_layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            QgsProject.instance().addMapLayer(line_layer)
            
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
            
            point_symbol = QgsMarkerSymbol.createSimple({'name': 'circle', 'color': '#0000FF', 'size': '4'})
            point_layer.setRenderer(QgsSingleSymbolRenderer(point_symbol))
            QgsProject.instance().addMapLayer(point_layer)
            
            if self.iface:
                self.iface.setActiveLayer(line_layer)
                self.iface.zoomToActiveLayer()
            
            QMessageBox.information(self, "Success", f"✅ Plotted traverse with {len(points)} points")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

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

    def on_preview_error(self, error_msg):
        """Handle preview error"""
        self.load_preview_btn.setEnabled(True)
        self.load_preview_btn.setText("🔍 Load Preview")
        QMessageBox.critical(self, "Preview Error", f"Failed to load preview:\n{error_msg}")

    def load_selected_as_layer(self):
        """Load selected table as QGIS layer (spatial only)"""
        if not hasattr(self, 'table_data') or not self.table_data['has_geometry']:
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
            cur = conn.cursor()
            cur.execute("""
                SELECT f_geometry_column 
                FROM geometry_columns 
                WHERE f_table_name = %s
            """, (table_name,))
            result = cur.fetchone()
            cur.close()
            conn.close()
            
            if result:
                geom_col = result[0]
                uri.setDataSource("public", table_name, geom_col)
                
                # Create layer
                layer_name = f"{table_name} (PostGIS)"
                layer = QgsVectorLayer(uri.uri(), layer_name, "postgres")
                
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                    QMessageBox.information(
                        self, "Success",
                        f"✅ Loaded {table_name} as QGIS layer\n"
                        f"Features: {layer.featureCount()}\n"
                        f"Geometry: {QgsWkbTypes.displayString(layer.wkbType())}"
                    )
                else:
                    QMessageBox.critical(self, "Error", "Failed to load layer")
            else:
                QMessageBox.warning(self, "No Geometry", "Could not find geometry column")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ========== DATABASE SAVE METHODS ==========
    def save_coordinates_to_postgis(self):
        """Save coordinates to PostGIS"""
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
            
            self.db_connection.commit()
            cur.close()
            
            QMessageBox.information(self, "Success", f"✅ Saved {self.coord_table.rowCount()} points")
            
        except Exception as e:
            self.db_connection.rollback()
            QMessageBox.critical(self, "Database Error", str(e))

    def save_traverse_to_postgis(self):
        """Save traverse to PostGIS"""
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
            
            self.db_connection.commit()
            cur.close()
            QMessageBox.information(self, "Success", f"✅ Saved {len(points)} traverse points")
            
        except Exception as e:
            self.db_connection.rollback()
            QMessageBox.critical(self, "Error", str(e))

    def save_survey_metadata(self):
        """Save new survey"""
        if not self.db_available:
            QMessageBox.warning(self, "No Database", "Database not connected")
            return
        
        if not self.plan_number.text() or not self.owner_name.text():
            QMessageBox.warning(self, "Validation Error", "Plan Number and Owner Name are required")
            return
        
        if self.current_survey_id:
            reply = QMessageBox.question(self, "Confirm", "Save as NEW survey?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                return
        
        try:
            crs_text = self.crs_combo.currentText()
            if crs_text == "Custom EPSG (specify below)":
                crs_value = self.custom_crs.text().strip()
                if crs_value:
                    crs_value = f"EPSG:{crs_value}"
                else:
                    crs_value = None
            else:
                crs_value = crs_text.split(" - ")[0]
            
            cur = self.db_connection.cursor()
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
            self.db_connection.commit()
            cur.close()
            
            self.current_survey_id = survey_id
            self.survey_id_display.setText(str(survey_id))
            self.current_survey_label.setText(f"📋 Current Survey: {self.plan_number.text()} (ID: {survey_id})")
            self.update_survey_btn.setEnabled(True)
            
            QMessageBox.information(self, "Success", f"✅ Survey saved with ID: {survey_id}")
            self.load_recent_surveys()
            
        except Exception as e:
            self.db_connection.rollback()
            QMessageBox.critical(self, "Database Error", str(e))

    def update_survey_metadata(self):
        """Update existing survey"""
        if not self.db_available or not self.current_survey_id:
            return
        
        if not self.plan_number.text() or not self.owner_name.text():
            QMessageBox.warning(self, "Validation Error", "Plan Number and Owner Name are required")
            return
        
        reply = QMessageBox.question(self, "Confirm Update", f"Update Survey ID {self.current_survey_id}?", 
                                    QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.No:
            return
        
        try:
            crs_text = self.crs_combo.currentText()
            if crs_text == "Custom EPSG (specify below)":
                crs_value = self.custom_crs.text().strip()
                if crs_value:
                    crs_value = f"EPSG:{crs_value}"
                else:
                    crs_value = None
            else:
                crs_value = crs_text.split(" - ")[0]
            
            cur = self.db_connection.cursor()
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
            
            self.db_connection.commit()
            cur.close()
            
            QMessageBox.information(self, "Success", f"✅ Survey updated successfully!")
            
        except Exception as e:
            self.db_connection.rollback()
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
        self.crs_combo.setCurrentIndex(1)
        self.custom_crs.clear()
        self.current_survey_id = None
        self.current_survey_label.setText("📋 Current Survey: None")
        self.update_survey_btn.setEnabled(False)
        self.refresh_documents_list()

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

    def refresh_search(self):
        """Refresh search results - placeholder for future implementation"""
        pass