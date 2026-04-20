# Survey Management System — QGIS Plugin for Nigerian Cadastral Records

> A published QGIS plugin for digitising, archiving, and managing Nigerian land survey records — with AutoCAD DXF import, AI-powered metadata extraction, PostGIS spatial storage, traverse calculations, and three-tier access control.

[![QGIS Plugin](https://img.shields.io/badge/QGIS-Plugin-3c9e3c?logo=qgis)](https://plugins.qgis.org/plugins/surveymanagement/)
[![Version](https://img.shields.io/badge/Version-1.2.0-blue)](https://github.com/Sochukwumaobim/surveymanagement/releases)
[![License: GPL v2](https://img.shields.io/badge/License-GPL%20v2-blue.svg)](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html)
[![QGIS Version](https://img.shields.io/badge/QGIS-3.28%2B-brightgreen)](https://qgis.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-12%2B-336791?logo=postgresql)](https://www.postgresql.org)
[![PostGIS](https://img.shields.io/badge/PostGIS-3.0%2B-1f5c2e)](https://postgis.net)

---

## 🏛️ Overview

The **Survey Management System** is a full-featured QGIS plugin built specifically for **Nigerian surveyors and cadastral offices** to replace paper-based filing systems with a robust, spatially-aware digital archive. It connects QGIS directly to a PostgreSQL/PostGIS database, enabling surveyors to store, retrieve, visualise, and verify land survey records — with 50-year archival durability and full audit trails.

Now in **version 1.2.0**, the plugin adds AutoCAD DXF/DWG import with AI-powered plan metadata extraction, making it possible to onboard legacy paper plans at scale.

### 🗺️ The Problem It Solves

Land survey records in Nigeria are overwhelmingly paper-based. Files deteriorate, get lost, or become impossible to search across thousands of records. There is no standard digital system for linking survey plans to spatial geometries, LGA boundaries, or adjoining plots. This plugin provides a complete, open-source solution built on tools surveyors already use — QGIS and PostgreSQL.

---

## ✨ What's New in v1.2.0 (April 2026)

- 🆕 **AutoCAD DXF/DWG Import** — extract beacon coordinates, traverse legs, bearings, and distances directly from AutoCAD plan files
- 🆕 **AI-Powered Metadata Extraction** — automatically reads plan number, owner name, surveyor, LGA, state, and survey date from DXF files via a hosted inference server (no API key needed)
- 🆕 **DXF Import Preview Dialog** — review and confirm all extracted data before committing to the database
- 🆕 **First-Run Setup Wizard** — installs `psycopg2` and creates the database automatically; no command line required
- 🆕 **User Login & Three-Tier Access Control** — superuser, surveyor, and viewer roles
- 🆕 **Full Audit Trail** — every action logged to `audit_log` with timestamp and user
- 🆕 **User Administration Panel** — manage users and roles from within the plugin
- 🆕 **Traverse Precision Ratio** — automatic 1:5000 closing error check per Nigerian Survey Regulations
- 🆕 **Adjoining Survey Detection** — PostGIS `ST_DWithin` flags neighbouring plots automatically
- 🆕 **Live EPSG Validation** — custom CRS input with real-time validation
- 🐛 Fixed traverse delete button index bug
- 🐛 Fixed Documents tab index using `indexOf()`

---

## 🌟 Key Features

### 📐 Survey Data Entry & Traverse Calculation
- Enter survey points by direct Easting/Northing or via **Bearing & Distance traverse**
- Support for **Whole Circle Bearings (WCB)** — the Nigerian surveying standard
- Quadrant bearings (N45°30'E format) and **DMS text shortcuts** (`45d30m15s`)
- Real-time traverse coordinate calculation and closing error computation
- Automatic **1:5000 precision ratio check** per Nigerian Survey Regulations
- Plot traverses directly on the QGIS map canvas

### 🏗️ AutoCAD DXF Import with AI Extraction
- Import `.dxf` and `.dwg` plan files from AutoCAD or compatible CAD software
- AI automatically extracts plan number, owner name, surveyor, LGA, state, and survey date
- Preview dialog to review, edit, and confirm before database insertion
- Eliminates manual data entry for legacy plan digitisation workflows

### 🗄️ PostgreSQL/PostGIS Spatial Database
- Automatic database creation and schema initialisation (8 tables)
- All geometries stored with SRID preservation via `geoalchemy2`
- `ST_DWithin` adjoining survey boundary detection
- `ST_AsGeoJSON` and `ST_Transform` for seamless QGIS layer loading
- Export search results to CSV

### 🔍 Multi-Level Search
- **Basic Search** — find records across all fields (plan number, owner, LGA, state, date range)
- **Advanced Search** — multiple simultaneous criteria with sorting
- **Global Search** — search across all database tables at once
- Quick filters: Recent surveys, no documents, Lagos State, FCT Abuja

### 📄 Document Management & File Integrity
- Attach PDFs, scanned images, and any file type to survey records
- **MD5 checksum verification** — detect file corruption or tampering
- Primary document flag per survey
- Last-verified date tracking and bulk verification

### 🔐 Access Control & Audit Trail
- Three roles: **Superuser**, **Surveyor**, **Viewer**
- Full audit log of every insert, update, and delete action with timestamps
- User administration panel for superusers
- Default credentials: `admin` / `admin123` (change on first login)

### 🇳🇬 Nigerian-Specific Configuration
- **CRS Presets** built in:
  - EPSG:26331 — Minna / Nigeria West
  - EPSG:26332 — Minna / Nigeria Mid Belt *(default for most of Nigeria)*
  - EPSG:26333 — Minna / Nigeria East
  - EPSG:4326 — WGS 84 (GPS)
  - EPSG:32631 / 32632 — UTM Zone 31N / 32N
- All **36 Nigerian states + FCT** in dropdown
- **Local Government Area (LGA)** field on every record
- Designed around **NIS (Nigerian Institution of Surveyors)** workflow standards

---

## 📥 Installation

### Requirements

| Software | Minimum Version | Purpose |
|----------|----------------|---------|
| [QGIS](https://qgis.org/en/site/forusers/download.html) | 3.28 | GIS platform |
| [PostgreSQL](https://www.postgresql.org/download/) | 12 | Spatial database |
| [PostGIS](https://postgis.net/install/) | 3.0 | Geometry storage & queries |

### Option 1: Install from QGIS Plugin Repository *(Recommended)*

1. Open QGIS
2. Go to **Plugins → Manage and Install Plugins**
3. Search for **`Survey Management System`**
4. Click **Install Plugin**
5. The 🏛️ icon will appear in your QGIS toolbar

### Option 2: Install from ZIP

1. Download the latest ZIP from [GitHub Releases](https://github.com/Sochukwumaobim/surveymanagement/releases)
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**
3. Select the downloaded file and click **Install Plugin**

### First Run

On first launch the **Setup Wizard** will:
1. Automatically install `psycopg2` if missing (no terminal needed)
2. Prompt for PostgreSQL connection details
3. Create the `survey_management` database
4. Enable the PostGIS extension
5. Create all required spatial tables
6. Encrypt and save your connection credentials

> **Default login:** username `admin`, password `admin123` — change this immediately via the User Administration panel.

---

## 🚀 Quick Start

### Create a Survey Record

1. Click the 🏛️ plugin icon in the QGIS toolbar
2. Log in with your credentials
3. Open the **Survey Metadata** tab
4. Enter Plan Number and Owner Name (required fields)
5. Select State, LGA, and Survey Date
6. Click **Save New Survey**

### Import from AutoCAD DXF

1. Go to the **DXF Import** tab
2. Click **Browse** and select your `.dxf` or `.dwg` file
3. The AI engine extracts plan metadata automatically
4. Review the **Import Preview** dialog — edit any fields as needed
5. Click **Confirm Import** — coordinates, traverse legs, and metadata are saved

### Add a Traverse Manually

1. Go to the **Bearing/Distance** tab
2. Enter the starting point coordinates (Easting, Northing)
3. Select bearing format: **Spin boxes**, **Text shortcuts**, or **Decimal degrees**
4. Enter each leg: bearing (e.g. `45d30m15s`) and distance in metres
5. Click **Add Leg** after each entry
6. Click **Calculate** — the 1:5000 precision ratio is checked automatically
7. Click **Plot on Map** to verify, then **Save to Database**

### Search for a Record

- **Basic:** Enter any text → click **Search**
- **Advanced:** Use multi-field queries with date ranges
- **Global:** Search across surveys, points, documents, and traverses simultaneously
- Double-click any result to load the full record

---

## 🗄️ Database Schema

The plugin creates and manages the following tables in the `survey_management` database:

| Table | Contents |
|-------|----------|
| `surveys` | Plan numbers, owner names, surveyors, LGA, state, survey dates |
| `survey_points` | Individual beacon coordinates with PostGIS geometry |
| `survey_boundaries` | Polygon boundaries with calculated area (shoelace formula) |
| `survey_documents` | File paths, MD5 checksums, primary document flags |
| `survey_traverses` | Traverse records with precision ratio and closing error |
| `traverse_legs` | Individual legs with bearings, distances, and geometry |
| `audit_log` | Full action audit trail with timestamps and user IDs |
| `users` | User accounts with hashed passwords and role assignments |

---

## 🇳🇬 Nigerian Surveying Standards Reference

### Coordinate Reference Systems

| Region | EPSG | Name |
|--------|------|------|
| Nationwide *(default)* | **26332** | Minna / Nigeria Mid Belt |
| Western Nigeria | 26331 | Minna / Nigeria West |
| Eastern Nigeria | 26333 | Minna / Nigeria East |
| GPS / GIS general | 4326 | WGS 84 |
| UTM Zone 31N | 32631 | WGS 84 / UTM zone 31N |
| UTM Zone 32N | 32632 | WGS 84 / UTM zone 32N |

### Bearing Formats Supported

| You Type | Parsed As |
|----------|-----------|
| `45d30m15s` | 45°30'15" |
| `45°30'15"` | 45°30'15" |
| `45.5042` | 45.5042° decimal |
| `N45d30mE` | North 45°30' East (quadrant) |
| `S30d15mW` | South 30°15' West (quadrant) |

**Precision standard:** 1:5000 closing error ratio per Nigerian Survey Regulations (NIS).

---

## 🔧 Troubleshooting

| Problem | Fix |
|---------|-----|
| `psycopg2 not installed` | Run the Setup Wizard — it installs this automatically. Or: `python -m pip install psycopg2-binary` in OSGeo4W Shell |
| Cannot connect to database | Confirm PostgreSQL is running; verify host, port, username, and password |
| Permission denied creating database | Use the `postgres` superuser, or grant `CREATEDB` to your user |
| PostGIS not available | Windows: install via Stack Builder. Linux: `sudo apt install postgis` |
| Nothing plots on map | Check CRS matches your data; right-click layer → Zoom to Layer |
| Bearing parse error | Use format `45d30m15s` or decimal degrees |
| DXF import finds no data | Ensure the file uses standard AutoCAD layer names; contact support for non-standard plans |

For detailed errors, open the **QGIS Python Console** (Plugins → Python Console).

---

## 📚 Documentation & Resources

- 📖 [Full User Manual (Google Docs)](https://docs.google.com/document/d/1PZ1Gvf_fzbpQL_uH3iHdavKOnUAtGRFA/edit?usp=drive_link)
- 🎬 [Video Walkthrough on YouTube](https://www.youtube.com/watch?v=FbWThtaPlUI)
- 🐛 [Report a Bug on GitHub](https://github.com/Sochukwumaobim/surveymanagement/issues)
- 🔌 [QGIS Plugin Repository Listing](https://plugins.qgis.org/plugins/surveymanagement/)

---

## 🤝 Contributing

Contributions are welcome from the QGIS community and Nigerian GIS practitioners:

- **Report bugs** — open a [GitHub Issue](https://github.com/Sochukwumaobim/surveymanagement/issues)
- **Request features** — describe your surveying workflow requirement
- **Translate** — add Hausa, Yoruba, or Igbo UI translations
- **Improve DXF parsing** — extend support for more CAD plan formats
- **Port to other countries** — Ghana, Kenya, and other nations using WCB traverse standards

```bash
# Development setup
git clone https://github.com/Sochukwumaobim/surveymanagement.git
pip install qgis-plugin-ci
qgis-plugin-ci package        # Build the plugin zip
# Load in QGIS using Plugin Reloader for live development
```

---

## 📜 License

Released under the **GNU General Public License v2 or later (GPL-2.0+)** — free software you may use, modify, and redistribute. See [LICENSE](./LICENSE) for full terms.

---

## 🙏 Acknowledgements

- [Nigerian Institution of Surveyors (NIS)](https://www.nigeriansurveyors.com/) for workflow and standards guidance
- The [QGIS community](https://qgis.org) for an outstanding open-source GIS platform
- [PostgreSQL](https://www.postgresql.org) and [PostGIS](https://postgis.net) teams
- Datamat Nigeria Limited
- All field surveyors who provided feedback during testing

---

## 📞 Support

| Channel | Details |
|---------|---------|
| Email | [ugwusochukwuma@gmail.com](mailto:ugwusochukwuma@gmail.com) |
| GitHub Issues | [github.com/Sochukwumaobim/surveymanagement/issues](https://github.com/Sochukwumaobim/surveymanagement/issues) |
| QGIS Repository | [plugins.qgis.org/plugins/surveymanagement](https://plugins.qgis.org/plugins/surveymanagement/) |
| Author | ASTROMAT GEO-SERVICES |

---

> *"Preserving Nigeria's surveying heritage, one coordinate at a time."* 🇳🇬

---

*Keywords: QGIS plugin Nigeria, Nigerian cadastral survey management, land survey digitisation Nigeria, PostGIS survey records, QGIS Nigeria cadastral, survey plan archive Nigeria, bearing traverse calculator QGIS, Minna coordinate system EPSG 26332, Nigerian land registry GIS, AutoCAD DXF QGIS import, land administration Nigeria open source, survey management system Africa*
