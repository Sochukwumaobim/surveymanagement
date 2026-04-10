# Survey Management System - QGIS Plugin

[![QGIS Plugin](https://img.shields.io/badge/QGIS-Plugin-3c9e3c?logo=qgis)](https://plugins.qgis.org/plugins/surveymanagement/)
[![License: GPL v2](https://img.shields.io/badge/License-GPL%20v2-blue.svg)](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)
[![QGIS Version](https://img.shields.io/badge/QGIS-3.28%2B-brightgreen)](https://qgis.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-12%2B-336791?logo=postgresql)](https://www.postgresql.org)
[![PostGIS](https://img.shields.io/badge/PostGIS-3.0%2B-1f5c2e?logo=postgresql)](https://postgis.net)

> **A complete digital archiving solution for Nigerian survey records**

---

## 📺 **Video Tutorials**

### 🎬 **Complete Plugin Walkthrough** (10 min)
[![Watch the video]([https://img.youtube.com/vi/YOUR_VIDEO_ID/maxresdefault.jpg)](https://youtu.be/YOUR_VIDEO_ID](https://www.youtube.com/watch?v=FbWThtaPlUI))
*Click image to watch on YouTube*


---

## 📋 **Table of Contents**

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
- [Quick Start Guide](#quick-start-guide)
- [Nigerian Surveying Standards](#nigerian-surveying-standards)
- [Database Schema](#database-schema)
- [Document Management](#document-management)
- [Search Capabilities](#search-capabilities)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Support](#support)

---

## 📖 **Overview**

The **Survey Management System** is a powerful QGIS plugin designed specifically for Nigerian surveyors to digitize, manage, and preserve survey records for 50+ years. It transforms traditional paper-based filing systems into a robust digital database integrated with QGIS and PostgreSQL/PostGIS.

### **Why This Plugin?**

| Challenge | Solution |
|-----------|----------|
| 📁 Paper files deteriorate over time | 🗄️ Digital archive with 50-year durability |
| ⏰ Hours searching for old surveys | 🔍 Find any survey in seconds |
| 🗺️ Can't visualize where surveys are | 📍 Plot directly on QGIS map |
| 📄 Scanned documents get lost | ✅ MD5 checksum verification |
| 🔄 Multiple coordinate systems | 🇳🇬 Nigerian CRS presets |
| 📐 Complex bearing calculations | 🧮 Automatic traverse calculator |

---

## ✨ **Features**

### 📋 **Survey Metadata Management**
- Store plan numbers, owner names, survey dates, and surveyor details
- Track location by LGA and State (all 36 Nigerian states + FCT)
- Add notes and descriptions
- Automatic timestamp and audit trail

### 📍 **Coordinate Input**
- Direct Easting/Northing entry
- Plot points directly on QGIS map
- Automatic boundary polygon creation
- Area calculation using shoelace formula

### 📐 **Bearing & Distance Traverse**
- Support for Whole Circle Bearings (0-360°) - Nigerian standard
- Quadrant bearing format (NE, SE, SW, NW)
- DMS input (Degrees/Minutes/Seconds) - No degree symbol needed!
- Three input methods: Spin boxes, Text with shortcuts, Decimal degrees
- Real-time coordinate calculation
- Closing error computation
- Plot traverse directly on map

### 📄 **Document Management**
- Upload multiple documents per survey
- MD5 checksum verification for file integrity
- Primary document flag
- Last-verified date tracking
- Open documents directly from plugin
- Supports PDF, images, and any file type

### 🔍 **Search Capabilities**
- **Basic Search**: Search across all fields with sorting
- **Advanced Search**: Multiple criteria (plan, owner, surveyor, LGA, state, date range)
- **Global Search**: Search across ALL database tables (surveys, points, documents, traverses)
- **Quick Filters**: Recent surveys, no documents, Lagos State, FCT Abuja
- Export results to CSV

### 🗄️ **PostgreSQL/PostGIS Integration**
- Automatic database creation
- Automatic table creation (6 tables)
- SRID preservation for all geometries
- Load spatial tables directly as QGIS layers
- Browse all database tables with preview
- Background loading - no UI freeze

### 🇳🇬 **Nigerian-Specific Features**
- **CRS Presets**:
  - EPSG:26331 - Minna / Nigeria West
  - EPSG:26332 - Minna / Nigeria Mid Belt (Default)
  - EPSG:26333 - Minna / Nigeria East
  - EPSG:32631 - WGS 84 / UTM zone 31N
  - EPSG:32632 - WGS 84 / UTM zone 32N
- All 36 Nigerian states + FCT in dropdown
- Local Government Area field
- Custom EPSG input

### 🪟 **User Experience**
- **Non-Modal Window** - Work in QGIS while plugin stays open
- Minimize to system tray
- Always-on-top option
- Status bar messages (no annoying popups)
- Progress indicators for long operations

---

## 📥 **Installation**

### **Prerequisites**

| Software | Version | Purpose |
|----------|---------|---------|
| QGIS | 3.28+ | GIS platform |
| PostgreSQL | 12+ | Database |
| PostGIS | 3.0+ | Spatial extension |

### **Option 1: Install from QGIS Plugin Repository (Recommended)**

1. Open QGIS
2. Go to **Plugins → Manage and Install Plugins**
3. Search for **"Survey Management System"**
4. Click **Install Plugin**
5. Click the plugin icon 🏛️ in the toolbar

### **Option 2: Install from ZIP**

1. Download the latest release from [GitHub Releases](https://github.com/Sochukwumaobim/surveymanagement/releases)
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded ZIP file
4. Click **Install Plugin**

### **First-Time Database Setup**

The plugin will automatically:

1. Prompt for PostgreSQL connection details on first run
2. Create the `survey_management` database if it doesn't exist
3. Enable PostGIS extension
4. Create all 6 required tables
5. Save your connection settings (encrypted)

**What you need to enter:**
- **Host**: `localhost` (or your database server IP)
- **Port**: `5432` (default PostgreSQL port)
- **Username**: `postgres` (or your PostgreSQL username)
- **Password**: Your PostgreSQL password

> 💡 **Tip**: Use the `postgres` superuser for automatic setup. Your password is encrypted by QGIS.

---

## 🚀 **Quick Start Guide**

### **1. Create Your First Survey**

1. Click the plugin icon 🏛️
2. Select your CRS (default EPSG:26332 works for most of Nigeria)
3. Go to **Survey Metadata** tab
4. Enter Plan Number and Owner Name (required)
5. Fill in other details as needed
6. Click **"Save New Survey"**

### **2. Add Coordinates (Method A - Direct Entry)**

1. Go to **Coordinate Input** tab
2. Enter Easting and Northing for each point
3. Add descriptions if desired
4. Click **"Add Point"** for each point
5. Click **"Plot Points"** to verify on map
6. Click **"Save to PostGIS"**

### **3. Add Traverse (Method B - Bearing & Distance)**

1. Go to **Bearing/Distance** tab
2. Enter starting point coordinates
3. Choose bearing input method (Spin boxes, Text, or Decimal)
4. Enter bearing (e.g., `45d30m15s` or `45.5042`)
5. Enter distance in meters
6. Click **"Add Leg"** for each leg
7. Click **"Calculate"** to verify
8. Click **"Plot on Map"** to visualize
9. Click **"Save to Database"**

### **4. Upload Documents**

1. Go to **Documents** tab
2. Click **"Browse"** to select your PDF or image
3. Add a description
4. Check **"Set as primary document"** if this is the main survey plan
5. Click **"Upload Document"**

### **5. Find an Existing Survey**

**Basic Search:**
1. Go to **Survey Metadata** tab
2. Enter search term (plan number, owner, etc.)
3. Click **"Search"**
4. Double-click any result to load

**Global Search (across all tables):**
1. Go to **Global Search** tab
2. Enter search term
3. Select which tables to search
4. Click **"Search All Tables"**

---

## 🇳🇬 **Nigerian Surveying Standards**

### **Coordinate Reference Systems**

| Region | EPSG Code | Name |
|--------|-----------|------|
| Nationwide (Default) | **26332** | Minna / Nigeria Mid Belt |
| Western Nigeria | 26331 | Minna / Nigeria West |
| Eastern Nigeria | 26333 | Minna / Nigeria East |
| GPS Devices | 4326 | WGS 84 |
| UTM Zone 31N | 32631 | WGS 84 / UTM zone 31N |
| UTM Zone 32N | 32632 | WGS 84 / UTM zone 32N |

### **Bearing Formats**

**Whole Circle Bearing (WCB)** - Standard Nigerian practice:
- 0° = North
- 90° = East
- 180° = South
- 270° = West

**Quadrant Format** (also supported):
- N45°30'E = 45°30'
- S45°30'E = 134°30'
- S45°30'W = 225°30'
- N45°30'W = 314°30'

### **Text Entry Shortcuts for Bearings**

| You Type | Means |
|----------|-------|
| `45d30m15s` | 45°30'15" |
| `45°30'15"` | 45°30'15" |
| `45.5042` | 45.5042° |
| `N45d30mE` | North 45°30' East |
| `S30d15mW` | South 30°15' West |

> 💡 Just type `d` for degrees, `m` for minutes, `s` for seconds - no degree symbol needed!

---

## 🗄️ **Database Schema**

The plugin creates 6 tables in your `survey_management` database:

### **surveys**
Main survey registry with metadata (plan numbers, owners, dates, locations)

### **survey_points**
Individual survey points with geometry, descriptions, and raw coordinates

### **survey_boundaries**
Plot boundaries as polygons with calculated area

### **survey_documents**
Document management with file paths, checksums, and verification dates

### **survey_traverses**
Traverse information linking to start points

### **traverse_legs**
Individual traverse legs with bearings, distances, and geometry

---

## 📄 **Document Management**

### **File Integrity Verification**

Every document uploaded gets an MD5 checksum (digital fingerprint). The plugin can verify files haven't been tampered with or corrupted.

**To verify documents:**
1. Load a survey
2. Go to **Documents** tab
3. Click **"Verify All"** or verify individual documents

### **Primary Documents**

Mark one document per survey as primary (e.g., the main survey plan). Primary documents are highlighted in the list.

### **File Storage**

Documents are stored on your file system (local or network). The plugin stores file paths in the database, not the files themselves.

---

## 🔍 **Search Capabilities**

### **What You Can Search For**

| Table | Fields |
|-------|--------|
| **Surveys** | Plan Number, Owner Name, Surveyor, LGA, State, Notes, Survey ID |
| **Points** | Description, Notes, Raw Coordinates, Point Number |
| **Documents** | File Name, Description, File Path |
| **Boundaries** | Verified status |
| **Traverses** | Traverse Name, Notes |

### **Search Examples**

- Find all surveys by `Chief Okonkwo`
- Find all points with `Boundary` in description
- Find all documents containing `survey plan`
- Find all surveys in `Lagos` state from `2024`

---

## 🔧 **Troubleshooting**

### **Common Issues**

| Issue | Solution |
|-------|----------|
| **"psycopg2 not installed"** | Run in OSGeo4W Shell: `python -m pip install psycopg2-binary` |
| **"Cannot connect to database"** | Check PostgreSQL is running, verify credentials |
| **"Permission denied to create database"** | Use `postgres` superuser or grant CREATE DATABASE permission |
| **"PostGIS not installed"** | Install PostGIS via Stack Builder (Windows) or `sudo apt install postgis` |
| **Nothing plots on map** | Check CRS matches your data, right-click layer → Zoom to Layer |
| **Bearing parse error** | Use format like `45d30m15s` or decimal degrees |

### **Getting Help**

1. Check the **QGIS Python Console** for detailed error messages
2. Visit the [GitHub Issues](https://github.com/Sochukwumaobim/surveymanagement/issues)
3. Email support: ugwusochukwuma@gmail.com

---

## 🤝 **Contributing**

Contributions are welcome! Here's how you can help:

1. **Report bugs** - Open an issue on GitHub
2. **Suggest features** - Tell us what you need
3. **Improve documentation** - Fix typos, add examples
4. **Translate** - Add translations for Hausa, Yoruba, Igbo
5. **Code contributions** - Submit pull requests

### **Development Setup**


```bash
# Clone the repository
git clone https://github.com/Sochukwumaobim/surveymanagement.git

# Install development dependencies
pip install qgis-plugin-ci

# Package for testing
qgis-plugin-ci package

# Reload plugin in QGIS using Plugin Reloader
```

### **📜 License**

This plugin is released under the GNU General Public License v2 or later.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or any later version.

---
###🙏 Acknowledgments
Nigerian Institution of Surveyors for guidance

Testers who provided valuable feedback

QGIS community for excellent documentation

PostgreSQL/PostGIS team for spatial database

---
###📞 Support
Contact	Information

Email	ugwusochukwuma@gmail.com

GitHub Issues	Report Bug

QGIS Repository	plugins.qgis.org/plugins/surveymanagement

Author	ASTROMAT GEO-SERVICES

---
###🌟 Star the Project
If you find this plugin useful, please:

⭐ Star it on GitHub

📝 Leave a review on the QGIS plugin repository

🔄 Share with fellow surveyors

---
```
"Preserving Nigeria's surveying heritage, one coordinate at a time." 🇳🇬
