# -*- coding: utf-8 -*-
"""
Connection configuration dialog for Survey Management System
"""

import os
import json
import psycopg2
from psycopg2 import sql

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QMessageBox,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, 
    QTextEdit, QTabWidget, QWidget
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsSettings


class ConnectionDialog(QDialog):
    """Dialog for configuring database connection"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Survey Management System - Database Connection")
        self.setMinimumWidth(650)
        self.setMinimumHeight(550)
        
        # Load saved settings (but don't try to set widgets yet)
        self.settings = QgsSettings()
        
        # First create the UI
        self.setup_ui()
        
        # Then load settings into widgets
        self.load_settings()
        
    def setup_ui(self):
        """Setup the user interface"""
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("🔌 DATABASE CONNECTION CONFIGURATION")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)
        
        # Description
        desc = QLabel(
            "Configure the connection to your PostgreSQL/PostGIS database.\n"
            "The plugin will automatically create the required tables if they don't exist."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #7f8c8d; padding: 5px;")
        layout.addWidget(desc)
        
        # Create tab widget for basic/advanced
        tab_widget = QTabWidget()
        
        # ========== BASIC TAB ==========
        basic_tab = QWidget()
        basic_layout = QVBoxLayout()
        
        # Connection settings group
        conn_group = QGroupBox("PostgreSQL Connection Settings")
        conn_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        form_layout = QFormLayout()
        form_layout.setSpacing(10)
        
        # Host
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g., localhost or 192.168.1.100")
        form_layout.addRow("Host:", self.host_edit)
        
        # Port
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(5432)
        form_layout.addRow("Port:", self.port_spin)
        
        # Database name
        self.db_edit = QLineEdit()
        self.db_edit.setPlaceholderText("survey_management")
        form_layout.addRow("Database Name:", self.db_edit)
        
        # Username
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("postgres")
        form_layout.addRow("Username:", self.user_edit)
        
        # Password
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Your password")
        form_layout.addRow("Password:", self.password_edit)
        
        # Save password checkbox
        self.save_password_cb = QCheckBox("Save password (encrypted in QGIS settings)")
        self.save_password_cb.setChecked(True)
        form_layout.addRow("", self.save_password_cb)
        
        conn_group.setLayout(form_layout)
        basic_layout.addWidget(conn_group)
        
        basic_tab.setLayout(basic_layout)
        tab_widget.addTab(basic_tab, "Basic Settings")
        
        # ========== ADVANCED TAB ==========
        advanced_tab = QWidget()
        adv_layout = QVBoxLayout()
        
        adv_group = QGroupBox("Advanced Connection Options")
        adv_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        adv_form = QFormLayout()
        adv_form.setSpacing(10)
        
        # Schema
        self.schema_edit = QLineEdit()
        self.schema_edit.setPlaceholderText("public")
        adv_form.addRow("Schema:", self.schema_edit)
        
        # SSL Mode
        self.ssl_combo = QComboBox()
        self.ssl_combo.addItems(["disable", "allow", "prefer", "require", "verify-ca", "verify-full"])
        adv_form.addRow("SSL Mode:", self.ssl_combo)
        
        # Connection timeout
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 60)
        self.timeout_spin.setValue(10)
        self.timeout_spin.setSuffix(" seconds")
        adv_form.addRow("Timeout:", self.timeout_spin)
        
        adv_group.setLayout(adv_form)
        adv_layout.addWidget(adv_group)
        
        # Connection pool settings
        pool_group = QGroupBox("Connection Pool Settings")
        pool_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        pool_form = QFormLayout()
        
        self.min_connections = QSpinBox()
        self.min_connections.setRange(1, 10)
        self.min_connections.setValue(1)
        pool_form.addRow("Min Connections:", self.min_connections)
        
        self.max_connections = QSpinBox()
        self.max_connections.setRange(1, 50)
        self.max_connections.setValue(10)
        pool_form.addRow("Max Connections:", self.max_connections)
        
        pool_group.setLayout(pool_form)
        adv_layout.addWidget(pool_group)
        
        adv_layout.addStretch()
        advanced_tab.setLayout(adv_layout)
        tab_widget.addTab(advanced_tab, "Advanced Settings")
        
        layout.addWidget(tab_widget)
        
        # Test connection button
        test_btn = QPushButton("🔌 Test Connection")
        test_btn.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px;")
        test_btn.clicked.connect(self.test_connection)
        layout.addWidget(test_btn)
        
        # Status display
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(120)
        self.status_text.setStyleSheet("background-color: #ecf0f1; font-family: monospace;")
        layout.addWidget(self.status_text)
        
        # Button box
        button_layout = QHBoxLayout()
        
        self.save_btn = QPushButton("💾 Save & Connect")
        self.save_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 8px;")
        self.save_btn.clicked.connect(self.save_and_connect)
        button_layout.addWidget(self.save_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 8px;")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
    def load_settings(self):
        """Load saved connection settings into widgets"""
        self.host_edit.setText(self.settings.value("survey_management/host", "localhost"))
        self.port_spin.setValue(int(self.settings.value("survey_management/port", "5432")))
        self.db_edit.setText(self.settings.value("survey_management/database", "survey_management"))
        self.user_edit.setText(self.settings.value("survey_management/user", "postgres"))
        
        # Password is stored encrypted by QGIS
        encrypted_pass = self.settings.value("survey_management/password", "")
        if encrypted_pass:
            self.password_edit.setText(encrypted_pass)
            
        self.schema_edit.setText(self.settings.value("survey_management/schema", "public"))
        self.ssl_combo.setCurrentText(self.settings.value("survey_management/sslmode", "prefer"))
        self.timeout_spin.setValue(int(self.settings.value("survey_management/timeout", "10")))
        
        # Pool settings
        self.min_connections.setValue(int(self.settings.value("survey_management/min_conn", "1")))
        self.max_connections.setValue(int(self.settings.value("survey_management/max_conn", "10")))
        
    def save_settings(self):
        """Save connection settings"""
        self.settings.setValue("survey_management/host", self.host_edit.text())
        self.settings.setValue("survey_management/port", str(self.port_spin.value()))
        self.settings.setValue("survey_management/database", self.db_edit.text())
        self.settings.setValue("survey_management/user", self.user_edit.text())
        self.settings.setValue("survey_management/schema", self.schema_edit.text())
        self.settings.setValue("survey_management/sslmode", self.ssl_combo.currentText())
        self.settings.setValue("survey_management/timeout", str(self.timeout_spin.value()))
        
        # Pool settings
        self.settings.setValue("survey_management/min_conn", str(self.min_connections.value()))
        self.settings.setValue("survey_management/max_conn", str(self.max_connections.value()))
        
        # Save password if requested
        if self.save_password_cb.isChecked():
            self.settings.setValue("survey_management/password", self.password_edit.text())
        else:
            self.settings.remove("survey_management/password")
            
        self.settings.sync()
        
    def get_connection_params(self):
        """Get connection parameters dictionary"""
        params = {
            "host": self.host_edit.text(),
            "port": self.port_spin.value(),
            "database": self.db_edit.text(),
            "user": self.user_edit.text(),
            "password": self.password_edit.text(),
            "connect_timeout": self.timeout_spin.value(),
            "sslmode": self.ssl_combo.currentText()
        }
        return params
        
    def test_connection(self):
        """Test the database connection"""
        self.status_text.clear()
        self.status_text.append("🔄 Testing connection...")
        self.status_text.append(f"Host: {self.host_edit.text()}:{self.port_spin.value()}")
        self.status_text.append(f"Database: {self.db_edit.text()}")
        self.status_text.append(f"User: {self.user_edit.text()}")
        
        try:
            params = self.get_connection_params()
            conn = psycopg2.connect(**params)
            
            # Get PostgreSQL version
            cur = conn.cursor()
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
            
            # Check PostGIS
            cur.execute("SELECT PostGIS_Version()")
            postgis_version = cur.fetchone()[0]
            
            # Check if our tables exist
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            tables = cur.fetchall()
            
            cur.close()
            conn.close()
            
            self.status_text.append("\n✅ CONNECTION SUCCESSFUL!")
            self.status_text.append(f"📊 PostgreSQL: {version[:50]}...")
            self.status_text.append(f"🗺️ PostGIS: {postgis_version}")
            
            if tables:
                self.status_text.append(f"\n📋 Existing tables: {len(tables)} found")
                for table in tables[:5]:  # Show first 5
                    self.status_text.append(f"  • {table[0]}")
                if len(tables) > 5:
                    self.status_text.append(f"  ... and {len(tables)-5} more")
            else:
                self.status_text.append("\n📋 No tables found - will be created on first use")
            
        except psycopg2.OperationalError as e:
            self.status_text.append(f"\n❌ Connection failed: {str(e)}")
            self.status_text.append("\nTroubleshooting tips:")
            self.status_text.append("• Is PostgreSQL running?")
            self.status_text.append("• Are host/port correct?")
            self.status_text.append("• Is username/password valid?")
        except Exception as e:
            self.status_text.append(f"\n❌ Error: {str(e)}")
            
    def save_and_connect(self):
        """Save settings and accept dialog"""
        self.save_settings()
        self.accept()


class DatabaseManager:
    """Manages database connections and setup"""
    
    def __init__(self):
        self.settings = QgsSettings()
        self.connection = None
        
    def get_connection_from_settings(self):
        """Create connection using saved settings"""
        try:
            params = {
                "host": self.settings.value("survey_management/host", "localhost"),
                "port": int(self.settings.value("survey_management/port", "5432")),
                "database": self.settings.value("survey_management/database", "survey_management"),
                "user": self.settings.value("survey_management/user", "postgres"),
                "password": self.settings.value("survey_management/password", ""),
                "connect_timeout": int(self.settings.value("survey_management/timeout", "10")),
                "sslmode": self.settings.value("survey_management/sslmode", "prefer")
            }
            
            self.connection = psycopg2.connect(**params)
            return self.connection
            
        except Exception as e:
            print(f"Connection error: {e}")
            return None
            
    def ensure_database_exists(self):
        """Check if database exists, create if not"""
        try:
            # Connect to default postgres database first
            conn = psycopg2.connect(
                host=self.settings.value("survey_management/host", "localhost"),
                port=int(self.settings.value("survey_management/port", "5432")),
                database="postgres",
                user=self.settings.value("survey_management/user", "postgres"),
                password=self.settings.value("survey_management/password", ""),
                connect_timeout=5
            )
            conn.autocommit = True
            cur = conn.cursor()
            
            # Check if our database exists
            db_name = self.settings.value("survey_management/database", "survey_management")
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            exists = cur.fetchone()
            
            if not exists:
                # Create database
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                print(f"✅ Created database: {db_name}")
                
                # Connect to new database and create extensions
                cur.close()
                conn.close()
                
                conn = psycopg2.connect(
                    host=self.settings.value("survey_management/host", "localhost"),
                    port=int(self.settings.value("survey_management/port", "5432")),
                    database=db_name,
                    user=self.settings.value("survey_management/user", "postgres"),
                    password=self.settings.value("survey_management/password", "")
                )
                conn.autocommit = True
                cur = conn.cursor()
                
                # Create PostGIS extension
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                print("✅ Created PostGIS extension")
                
            cur.close()
            conn.close()
            return True
            
        except Exception as e:
            print(f"❌ Error ensuring database exists: {e}")
            return False
            
    def create_tables(self):
        """Create all required tables if they don't exist using the updated schema"""
        if not self.connection:
            self.connection = self.get_connection_from_settings()
            
        if not self.connection:
            return False
            
        try:
            cur = self.connection.cursor()
            
            # ==================== SURVEYS TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS surveys (
                    survey_id SERIAL PRIMARY KEY,
                    plan_number VARCHAR(50) NOT NULL UNIQUE,
                    owner_name VARCHAR(200),
                    survey_date DATE,
                    original_crs VARCHAR(100),
                    surveyor_name VARCHAR(100),
                    local_government VARCHAR(100),
                    state VARCHAR(50),
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by VARCHAR(50),
                    is_archived BOOLEAN DEFAULT FALSE,
                    file_path TEXT,
                    pdf_path TEXT,
                    description_id INTEGER
                )
            """)
            print("✅ Created/verified surveys table")
            
            # ==================== SURVEY POINTS TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_points (
                    point_id SERIAL PRIMARY KEY,
                    survey_id INTEGER REFERENCES surveys(survey_id) ON DELETE CASCADE,
                    point_number INTEGER,
                    geometry GEOMETRY(POINT),
                    raw_coordinates TEXT,
                    raw_crs VARCHAR(100),
                    notes TEXT,
                    description VARCHAR(50)
                )
            """)
            print("✅ Created/verified survey_points table")
            
            # Create spatial index
            cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_points_geometry ON survey_points USING GIST(geometry)")
            
            # ==================== SURVEY BOUNDARIES TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_boundaries (
                    boundary_id SERIAL PRIMARY KEY,
                    survey_id INTEGER UNIQUE REFERENCES surveys(survey_id) ON DELETE CASCADE,
                    geometry GEOMETRY(POLYGON),
                    calculated_area_sqm NUMERIC(15,2),
                    calculated_area_hectares NUMERIC(15,4),
                    verified BOOLEAN DEFAULT FALSE
                )
            """)
            print("✅ Created/verified survey_boundaries table")
            
            # Create spatial index
            cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_boundaries_geometry ON survey_boundaries USING GIST(geometry)")
            
            # ==================== SURVEY TRAVERSES TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_traverses (
                    traverse_id SERIAL PRIMARY KEY,
                    survey_id INTEGER REFERENCES surveys(survey_id) ON DELETE CASCADE,
                    traverse_name VARCHAR(100),
                    start_point_id INTEGER REFERENCES survey_points(point_id),
                    is_closed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("✅ Created/verified survey_traverses table")
            
            # ==================== TRAVERSE LEGS TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS traverse_legs (
                    leg_id SERIAL PRIMARY KEY,
                    traverse_id INTEGER REFERENCES survey_traverses(traverse_id) ON DELETE CASCADE,
                    leg_number INTEGER NOT NULL,
                    from_point_id INTEGER REFERENCES survey_points(point_id),
                    bearing_decimal DECIMAL(10,6),
                    distance_meters DECIMAL(15,3),
                    bearing_raw VARCHAR(50),
                    distance_raw VARCHAR(50),
                    geometry GEOMETRY(LINESTRING),
                    notes TEXT,
                    UNIQUE(traverse_id, leg_number)
                )
            """)
            print("✅ Created/verified traverse_legs table")
            
            # ==================== SURVEY DOCUMENTS TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_documents (
                    document_id SERIAL PRIMARY KEY,
                    survey_id INTEGER REFERENCES surveys(survey_id) ON DELETE CASCADE,
                    pdf_path TEXT NOT NULL,
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
            print("✅ Created/verified survey_documents table")
            
            self.connection.commit()
            cur.close()
            
            print("\n✅ All tables created/verified successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Error creating tables: {e}")
            self.connection.rollback()
            return False
            
    def get_table_info(self):
        """Get information about existing tables"""
        if not self.connection:
            self.connection = self.get_connection_from_settings()
            
        if not self.connection:
            return None
            
        try:
            cur = self.connection.cursor()
            
            # Get list of tables
            cur.execute("""
                SELECT table_name, 
                       (SELECT COUNT(*) FROM information_schema.columns WHERE table_name=t.table_name) as column_count
                FROM information_schema.tables t
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            
            tables = cur.fetchall()
            
            # Get row counts
            table_info = []
            for table in tables:
                table_name, col_count = table
                cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_count = cur.fetchone()[0]
                table_info.append({
                    'name': table_name,
                    'columns': col_count,
                    'rows': row_count
                })
            
            cur.close()
            return table_info
            
        except Exception as e:
            print(f"Error getting table info: {e}")
            return None