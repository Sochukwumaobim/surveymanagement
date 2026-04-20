# -*- coding: utf-8 -*-
"""
Connection configuration dialog for Survey Management System
COMPLETE AUTO-SETUP VERSION - Creates everything automatically!
"""

import os
import json

# psycopg2 may not be installed yet — wrap gracefully
try:
    import psycopg2
    from psycopg2 import sql
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    psycopg2 = None
    sql = None

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QPushButton, QLabel, QMessageBox,
    QGroupBox, QCheckBox, QComboBox, QSpinBox, 
    QTextEdit, QTabWidget, QWidget, QProgressBar,
    QApplication  # QApplication is in QtWidgets, not QtGui
)
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsSettings


class ConnectionDialog(QDialog):
    """Dialog for configuring database connection with AUTO-CREATION"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Survey Management System - Database Setup")
        self.setMinimumWidth(650)
        self.setMinimumHeight(600)
        
        # Load settings
        self.settings = QgsSettings()
        
        # Create UI
        self.setup_ui()
        
        # Load settings into widgets
        self.load_settings()
        
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("🚀 SURVEY MANAGEMENT SYSTEM - AUTO DATABASE SETUP")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #27ae60;")
        layout.addWidget(title)
        
        # Description
        desc = QLabel(
            "This plugin will AUTOMATICALLY create everything for you:\n"
            "• Database 'survey_management' (if missing)\n"
            "• PostGIS extension\n"
            "• All required tables (surveys, points, boundaries, documents, etc.)\n\n"
            "Just enter your PostgreSQL credentials below and click 'Auto-Setup Database'."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("background-color: #ecf0f1; padding: 10px; border-radius: 5px;")
        layout.addWidget(desc)
        
        # Connection settings group
        conn_group = QGroupBox("PostgreSQL Connection Settings")
        conn_group.setStyleSheet("QGroupBox { font-weight: bold; }")
        form_layout = QFormLayout()
        form_layout.setSpacing(10)
        
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g., localhost or 192.168.1.100")
        form_layout.addRow("Host:", self.host_edit)
        
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(5432)
        form_layout.addRow("Port:", self.port_spin)
        
        self.db_edit = QLineEdit()
        self.db_edit.setText("survey_management")
        self.db_edit.setEnabled(False)  # Fixed database name
        self.db_edit.setStyleSheet("background-color: #f0f0f0;")
        form_layout.addRow("Database to Create:", self.db_edit)
        
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("postgres")
        form_layout.addRow("Username:", self.user_edit)
        
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Your password")
        form_layout.addRow("Password:", self.password_edit)
        
        self.save_password_cb = QCheckBox("Save password (encrypted)")
        self.save_password_cb.setChecked(True)
        form_layout.addRow("", self.save_password_cb)
        
        conn_group.setLayout(form_layout)
        layout.addWidget(conn_group)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Status text
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(150)
        self.status_text.setStyleSheet("background-color: #ecf0f1; font-family: monospace;")
        layout.addWidget(self.status_text)
        
        # Button layout
        button_layout = QHBoxLayout()
        
        self.auto_setup_btn = QPushButton("🚀 AUTO-SETUP DATABASE")
        self.auto_setup_btn.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 10px; font-size: 12pt;")
        self.auto_setup_btn.clicked.connect(self.auto_setup_database)
        button_layout.addWidget(self.auto_setup_btn)
        
        self.test_btn = QPushButton("🔌 Test Connection Only")
        self.test_btn.setStyleSheet("background-color: #3498db; color: white; padding: 8px;")
        self.test_btn.clicked.connect(self.test_connection)
        button_layout.addWidget(self.test_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet("background-color: #e74c3c; color: white; padding: 8px;")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Note about permissions
        note = QLabel("Note: Your PostgreSQL user needs CREATE DATABASE permissions.\nDefault 'postgres' superuser always works.")
        note.setStyleSheet("color: #7f8c8d; font-style: italic;")
        layout.addWidget(note)
        
        self.setLayout(layout)
        
    def load_settings(self):
        """Load saved settings into widgets"""
        self.host_edit.setText(self.settings.value("survey_management/host", "localhost"))
        self.port_spin.setValue(int(self.settings.value("survey_management/port", "5432")))
        self.user_edit.setText(self.settings.value("survey_management/user", "postgres"))
        
        # Password is stored encrypted by QGIS
        encrypted_pass = self.settings.value("survey_management/password", "")
        if encrypted_pass:
            self.password_edit.setText(encrypted_pass)
        
    def save_settings(self):
        """Save connection settings"""
        self.settings.setValue("survey_management/host", self.host_edit.text())
        self.settings.setValue("survey_management/port", str(self.port_spin.value()))
        self.settings.setValue("survey_management/database", "survey_management")
        self.settings.setValue("survey_management/user", self.user_edit.text())
        
        if self.save_password_cb.isChecked():
            self.settings.setValue("survey_management/password", self.password_edit.text())
        else:
            self.settings.remove("survey_management/password")
            
        self.settings.sync()
        
    def log(self, message):
        """Add message to status log"""
        self.status_text.append(message)
        QApplication.processEvents()
        
    def auto_setup_database(self):
        """COMPLETE AUTO-SETUP: Creates database, enables PostGIS, creates all tables"""
        self.status_text.clear()
        self.auto_setup_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 5)
        
        try:
            # Step 1: Get connection parameters
            host = self.host_edit.text()
            port = self.port_spin.value()
            user = self.user_edit.text()
            password = self.password_edit.text()
            db_name = "survey_management"
            
            self.log("=" * 50)
            self.log("🚀 STARTING AUTO DATABASE SETUP")
            self.log("=" * 50)
            self.log(f"Host: {host}:{port}")
            self.log(f"User: {user}")
            self.log(f"Target Database: {db_name}")
            self.progress_bar.setValue(1)
            
            # Step 2: Connect to default 'postgres' database
            self.log("\n📡 Connecting to PostgreSQL server...")
            try:
                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    database="postgres",
                    user=user,
                    password=password,
                    connect_timeout=10
                )
                conn.autocommit = True
                self.log("✅ Connected to 'postgres' database")
            except psycopg2.OperationalError:
                # Try template1 as fallback
                try:
                    conn = psycopg2.connect(
                        host=host,
                        port=port,
                        database="template1",
                        user=user,
                        password=password,
                        connect_timeout=10
                    )
                    conn.autocommit = True
                    self.log("✅ Connected to 'template1' database")
                except psycopg2.OperationalError as e:
                    self.log(f"❌ Could not connect to PostgreSQL server: {str(e)}")
                    raise Exception("Cannot connect to PostgreSQL server. Make sure it's installed and running.")
            
            self.progress_bar.setValue(2)
            
            # Step 3: Check if our database exists and create if needed
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            exists = cur.fetchone()
            
            if not exists:
                self.log("\n📁 Creating database 'survey_management'...")
                try:
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                    self.log("✅ Database created successfully")
                except Exception as e:
                    self.log(f"❌ Failed to create database: {str(e)}")
                    self.log("\n💡 TIP: Use superuser 'postgres' or ask admin to grant CREATE DATABASE permission")
                    raise Exception(f"Cannot create database: {str(e)}")
            else:
                self.log("\n✅ Database 'survey_management' already exists")
            
            cur.close()
            conn.close()
            self.progress_bar.setValue(3)
            
            # Step 4: Connect to new database and enable PostGIS
            self.log("\n🔌 Connecting to new database...")
            conn = psycopg2.connect(
                host=host,
                port=port,
                database=db_name,
                user=user,
                password=password
            )
            conn.autocommit = True
            cur = conn.cursor()
            self.log("✅ Connected to 'survey_management'")
            
            # Check if PostGIS is available
            self.log("\n🗺️ Checking PostGIS installation...")
            cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
            if not cur.fetchone():
                self.log("❌ PostGIS extension not found on this server")
                self.log("\n💡 Please install PostGIS first:")
                self.log("  • Windows: Run Stack Builder from PostgreSQL start menu")
                self.log("  • Linux: sudo apt install postgis")
                self.log("  • macOS: brew install postgis")
                raise Exception("PostGIS not installed")
            
            self.log("✅ PostGIS is available")
            
            # Enable PostGIS
            self.log("\n🔧 Enabling PostGIS extension...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            self.log("✅ PostGIS enabled successfully")
            
            self.progress_bar.setValue(4)
            
            # Step 5: Create all tables
            self.log("\n📋 Creating all required tables...")
            
            # Surveys table
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
            self.log("  ✅ surveys table created")
            
            # Survey points table
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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_points_geometry ON survey_points USING GIST(geometry)")
            self.log("  ✅ survey_points table created")
            
            # Survey boundaries table
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
            cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_boundaries_geometry ON survey_boundaries USING GIST(geometry)")
            self.log("  ✅ survey_boundaries table created")
            
            # Survey traverses table
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
            self.log("  ✅ survey_traverses table created")
            
            # Traverse legs table
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
            self.log("  ✅ traverse_legs table created")
            
            # Survey documents table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_documents (
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
            self.log("  ✅ survey_documents table created")
            
            cur.close()
            conn.close()
            
            self.progress_bar.setValue(5)
            
            # Step 6: Success!
            self.log("\n" + "=" * 50)
            self.log("✅✅✅ AUTO SETUP COMPLETE! ✅✅✅")
            self.log("=" * 50)
            self.log("\n🎉 All done! Your database is ready:")
            self.log("  • Database: survey_management")
            self.log("  • PostGIS: Enabled")
            self.log("  • Tables: 6 tables created")
            self.log("\n📊 Tables created:")
            self.log("  • surveys")
            self.log("  • survey_points")
            self.log("  • survey_boundaries")
            self.log("  • survey_traverses")
            self.log("  • traverse_legs")
            self.log("  • survey_documents")
            
            # Save settings and accept
            self.save_settings()
            
            QMessageBox.information(
                self, "Success!",
                "✅✅✅ DATABASE SETUP COMPLETE! ✅✅✅\n\n"
                "All done! Your database is ready to use.\n\n"
                "• Database: survey_management\n"
                "• PostGIS: Enabled\n"
                "• Tables: 6 tables created\n\n"
                "Click OK to start using the plugin."
            )
            
            self.accept()
            
        except psycopg2.OperationalError as e:
            self.log(f"\n❌ Connection error: {str(e)}")
            QMessageBox.critical(
                self, "Connection Failed",
                f"❌ Could not connect to PostgreSQL.\n\n"
                f"Error: {str(e)}\n\n"
                f"Make sure:\n"
                f"• PostgreSQL is installed and running\n"
                f"• Host/port are correct\n"
                f"• Username/password are valid"
            )
        except Exception as e:
            self.log(f"\n❌ Setup failed: {str(e)}")
            QMessageBox.critical(
                self, "Setup Failed",
                f"❌ Database setup failed.\n\nError: {str(e)}"
            )
        finally:
            self.auto_setup_btn.setEnabled(True)
            self.test_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
            
    def test_connection(self):
        """Test connection to an EXISTING database"""
        self.status_text.clear()
        self.log("🔄 Testing connection to survey_management...")
        
        try:
            conn = psycopg2.connect(
                host=self.host_edit.text(),
                port=self.port_spin.value(),
                database="survey_management",
                user=self.user_edit.text(),
                password=self.password_edit.text(),
                connect_timeout=5
            )
            
            cur = conn.cursor()
            
            # Check PostGIS
            try:
                cur.execute("SELECT PostGIS_Version()")
                postgis_version = cur.fetchone()[0]
                self.log(f"✅ PostGIS version: {postgis_version}")
            except:
                self.log("⚠️ PostGIS not enabled")
            
            # Check tables
            try:
                cur.execute("SELECT COUNT(*) FROM surveys")
                survey_count = cur.fetchone()[0]
                self.log(f"📊 Surveys in database: {survey_count}")
            except:
                self.log("⚠️ surveys table not found")
            
            cur.close()
            conn.close()
            
            self.log("✅ Connection successful!")
            
        except psycopg2.OperationalError as e:
            self.log(f"❌ Connection failed: {str(e)}")
            self.log("\n💡 Use 'AUTO-SETUP DATABASE' button to create everything automatically!")


class DatabaseManager:
    """Manages database connections and setup with enhanced error handling"""
    
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
        """
        Check if database exists, create if not - with enhanced error handling
        Returns: (success, message)
        """
        try:
            host = self.settings.value("survey_management/host", "localhost")
            port = int(self.settings.value("survey_management/port", "5432"))
            user = self.settings.value("survey_management/user", "postgres")
            password = self.settings.value("survey_management/password", "")
            db_name = self.settings.value("survey_management/database", "survey_management")
            
            # Try to connect to default postgres database first
            try:
                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    database="postgres",
                    user=user,
                    password=password,
                    connect_timeout=5
                )
                conn.autocommit = True
                connected_db = "postgres"
            except psycopg2.OperationalError:
                # If postgres fails, try template1
                try:
                    conn = psycopg2.connect(
                        host=host,
                        port=port,
                        database="template1",
                        user=user,
                        password=password,
                        connect_timeout=5
                    )
                    conn.autocommit = True
                    connected_db = "template1"
                except psycopg2.OperationalError as e:
                    return False, f"Cannot connect to PostgreSQL server.\nError: {str(e)}\n\nMake sure PostgreSQL is installed and running."

            cur = conn.cursor()
            
            # Check if our database exists
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            exists = cur.fetchone()
            
            if not exists:
                # Try to create database
                try:
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
                    print(f"✅ Created database: {db_name}")
                except Exception as e:
                    cur.close()
                    conn.close()
                    return False, f"Cannot create database '{db_name}'.\nError: {str(e)}\n\nPossible solutions:\n• Use a superuser account (postgres)\n• Grant CREATE DATABASE permission to your user\n• Create the database manually"
            
            cur.close()
            conn.close()
            
            # Now connect to the new database and create extensions
            try:
                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    database=db_name,
                    user=user,
                    password=password
                )
                conn.autocommit = True
                cur = conn.cursor()
                
                # Check if PostGIS is available
                cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
                if not cur.fetchone():
                    cur.close()
                    conn.close()
                    return False, "PostGIS extension is not installed on this PostgreSQL server.\n\nPlease install PostGIS first:\n• Windows: Use Stack Builder\n• Linux: sudo apt install postgis\n• macOS: brew install postgis"
                
                # Create PostGIS extension
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
                print("✅ Created/verified PostGIS extension")
                
                cur.close()
                conn.close()
                return True, "Database ready"
                
            except Exception as e:
                return False, f"Connected but cannot create extensions: {str(e)}"
            
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"
            
    def create_tables(self):
        """Create all required tables if they don't exist"""
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
            
            # ==================== SURVEY DOCUMENTS TABLE ====================
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_documents (
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

            # ==================== APP USERS TABLE ====================
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

            # ==================== AUDIT LOG TABLE ====================
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

            self.connection.commit()
            cur.close()

            print("✅ All tables created successfully")
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
