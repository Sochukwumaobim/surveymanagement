# -*- coding: utf-8 -*-
"""
dxf_import_dialog.py  —  Survey Management System v1.2
Preview dialog shown after DXF extraction, before data is loaded into the form.

The user can:
  • See all extracted points, legs, and metadata
  • Choose which polyline to use if multiple found
  • Choose the coordinate layer if multiple point layers exist
  • Edit/remove individual rows before accepting
  • Decide whether to import coordinates, traverse legs, or metadata
"""

import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
    QPushButton, QGroupBox, QCheckBox, QComboBox, QTextEdit,
    QMessageBox, QSplitter, QApplication
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont


class DXFImportDialog(QDialog):
    """
    Shows what was extracted from a DXF file.
    Accepted result contains .accepted_points, .accepted_legs, .accepted_metadata.
    """

    def __init__(self, parent=None, import_result=None, filepath=""):
        super().__init__(parent)
        self.result   = import_result
        self.filepath = filepath

        # What the caller will read after exec_()
        self.accepted_points   = []
        self.accepted_legs     = []
        self.accepted_metadata = {}

        self.setWindowTitle("DXF Import Preview — Survey Management System")
        self.setMinimumWidth(900)
        self.setMinimumHeight(640)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint |
                            Qt.WindowSystemMenuHint | Qt.WindowCloseButtonHint)
        self._setup_ui()
        self._populate()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(10)

        # Header
        fname = os.path.basename(self.filepath)
        hdr = QLabel(f"📐  DXF Import Preview:  {fname}")
        hdr.setStyleSheet("font-size:12pt; font-weight:bold; color:#1A5C38; padding:4px 0;")
        layout.addWidget(hdr)

        # AI badge — show green if ANY metadata was extracted (regex or AI)
        ai_used = getattr(self.result, 'ai_extraction', False) if self.result else False
        has_meta = bool(self.result and self.result.metadata and any(
            k not in ('grid_origin_e', 'grid_origin_n', 'grid_control_beacon')
            and str(v).strip() not in ('', 'null', 'None')
            for k, v in self.result.metadata.items()
        ))

        if ai_used:
            ai_badge = QLabel(
                "🤖  AI extraction active — metadata, bearings, and distances "
                "were extracted by AI. Review all values before accepting."
            )
            ai_badge.setWordWrap(True)
            ai_badge.setStyleSheet(
                "background:#E8F5EE; padding:8px 12px; "
                "border-left:4px solid #1A5C38; font-size:10pt; font-weight:bold;"
            )
            layout.addWidget(ai_badge)
        elif has_meta:
            # Regex extraction got the metadata — green badge, no warning needed
            ok_badge = QLabel(
                "✅  Metadata extracted from plan title block — "
                "plan number, owner, surveyor, date and location read successfully."
            )
            ok_badge.setWordWrap(True)
            ok_badge.setStyleSheet(
                "background:#E8F5EE; padding:8px 12px; "
                "border-left:4px solid #1A5C38; font-size:10pt;"
            )
            layout.addWidget(ok_badge)
        else:
            # Truly nothing extracted — yellow warning
            no_ai_badge = QLabel(
                "ℹ  Metadata could not be read from this file automatically. "
                "Coordinates and traverse legs are extracted. "
                "Fill in the metadata fields manually after accepting."
            )
            no_ai_badge.setWordWrap(True)
            no_ai_badge.setStyleSheet(
                "background:#FFF8E1; padding:8px 12px; "
                "border-left:4px solid #F9A825; font-size:10pt;"
            )
            layout.addWidget(no_ai_badge)

        sub = QLabel(
            "Review the extracted data below. "
            "Un-tick any rows you do not want to import, then click ✅ Accept Import."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#555; font-size:10pt; margin-bottom:4px;")
        layout.addWidget(sub)

        # Summary box
        self.summary_lbl = QLabel("")
        self.summary_lbl.setStyleSheet(
            "background:#E8F5EE; padding:8px; border-left:4px solid #1A5C38; font-family:monospace;"
        )
        self.summary_lbl.setWordWrap(True)
        layout.addWidget(self.summary_lbl)

        # Tabs
        self.tabs = QTabWidget()

        self.tabs.addTab(self._build_points_tab(),   "📍 Coordinates")
        self.tabs.addTab(self._build_traverse_tab(), "📐 Traverse Legs")
        self.tabs.addTab(self._build_metadata_tab(), "📋 Metadata")
        self.tabs.addTab(self._build_layers_tab(),   "🗂 Layers")
        self.tabs.addTab(self._build_warnings_tab(), "⚠ Warnings")

        layout.addWidget(self.tabs)

        # Import options
        opt_group = QGroupBox("Import options")
        opt_group.setStyleSheet("QGroupBox{font-weight:bold;}")
        opt_layout = QHBoxLayout()

        self.import_coords_cb   = QCheckBox("Import coordinates into Coordinate Input tab")
        self.import_coords_cb.setChecked(True)
        opt_layout.addWidget(self.import_coords_cb)

        self.import_traverse_cb = QCheckBox("Import legs into Bearing/Distance tab")
        self.import_traverse_cb.setChecked(True)
        opt_layout.addWidget(self.import_traverse_cb)

        self.import_meta_cb     = QCheckBox("Pre-fill survey metadata fields")
        self.import_meta_cb.setChecked(True)
        opt_layout.addWidget(self.import_meta_cb)

        opt_layout.addStretch()
        opt_group.setLayout(opt_layout)
        layout.addWidget(opt_group)

        # Polyline selector (shown when multiple polylines detected)
        self.poly_group = QGroupBox("Boundary polyline selection")
        self.poly_group.setStyleSheet("QGroupBox{font-weight:bold;}")
        poly_layout = QHBoxLayout()
        poly_layout.addWidget(QLabel("Use polyline:"))
        self.poly_combo = QComboBox()
        poly_layout.addWidget(self.poly_combo)
        poly_layout.addStretch()
        self.poly_group.setLayout(poly_layout)
        self.poly_group.setVisible(False)
        layout.addWidget(self.poly_group)

        # Buttons
        btn_row = QHBoxLayout()

        self.select_all_btn = QPushButton("☑ Select All")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checks(True))
        btn_row.addWidget(self.select_all_btn)

        self.deselect_btn = QPushButton("☐ Deselect All")
        self.deselect_btn.clicked.connect(lambda: self._set_all_checks(False))
        btn_row.addWidget(self.deselect_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("✖ Cancel")
        cancel_btn.setStyleSheet("background-color:#e74c3c;color:white;font-weight:bold;padding:8px;")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self.accept_btn = QPushButton("✅ Accept Import")
        self.accept_btn.setStyleSheet("background-color:#27ae60;color:white;font-weight:bold;padding:8px;")
        self.accept_btn.clicked.connect(self._do_accept)
        btn_row.addWidget(self.accept_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def _build_points_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        lbl = QLabel("Tick the rows you want to import. Double-click any cell to edit.")
        lbl.setStyleSheet("color:#555; font-size:10pt;")
        layout.addWidget(lbl)

        self.points_table = QTableWidget()
        self.points_table.setColumnCount(5)
        self.points_table.setHorizontalHeaderLabels(
            ["✓", "Point #", "Easting (m)", "Northing (m)", "Description"])
        self.points_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.points_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.points_table.setColumnWidth(0, 40)
        self.points_table.setAlternatingRowColors(True)
        layout.addWidget(self.points_table)

        tab.setLayout(layout)
        return tab

    def _build_traverse_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        lbl = QLabel("Traverse legs matched from bearing and distance annotations in the DXF.")
        lbl.setStyleSheet("color:#555; font-size:10pt;")
        layout.addWidget(lbl)

        self.legs_table = QTableWidget()
        self.legs_table.setColumnCount(4)
        self.legs_table.setHorizontalHeaderLabels(
            ["✓", "Leg #", "Bearing (DMS)", "Distance (m)"])
        self.legs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.legs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.legs_table.setColumnWidth(0, 40)
        self.legs_table.setAlternatingRowColors(True)
        layout.addWidget(self.legs_table)

        tab.setLayout(layout)
        return tab

    def _build_metadata_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        lbl = QLabel("Metadata detected from title block and annotation text. Edit as needed.")
        lbl.setStyleSheet("color:#555; font-size:10pt;")
        layout.addWidget(lbl)

        self.meta_table = QTableWidget()
        self.meta_table.setColumnCount(3)
        self.meta_table.setHorizontalHeaderLabels(["✓", "Field", "Extracted Value"])
        self.meta_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.meta_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.meta_table.setColumnWidth(0, 40)
        self.meta_table.setAlternatingRowColors(True)
        layout.addWidget(self.meta_table)

        tab.setLayout(layout)
        return tab

    def _build_layers_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        lbl = QLabel("All layers found in the DXF file. Use this to identify which layer contains your beacon points.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#555; font-size:10pt;")
        layout.addWidget(lbl)

        self.layers_text = QTextEdit()
        self.layers_text.setReadOnly(True)
        self.layers_text.setStyleSheet("font-family:monospace; font-size:10pt;")
        layout.addWidget(self.layers_text)

        tab.setLayout(layout)
        return tab

    def _build_warnings_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        self.warnings_text = QTextEdit()
        self.warnings_text.setReadOnly(True)
        self.warnings_text.setStyleSheet("font-family:monospace; font-size:10pt;")
        layout.addWidget(self.warnings_text)
        tab.setLayout(layout)
        return tab

    # ── Populate from result ──────────────────────────────────────────────────

    def _populate(self):
        if not self.result:
            return

        # Summary
        self.summary_lbl.setText(self.result.summary())

        # Warnings tab badge
        w_count = len(self.result.warnings) + len(self.result.errors)
        if w_count:
            self.tabs.setTabText(4, f"⚠ Warnings ({w_count})")

        # Points — prefer polyline vertices if they look like a survey boundary
        all_points = list(self.result.points)
        best_poly  = self._pick_best_polyline()
        if best_poly and not all_points:
            all_points = best_poly

        self.points_table.setRowCount(len(all_points))
        for i, pt in enumerate(all_points):
            cb = QTableWidgetItem()
            cb.setCheckState(Qt.Checked)
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            self.points_table.setItem(i, 0, cb)
            self.points_table.setItem(i, 1, QTableWidgetItem(str(i + 1)))
            self.points_table.setItem(i, 2, QTableWidgetItem(f"{pt['x']:.3f}"))
            self.points_table.setItem(i, 3, QTableWidgetItem(f"{pt['y']:.3f}"))
            self.points_table.setItem(i, 4, QTableWidgetItem(pt.get("desc", "")))

        # Multiple polylines → show selector
        if len(self.result.polylines) > 1:
            self.poly_group.setVisible(True)
            for i, poly in enumerate(self.result.polylines):
                self.poly_combo.addItem(
                    f"Polyline {i+1}  ({len(poly)} vertices)", i)
            self.poly_combo.currentIndexChanged.connect(self._on_poly_changed)

        # Legs
        self.legs_table.setRowCount(len(self.result.legs))
        for i, leg in enumerate(self.result.legs):
            cb = QTableWidgetItem()
            cb.setCheckState(Qt.Checked)
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            self.legs_table.setItem(i, 0, cb)
            self.legs_table.setItem(i, 1, QTableWidgetItem(str(i + 1)))
            self.legs_table.setItem(i, 2, QTableWidgetItem(leg["bearing_dms"]))
            self.legs_table.setItem(i, 3, QTableWidgetItem(f"{leg['distance']:.3f}"))

        # Colour legs table rows by decimal bearing (sanity check)
        for i, leg in enumerate(self.result.legs):
            b = leg["bearing_decimal"]
            if not (0 <= b < 360):
                for col in range(4):
                    item = self.legs_table.item(i, col)
                    if item:
                        item.setBackground(QColor("#FFEBEE"))

        # Metadata — use friendly display labels, skip area/internal fields
        FRIENDLY_LABELS = {
            "plan_number":   "Plan Number",
            "owner":         "Owner Name",
            "owner_name":    "Owner Name",
            "surveyor":      "Surveyor Name",
            "surveyor_name": "Surveyor Name",
            "survey_date":   "Survey Date",
            "lga":           "LGA",
            "state":         "State",
            "description":   "Description / Notes",
            "area_sqm":      "Area (sq metres)",
            "area_acres":    "Area (acres)",
            "area_hectares": "Area (hectares)",
        }
        # Deduplicate owner/owner_name etc
        seen_labels = set()
        meta_rows = []
        for key, val in self.result.metadata.items():
            if val is None or str(val).strip() in ("", "null", "None"):
                continue
            label = FRIENDLY_LABELS.get(key, key.replace("_", " ").title())
            if label in seen_labels:
                continue
            seen_labels.add(label)
            meta_rows.append((label, key, val))

        self.meta_table.setRowCount(len(meta_rows))
        for i, (label, key, val) in enumerate(meta_rows):
            cb = QTableWidgetItem()
            cb.setCheckState(Qt.Checked)
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            self.meta_table.setItem(i, 0, cb)
            lbl_item = QTableWidgetItem(label)
            lbl_item.setData(Qt.UserRole, key)   # store raw key for _do_accept
            self.meta_table.setItem(i, 1, lbl_item)
            val_item = QTableWidgetItem(str(val))
            val_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable)
            self.meta_table.setItem(i, 2, val_item)

        # Layers
        self.layers_text.setText("\n".join(self.result.layers) or "No named layers found.")

        # Warnings
        msgs = []
        for e in self.result.errors:
            msgs.append(f"ERROR: {e}")
        for w in self.result.warnings:
            msgs.append(f"WARNING: {w}")
        self.warnings_text.setText("\n\n".join(msgs) or "No warnings.")

        # Disable accept if there were fatal errors
        if self.result.errors:
            self.accept_btn.setEnabled(False)
            self.accept_btn.setText("✖ Cannot import — see Warnings tab")
            self.tabs.setCurrentIndex(4)

        # Switch to most useful tab
        elif self.result.legs:
            self.tabs.setCurrentIndex(1)  # traverse
        else:
            self.tabs.setCurrentIndex(0)  # coordinates

    def _pick_best_polyline(self):
        """Choose the polyline most likely to be the survey boundary."""
        if not self.result.polylines:
            return None
        # Prefer the one with most vertices
        return max(self.result.polylines, key=len)

    def _on_poly_changed(self, idx):
        """Repopulate points table when user picks a different polyline."""
        poly_idx = self.poly_combo.currentData()
        if poly_idx is None:
            return
        verts = self.result.polylines[poly_idx]
        self.points_table.setRowCount(len(verts))
        for i, v in enumerate(verts):
            cb = QTableWidgetItem()
            cb.setCheckState(Qt.Checked)
            cb.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            self.points_table.setItem(i, 0, cb)
            self.points_table.setItem(i, 1, QTableWidgetItem(str(i + 1)))
            self.points_table.setItem(i, 2, QTableWidgetItem(f"{v['x']:.3f}"))
            self.points_table.setItem(i, 3, QTableWidgetItem(f"{v['y']:.3f}"))
            self.points_table.setItem(i, 4, QTableWidgetItem(""))

    def _set_all_checks(self, state):
        """Check / uncheck all rows in the active tab."""
        qt_state = Qt.Checked if state else Qt.Unchecked
        idx = self.tabs.currentIndex()
        table = [self.points_table, self.legs_table, self.meta_table,
                 None, None][idx]
        if table:
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item:
                    item.setCheckState(qt_state)

    # ── Accept ────────────────────────────────────────────────────────────────

    def _do_accept(self):
        # Collect checked points
        if self.import_coords_cb.isChecked():
            for row in range(self.points_table.rowCount()):
                cb = self.points_table.item(row, 0)
                if cb and cb.checkState() == Qt.Checked:
                    try:
                        e = float(self.points_table.item(row, 2).text())
                        n = float(self.points_table.item(row, 3).text())
                        desc = self.points_table.item(row, 4).text() if self.points_table.item(row, 4) else ""
                        self.accepted_points.append({"x": e, "y": n, "desc": desc})
                    except (ValueError, AttributeError):
                        pass

        # Collect checked legs
        if self.import_traverse_cb.isChecked():
            for row in range(self.legs_table.rowCount()):
                cb = self.legs_table.item(row, 0)
                if cb and cb.checkState() == Qt.Checked:
                    try:
                        dms  = self.legs_table.item(row, 2).text()
                        dist = float(self.legs_table.item(row, 3).text())
                        # Recover decimal from result.legs if available
                        if row < len(self.result.legs):
                            dec = self.result.legs[row]["bearing_decimal"]
                        else:
                            dec = 0.0
                        self.accepted_legs.append({
                            "bearing_dms":     dms,
                            "bearing_decimal": dec,
                            "distance":        dist
                        })
                    except (ValueError, AttributeError):
                        pass

        # Collect checked metadata
        # Normalise various key spellings to the canonical form _apply_dxf_result expects
        NORMALISE = {
            "owner_name":    "owner",
            "surveyor_name": "surveyor",
        }
        if self.import_meta_cb.isChecked():
            for row in range(self.meta_table.rowCount()):
                cb = self.meta_table.item(row, 0)
                if cb and cb.checkState() == Qt.Checked:
                    lbl_item = self.meta_table.item(row, 1)
                    val_item = self.meta_table.item(row, 2)
                    if not lbl_item or not val_item:
                        continue
                    # Prefer the raw key stored in UserRole; fall back to label
                    raw_key = lbl_item.data(Qt.UserRole) or                               lbl_item.text().lower().replace(" ", "_")
                    key = NORMALISE.get(raw_key, raw_key)
                    val = val_item.text().strip()
                    if val and val not in ("null", "None"):
                        self.accepted_metadata[key] = val

        # Validate at least something was selected
        if (not self.accepted_points and
                not self.accepted_legs and
                not self.accepted_metadata):
            QMessageBox.warning(self, "Nothing selected",
                "Please tick at least one row to import, "
                "or cancel the import.")
            return

        self.accept()
