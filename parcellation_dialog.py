# -*- coding: utf-8 -*-
"""
parcellation_dialog.py — Survey Management System v2.0
Clean rewrite — all buttons wired, robust coordinate loading.
"""

import os, sys, math, traceback
from typing import List, Optional

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QDoubleSpinBox, QTabWidget, QWidget,
    QGroupBox, QComboBox, QFileDialog, QMessageBox,
    QSplitter, QTextEdit, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QListWidget, QListWidgetItem, QScrollArea,
    QSizePolicy
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QTimer, QVariant
from qgis.PyQt.QtGui import QColor, QFont, QBrush

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsPointXY, QgsField, QgsFields, QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol, QgsLineSymbol,
    QgsSingleSymbolRenderer,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
    QgsTextFormat, QgsTextBufferSettings, QgsRectangle
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand

from .parcellation_engine import (
    ParcellationResult,
    polygon_area_abs
)
from .road_aware_engine import RoadAwareEngine

# Shared brand color (matches the group-box titles and the "Run
# Parcellation" button) applied to every table header, so the Coordinates
# and Areas tabs read as part of the same document instead of plain
# default-grey Qt tables.
_TABLE_HEADER_QSS = (
    "QHeaderView::section{background:#1A5C38;color:white;"
    "font-weight:bold;padding:4px;border:none;}"
)


class RoadDrawingTool(QgsMapToolEmitPoint):
    road_finished = pyqtSignal(list)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._pts = []
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self._rb.setColor(QColor(80, 80, 80))
        self._rb.setWidth(3)

    def canvasPressEvent(self, e):
        pt = self.toMapCoordinates(e.pos())
        if e.button() == Qt.RightButton:
            if len(self._pts) >= 2:
                self.road_finished.emit(list(self._pts))
            self._pts = []
            self._rb.reset(QgsWkbTypes.LineGeometry)
            return
        self._pts.append((pt.x(), pt.y()))
        self._rb.addPoint(pt)

    def canvasMoveEvent(self, e):
        if self._pts:
            pt = self.toMapCoordinates(e.pos())
            if self._rb.numberOfVertices() > len(self._pts):
                self._rb.removePoint(self._rb.numberOfVertices() - 1)
            self._rb.addPoint(pt)

    def deactivate(self):
        self._pts = []
        self._rb.reset(QgsWkbTypes.LineGeometry)
        super().deactivate()


class SubdivisionWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, engine):
        super().__init__()
        self._eng = engine

    def run(self):
        try:
            self.finished.emit(self._eng.subdivide())
        except Exception as ex:
            self.error.emit(str(ex))


class ParcellationDialog(QDialog):

    def __init__(self, iface, crs, parent=None, existing_perimeter=None):
        super().__init__(parent)
        self.iface  = iface
        self.canvas = iface.mapCanvas()
        self.crs    = crs if (crs and crs.isValid()) \
                      else QgsCoordinateReferenceSystem("EPSG:26333")

        self.setWindowTitle("Parcellation Module — Survey Management System v2.0")
        # Default size is notably larger than before so the tables show
        # several more rows without scrolling out of the box; minimum size
        # is kept below 768px tall so the dialog still fits on a 1366x768
        # laptop screen if the user drags it smaller.
        self.setMinimumSize(1100, 720)
        self.resize(1420, 880)

        self._perimeter: List = existing_perimeter or []
        self._roads: List     = []
        self._result          = None
        self._layers          = {}
        self._draw_tool       = None
        self._prev_tool       = None
        self._worker          = None

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._run)

        self._build_ui()

        if self._perimeter:
            self._loaded()

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)

        ttl = QLabel("🗺  Parcellation Module")
        ttl.setFont(QFont("Arial", 13, QFont.Bold))
        ttl.setStyleSheet("color:#1A5C38;padding:4px 0;")
        ttl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root.addWidget(ttl, 0)

        # The splitter (perimeter/roads/parameters panel + tabs) is the
        # only part of this dialog that should ever grow -- give it the
        # explicit stretch factor AND force its size policy to Expanding
        # rather than relying on QSplitter's default. Without both of
        # these, resizing the dialog taller left the extra height as dead
        # grey space above the title and below the status bar instead of
        # going into the panel/tables, which is what "expand" is asking
        # for here.
        sp = QSplitter(Qt.Horizontal)
        sp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(sp, 1)

        # ── LEFT ──────────────────────────────────────────────────────────
        lw = QWidget(); lw.setMaximumWidth(390)
        lv = QVBoxLayout(lw); lv.setSpacing(6)

        # Perimeter
        g1 = QGroupBox("1.  Perimeter")
        g1.setStyleSheet("QGroupBox{font-weight:bold;color:#1A5C38;}")
        v1 = QVBoxLayout(g1)

        self.btn_enter = QPushButton("📋  Enter / paste coordinates")
        self.btn_enter.setFixedHeight(34)
        self.btn_enter.setStyleSheet(
            "QPushButton{background:#1A5C38;color:white;font-weight:bold;"
            "border-radius:3px;}QPushButton:hover{background:#1D9E75;}")
        self.btn_enter.clicked.connect(lambda: self.tabs.setCurrentIndex(0))

        self.btn_dxf = QPushButton("📂  Import from DXF file")
        self.btn_dxf.setFixedHeight(30)
        self.btn_dxf.clicked.connect(self._import_dxf)

        self.btn_clr_peri = QPushButton("✕  Clear perimeter")
        self.btn_clr_peri.setFixedHeight(28)
        self.btn_clr_peri.clicked.connect(self._clear_peri)

        self.lbl_peri = QLabel("No perimeter loaded")
        self.lbl_peri.setStyleSheet("color:#888;font-size:11px;")
        self.lbl_peri.setWordWrap(True)

        for w in (self.btn_enter, self.btn_dxf, self.btn_clr_peri,
                  self.lbl_peri):
            v1.addWidget(w)
        lv.addWidget(g1)

        # Roads
        g2 = QGroupBox("2.  Roads (not used by this engine)")
        g2.setStyleSheet("QGroupBox{font-weight:bold;color:#999;}")
        v2 = QVBoxLayout(g2)

        self.btn_draw = QPushButton("✏  Draw road centreline on map")
        self.btn_draw.setCheckable(True)
        self.btn_draw.setFixedHeight(32)
        self.btn_draw.setEnabled(False)
        self.btn_draw.setToolTip(
            "The road-aware engine computes its own road grid (including\n"
            "perpendicular cross-roads) from Plot area / frontage / road\n"
            "width / cross-road spacing below -- manual centrelines aren't used.")
        self.btn_draw.setStyleSheet(
            "QPushButton:checked{background:#1D9E75;color:white;"
            "font-weight:bold;}")
        self.btn_draw.toggled.connect(self._toggle_draw)

        hint = QLabel("The road-aware engine below computes its own road "
                       "grid automatically.")
        hint.setStyleSheet("color:#999;font-size:10px;")
        hint.setWordWrap(True)

        # Road list — kept for layout stability but unused/disabled.
        self.lst_roads = QListWidget()
        self.lst_roads.setFixedHeight(72)
        self.lst_roads.setStyleSheet("font-size:10px;")
        self.lst_roads.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lst_roads.setEnabled(False)

        road_btns = QHBoxLayout()
        self.btn_del_road = QPushButton("✕ Delete selected")
        self.btn_del_road.setFixedHeight(26)
        self.btn_del_road.setEnabled(False)
        self.btn_del_road.clicked.connect(self._del_selected_road)
        self.btn_clr_roads = QPushButton("✕ Clear all")
        self.btn_clr_roads.setFixedHeight(26)
        self.btn_clr_roads.setEnabled(False)
        self.btn_clr_roads.clicked.connect(self._clear_roads)
        road_btns.addWidget(self.btn_del_road)
        road_btns.addWidget(self.btn_clr_roads)

        self.lbl_roads = QLabel("0 roads defined")
        self.lbl_roads.setStyleSheet("font-size:11px;color:#444;")

        self.lst_roads.itemSelectionChanged.connect(
            lambda: self.btn_del_road.setEnabled(
                len(self.lst_roads.selectedItems()) > 0))

        for w in (self.btn_draw, hint, self.lst_roads):
            v2.addWidget(w)
        v2.addLayout(road_btns)
        v2.addWidget(self.lbl_roads)
        lv.addWidget(g2)

        # Parameters
        g3 = QGroupBox("3.  Parameters")
        g3.setStyleSheet("QGroupBox{font-weight:bold;color:#1A5C38;}")
        gr = QGridLayout(g3); gr.setSpacing(5)

        gr.addWidget(QLabel("Engine: Road-aware (exact-area, Brent's method)"),
                     0, 0, 1, 2)
        gr.itemAtPosition(0, 0).widget().setStyleSheet(
            "font-size:11px;color:#1A5C38;font-weight:bold;")

        gr.addWidget(QLabel("Plot area (m²):"), 1, 0)
        self.sp_area = QDoubleSpinBox()
        self.sp_area.setRange(50, 500000); self.sp_area.setValue(500)
        self.sp_area.setSingleStep(50); self.sp_area.setDecimals(0)
        gr.addWidget(self.sp_area, 1, 1)

        gr.addWidget(QLabel("Plot frontage (m):"), 2, 0)
        self.sp_front = QDoubleSpinBox()
        self.sp_front.setRange(3, 500); self.sp_front.setValue(15)
        self.sp_front.setSingleStep(1); self.sp_front.setDecimals(1)
        gr.addWidget(self.sp_front, 2, 1)

        gr.addWidget(QLabel("Road width (m):"), 3, 0)
        self.sp_road = QDoubleSpinBox()
        self.sp_road.setRange(0, 60); self.sp_road.setValue(9)
        self.sp_road.setSingleStep(0.5); self.sp_road.setDecimals(1)
        gr.addWidget(self.sp_road, 3, 1)

        gr.addWidget(QLabel("Cross-road spacing (m):"), 4, 0)
        self.sp_cross = QDoubleSpinBox()
        self.sp_cross.setRange(0, 2000); self.sp_cross.setValue(0)
        self.sp_cross.setSingleStep(10); self.sp_cross.setDecimals(0)
        self.sp_cross.setSpecialValueText("Off")
        self.sp_cross.setToolTip(
            "Adds perpendicular connector roads so no block runs longer\n"
            "than this before a cross-street gives mid-block vehicle access.\n"
            "0 = off (rows run the full width of the site).")
        gr.addWidget(self.sp_cross, 4, 1)
        lv.addWidget(g3)

        # Summary
        g4 = QGroupBox("Summary")
        g4.setStyleSheet("QGroupBox{font-weight:bold;color:#1A5C38;}")
        v4 = QVBoxLayout(g4)
        self.lbl_sum = QLabel("Run parcellation to see summary.")
        self.lbl_sum.setWordWrap(True)
        self.lbl_sum.setStyleSheet("font-size:11px;color:#333;")
        v4.addWidget(self.lbl_sum)
        lv.addWidget(g4)

        self.prog = QProgressBar()
        self.prog.setVisible(False)
        self.prog.setRange(0, 0)
        lv.addWidget(self.prog)
        lv.addStretch()

        self.btn_run = QPushButton("▶  Run Parcellation")
        self.btn_run.setFixedHeight(38)
        self.btn_run.setStyleSheet(
            "QPushButton{background:#1A5C38;color:white;font-weight:bold;"
            "border-radius:4px;font-size:13px;}"
            "QPushButton:hover{background:#1D9E75;}"
            "QPushButton:disabled{background:#888;}")
        self.btn_run.clicked.connect(self._run)

        self.btn_exp = QPushButton("💾  Export DXF")
        self.btn_exp.setFixedHeight(30)
        self.btn_exp.setEnabled(False)
        self.btn_exp.clicked.connect(self._export)

        self.btn_xlsx = QPushButton("📊  Export Excel")
        self.btn_xlsx.setFixedHeight(30)
        self.btn_xlsx.setEnabled(False)
        self.btn_xlsx.clicked.connect(self._export_xlsx)

        self.btn_pdf = QPushButton("📄  Save Report (PDF)")
        self.btn_pdf.setFixedHeight(30)
        self.btn_pdf.setEnabled(False)
        self.btn_pdf.clicked.connect(self._save_report_pdf)

        self.btn_print = QPushButton("🖨  Print Report")
        self.btn_print.setFixedHeight(30)
        self.btn_print.setEnabled(False)
        self.btn_print.clicked.connect(self._print_report)

        self.btn_cls = QPushButton("Close")
        self.btn_cls.setFixedHeight(30)
        self.btn_cls.clicked.connect(self.accept)

        for w in (self.btn_run, self.btn_exp, self.btn_xlsx,
                  self.btn_pdf, self.btn_print, self.btn_cls):
            lv.addWidget(w)

        # Absorb any leftover vertical space HERE, at the very end, rather
        # than letting Qt distribute it as gaps between the group boxes
        # above. Without this, QScrollArea's setWidgetResizable(True)
        # stretches `lw` to fill the full viewport height whenever the
        # dialog is taller than the panel's natural content -- and with no
        # stretch to soak that up, the extra space gets spread out as
        # visible whitespace throughout the layout (this is exactly what
        # produced the "ugly", oddly-gapped panel).
        lv.addStretch(1)

        # Wrap the whole left panel in a scroll area -- with the engine
        # selector line, four parameter fields, and five action buttons
        # all stacked in a fixed-width column, this panel was routinely
        # taller than the available dialog height and everything below
        # "Summary" got visually compressed. A scroll area lets it take
        # its natural size and simply scroll instead.
        scroll = QScrollArea()
        scroll.setWidget(lw)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMaximumWidth(410)
        scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sp.addWidget(scroll)

        # ── RIGHT ─────────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tabs.setStyleSheet(
            "QTabBar::tab:selected{color:#1A5C38;font-weight:bold;}")

        # Tab 0: coordinate entry
        te = QWidget(); tev = QVBoxLayout(te)
        tev.addWidget(QLabel(
            "Enter or paste perimeter coordinates  (Easting, Northing — one per line):"))
        self.txt = QTextEdit()
        self.txt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.txt.setFont(QFont("Courier New", 10))
        self.txt.setPlaceholderText(
            "Paste Easting, Northing — one point per line.\n"
            "Example (Okuku, Owerri West — 1 hectare):\n\n"
            "498602.775, 164766.106\n"
            "498705.372, 164796.456\n"
            "498681.771, 164881.342\n"
            "498632.915, 164872.434\n"
            "498578.567, 164849.799\n"
            "498583.065, 164811.453")
        tev.addWidget(self.txt)

        self.btn_load = QPushButton("✅  Load these coordinates as perimeter")
        self.btn_load.setFixedHeight(38)
        self.btn_load.setStyleSheet(
            "QPushButton{background:#1A5C38;color:white;font-weight:bold;"
            "border-radius:4px;font-size:12px;}"
            "QPushButton:hover{background:#1D9E75;}")
        self.btn_load.clicked.connect(self._load)
        tev.addWidget(self.btn_load)
        self.tabs.addTab(te, "✏  Enter coordinates")

        # Tab 1: coordinates output
        tc = QWidget(); tcv = QVBoxLayout(tc)
        tcv.addWidget(QLabel("Setting-out coordinates — plots, perimeter (green) "
                              "and roads (grey):"))
        self.tbl_c = QTableWidget(0, 4)
        self.tbl_c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tbl_c.setHorizontalHeaderLabels(
            ["Plot / Group", "Point", "Easting (m)", "Northing (m)"])
        self.tbl_c.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_c.horizontalHeader().setStyleSheet(_TABLE_HEADER_QSS)
        self.tbl_c.horizontalHeader().setFixedHeight(30)
        self.tbl_c.verticalHeader().setDefaultSectionSize(26)
        self.tbl_c.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_c.setAlternatingRowColors(True)
        tcv.addWidget(self.tbl_c)
        bc = QPushButton("📋  Copy to clipboard")
        bc.setFixedHeight(28); bc.clicked.connect(self._copy_c)
        tcv.addWidget(bc)
        self.tabs.addTab(tc, "📍 Coordinates")

        # Tab 2: area schedule
        ta = QWidget(); tav = QVBoxLayout(ta)
        tav.addWidget(QLabel("Schedule of areas:"))
        self.tbl_a = QTableWidget(0, 4)
        self.tbl_a.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tbl_a.setHorizontalHeaderLabels(
            ["No.", "Plot ID", "Area (m²)", "Area (ha)"])
        self.tbl_a.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_a.horizontalHeader().setStyleSheet(_TABLE_HEADER_QSS)
        self.tbl_a.horizontalHeader().setFixedHeight(30)
        self.tbl_a.verticalHeader().setDefaultSectionSize(26)
        self.tbl_a.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_a.setAlternatingRowColors(True)
        tav.addWidget(self.tbl_a)
        self.lbl_tot = QLabel("")
        self.lbl_tot.setStyleSheet(
            "font-weight:bold;color:#1A5C38;font-size:12px;")
        tav.addWidget(self.lbl_tot)
        ba = QPushButton("📋  Copy to clipboard")
        ba.setFixedHeight(28); ba.clicked.connect(self._copy_a)
        tav.addWidget(ba)
        self.tabs.addTab(ta, "📊 Areas")

        sp.addWidget(self.tabs)
        sp.setSizes([390, 1030])

        # Status
        self.lbl_st = QLabel("Ready.  Paste coordinates in the tab above and click Load.")
        self.lbl_st.setStyleSheet(
            "background:#F0F0F0;color:#333;padding:4px 8px;font-size:11px;")
        self.lbl_st.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root.addWidget(self.lbl_st, 0)

        # Parameter live preview
        for w in (self.sp_area, self.sp_front, self.sp_road, self.sp_cross):
            w.valueChanged.connect(lambda _: self._timer.start()
                                   if self._perimeter else None)

    # ── Coordinate entry ──────────────────────────────────────────────────

    def _load(self):
        text = self.txt.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty",
                "Paste your coordinates in the text box first.\n\n"
                "Format:  Easting, Northing  — one point per line\n\n"
                "Example:\n498602.775, 164766.106\n498705.372, 164796.456")
            return

        pts, errors = [], []
        for i, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").replace("\t", " ").split()
            parts = [p for p in parts if p]
            if len(parts) < 2:
                errors.append(f"Line {i}: {line!r}")
                continue
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                errors.append(f"Line {i}: {line!r}")

        if len(pts) < 3:
            detail = ("\n".join(errors[:5])) if errors else ""
            QMessageBox.warning(self, "Not enough points",
                f"Need at least 3 points, got {len(pts)}.\n\n{detail}")
            return

        if errors:
            QMessageBox.information(self, "Some lines skipped",
                f"Loaded {len(pts)} points.\n"
                f"Skipped {len(errors)} line(s):\n" +
                "\n".join(errors[:5]))

        self._perimeter = pts
        self._loaded()

    def _loaded(self):
        area = polygon_area_abs(self._perimeter)
        msg = (f"{len(self._perimeter)} pts  ·  "
               f"{area:,.1f} m²  ({area/10000:.4f} ha)")
        self.lbl_peri.setText(msg)
        self.lbl_peri.setStyleSheet(
            "color:#1A5C38;font-size:11px;font-weight:bold;")
        self._st(f"Perimeter loaded: {msg}")
        self._layer_peri()
        self._zoom()
        self._timer.start()
        QMessageBox.information(self, "Perimeter Loaded",
            f"Loaded {len(self._perimeter)} points.\n\n"
            f"Area: {area:,.1f} m²  ({area/10000:.4f} ha)\n\n"
            "Now draw roads (optional) and click Run Parcellation.")

    # ── DXF import ────────────────────────────────────────────────────────

    def _import_dxf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open DXF", "", "AutoCAD DXF (*.dxf *.DXF)")
        if not path:
            return
        try:
            import ezdxf
        except ImportError:
            lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
            if lib not in sys.path:
                sys.path.insert(0, lib)
            try:
                import ezdxf
            except ImportError:
                QMessageBox.warning(self, "ezdxf Not Available",
                    "ezdxf is not installed yet.\n\n"
                    "Open the main plugin and import any DXF file first —\n"
                    "that installs ezdxf automatically.\n\n"
                    "Then return here and try again.")
                return
        try:
            with open(path, "rb") as fh:
                doc = ezdxf.read(fh)
            pts, best = [], 0
            for e in doc.modelspace():
                if e.dxftype() != "LWPOLYLINE":
                    continue
                v = [(x, y) for x, y, *_ in e.get_points()]
                if len(v) > best:
                    best, pts = len(v), v
            if pts and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) < 3:
                QMessageBox.warning(self, "DXF",
                    f"Only found {len(pts)} point(s).\n"
                    "Use the coordinate entry tab instead.")
                return
            self._perimeter = pts
            self._loaded()
        except Exception as ex:
            QMessageBox.critical(self, "DXF Error", str(ex))

    # ── Perimeter helpers ─────────────────────────────────────────────────

    def _clear_peri(self):
        self._perimeter = []
        self._result = None
        self.lbl_peri.setText("No perimeter loaded")
        self.lbl_peri.setStyleSheet("color:#888;font-size:11px;")
        self.btn_exp.setEnabled(False)
        self.btn_xlsx.setEnabled(False)
        self.btn_pdf.setEnabled(False)
        self.btn_print.setEnabled(False)
        self._del_layers()
        self._clr_tables()
        self.lbl_sum.setText("Run parcellation to see summary.")
        self._st("Perimeter cleared")

    def _zoom(self):
        if not self._perimeter:
            return
        try:
            xs = [p[0] for p in self._perimeter]
            ys = [p[1] for p in self._perimeter]
            m = max(10, (max(xs)-min(xs)) * 0.15)
            self.canvas.setExtent(
                QgsRectangle(min(xs)-m, min(ys)-m, max(xs)+m, max(ys)+m))
            self.canvas.refresh()
        except Exception:
            pass

    # ── Road drawing ──────────────────────────────────────────────────────

    def _toggle_draw(self, on):
        if on:
            if not self._perimeter:
                QMessageBox.information(self, "Draw Road",
                    "Load a perimeter first.")
                self.btn_draw.setChecked(False)
                return
            self._draw_tool = RoadDrawingTool(self.canvas)
            self._draw_tool.road_finished.connect(self._road_done)
            self._prev_tool = self.canvas.mapTool()
            self.canvas.setMapTool(self._draw_tool)
            self._st("Click map to place road points · Right-click to finish")
        else:
            if self._draw_tool:
                self.canvas.setMapTool(self._prev_tool)
                self._draw_tool.deactivate()
                self._draw_tool = None
            self._st("Road drawing stopped")

    def _road_done(self, pts):
        self._roads.append(pts)
        n = len(self._roads)
        # Add to list widget
        length = sum(
            ((pts[i+1][0]-pts[i][0])**2 + (pts[i+1][1]-pts[i][1])**2)**0.5
            for i in range(len(pts)-1))
        item = QListWidgetItem(
            f"Road {n}  —  {len(pts)} pts  ·  {length:.1f} m")
        item.setData(32, n-1)   # store index
        self.lst_roads.addItem(item)
        self.lbl_roads.setText(f"{n} road(s) defined")
        self._layer_roads()
        self._timer.start()
        self._st(f"Road {n} added ({length:.1f} m). Draw another or Run.")

    def _del_selected_road(self):
        """Delete the selected road from the list."""
        items = self.lst_roads.selectedItems()
        if not items:
            return
        idx = items[0].data(32)   # stored index
        if 0 <= idx < len(self._roads):
            self._roads.pop(idx)
            self.lst_roads.clear()
            # Rebuild list
            for i, rd in enumerate(self._roads):
                length = sum(
                    ((rd[j+1][0]-rd[j][0])**2 + (rd[j+1][1]-rd[j][1])**2)**0.5
                    for j in range(len(rd)-1))
                item = QListWidgetItem(
                    f"Road {i+1}  —  {len(rd)} pts  ·  {length:.1f} m")
                item.setData(32, i)
                self.lst_roads.addItem(item)
            self.lbl_roads.setText(f"{len(self._roads)} road(s) defined")
            self.btn_del_road.setEnabled(False)
            self._layer_roads()
            self._timer.start()
            self._st(f"Road deleted. {len(self._roads)} road(s) remaining.")

    def _clear_roads(self):
        self._roads = []
        self.btn_draw.setChecked(False)
        self.lst_roads.clear()
        self.btn_del_road.setEnabled(False)
        self.lbl_roads.setText("0 roads defined")
        for k in ("roads", "plots", "road_polys"):
            self._del_layer(k)
        self._result = None
        self.btn_exp.setEnabled(False)
        self.btn_xlsx.setEnabled(False)
        self.btn_pdf.setEnabled(False)
        self.btn_print.setEnabled(False)
        self._clr_tables()
        self.lbl_sum.setText("Run parcellation to see summary.")
        self._st("Roads cleared")

    # ── Subdivision ───────────────────────────────────────────────────────

    def _run(self):
        if not self._perimeter:
            QMessageBox.information(self, "No Perimeter",
                "Paste coordinates in the Enter coordinates tab\n"
                "and click the green Load button first.")
            return
        self.btn_run.setEnabled(False)
        self.prog.setVisible(True)
        self._st("Running parcellation…")

        eng = RoadAwareEngine(self._perimeter)
        eng.set_params(
            plot_area=self.sp_area.value(),
            frontage=self.sp_front.value(),
            road_width=self.sp_road.value(),
            cross_road_spacing=self.sp_cross.value())

        self._worker = SubdivisionWorker(eng)
        self._worker.finished.connect(self._done)
        self._worker.error.connect(self._err)
        self._worker.start()

    def _done(self, result):
        self._result = result
        self.prog.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_exp.setEnabled(True)
        self.btn_xlsx.setEnabled(True)
        self.btn_pdf.setEnabled(True)
        self.btn_print.setEnabled(True)
        s = result.summary
        n_ll = s.get('n_landlocked', 0)
        ll_line = f"🚫 Landlocked: {n_ll}\n" if n_ll else "✅ Landlocked: 0\n"

        if s.get("engine") == "road_aware":
            self.lbl_sum.setText(
                f"Perimeter: {s['perimeter_area_m2']:,.1f} m²\n"
                f"Road area: {s['road_area_m2']:,.1f} m²\n"
                f"Total plots: {s['n_plots']}\n"
                f"✅ Standard: {s.get('n_standard',0)}\n"
                f"🟢 Merged (bonus): {s.get('n_merged',0)}\n"
                f"🟣 Reduced (corner): {s.get('n_reduced',0)}\n"
                f"{ll_line}"
                f"Coverage: {s.get('coverage_pct',0)}%")
        else:
            lo = s.get('tolerance_lo_m2', 0)
            hi = s.get('tolerance_hi_m2', 0)
            opt_str = " (optimized)" if s.get('optimized') else ""
            self.lbl_sum.setText(
                f"Perimeter: {s['perimeter_area_m2']:,.1f} m²\n"
                f"Road area: {s['road_area_m2']:,.1f} m²\n"
                f"Total plots: {s['n_plots']}{opt_str}\n"
                f"✅ Compliant ({lo}–{hi} m²): {s.get('n_compliant',0)}\n"
                f"⚠  Edge/partial: {s.get('n_edge',0)}\n"
                f"{ll_line}"
                f"Theoretical max: {s['theoretical_n_plots']}")
        self._layer_result(result)
        self._fill_c(result)
        self._fill_a(result)
        self.tabs.setCurrentIndex(2)
        self._st(f"Done: {s['n_plots']} plots in {s['n_loops']} block(s)")

    def _err(self, msg):
        self.prog.setVisible(False)
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, "Error", msg)
        self._st("Error")

    # ── Layers ────────────────────────────────────────────────────────────

    def _crs(self):
        try:
            return self.crs.authid() if self.crs and self.crs.isValid() \
                   else "EPSG:26333"
        except Exception:
            return "EPSG:26333"

    def _get_lyr(self, key, name, geom, fields=None):
        if key in self._layers:
            lyr = self._layers[key]
            if lyr and lyr.isValid():
                lyr.dataProvider().truncate()
                return lyr
        lyr = QgsVectorLayer(f"{geom}?crs={self._crs()}", name, "memory")
        if fields:
            lyr.dataProvider().addAttributes(fields)
            lyr.updateFields()
        QgsProject.instance().addMapLayer(lyr)
        self._layers[key] = lyr
        return lyr

    def _layer_peri(self):
        try:
            self._del_layer("perimeter")
            uri = f"Polygon?crs={self._crs()}"
            lyr = QgsVectorLayer(uri, "Parcellation — Perimeter", "memory")
            if not lyr.isValid():
                print("[Parcellation] perimeter layer invalid")
                return
            QgsProject.instance().addMapLayer(lyr)
            self._layers["perimeter"] = lyr
            lyr.startEditing()
            f = QgsFeature(lyr.fields())
            f.setGeometry(QgsGeometry.fromPolygonXY(
                [[QgsPointXY(e, n) for e, n in self._perimeter]]))
            lyr.addFeature(f)
            lyr.commitChanges()
            lyr.updateExtents()
            lyr.setRenderer(QgsSingleSymbolRenderer(
                QgsFillSymbol.createSimple({
                    "color": "0,0,0,0",
                    "outline_color": "30,120,60",
                    "outline_width": "1.2"})))
            lyr.triggerRepaint()
            print("[Parcellation] perimeter layer OK")
        except Exception as ex:
            print(f"[Parcellation] peri layer: {ex}")
            traceback.print_exc()

    def _layer_roads(self):
        try:
            self._del_layer("roads")
            uri = f"LineString?crs={self._crs()}"
            lyr = QgsVectorLayer(uri, "Parcellation — Road centrelines", "memory")
            if not lyr.isValid():
                return
            QgsProject.instance().addMapLayer(lyr)
            self._layers["roads"] = lyr
            lyr.startEditing()
            for rd in self._roads:
                f = QgsFeature(lyr.fields())
                f.setGeometry(QgsGeometry.fromPolylineXY(
                    [QgsPointXY(e, n) for e, n in rd]))
                lyr.addFeature(f)
            lyr.commitChanges()
            lyr.updateExtents()
            lyr.setRenderer(QgsSingleSymbolRenderer(
                QgsLineSymbol.createSimple({
                    "color": "200,0,0",
                    "line_width": "0.8",
                    "line_style": "dash"})))
            lyr.triggerRepaint()
        except Exception as ex:
            print(f"[Parcellation] road layer: {ex}")

    def _layer_result(self, result):
        try:
            # Build URI with field definitions embedded — most reliable approach
            uri = (f"Polygon?crs={self._crs()}"
                   "&field=plot_id:string"
                   "&field=area_m2:double"
                   "&field=label:string")

            # Remove old layer if it exists
            self._del_layer("plots")
            lyr = QgsVectorLayer(uri, "Parcellation — Plots", "memory")
            if not lyr.isValid():
                print("[Parcellation] plots layer invalid!")
                return

            QgsProject.instance().addMapLayer(lyr)
            self._layers["plots"] = lyr

            lyr.startEditing()
            for p in result.plots:
                f = QgsFeature(lyr.fields())
                pts = [QgsPointXY(e, n) for e, n in p.ring]
                f.setGeometry(QgsGeometry.fromPolygonXY([pts]))
                f.setAttribute("plot_id", p.plot_id)
                f.setAttribute("area_m2", round(p.area_m2, 2))
                f.setAttribute("label", f"{p.plot_id}\n{p.area_m2:.0f} m2")
                lyr.addFeature(f)
            lyr.commitChanges()
            lyr.updateExtents()

            # Style
            lyr.setRenderer(QgsSingleSymbolRenderer(
                QgsFillSymbol.createSimple({
                    "color": "100,180,255,60",
                    "outline_color": "0,80,160",
                    "outline_width": "0.5"})))

            # Labels
            try:
                pal = QgsPalLayerSettings()
                pal.fieldName = "label"
                pal.enabled   = True
                tf  = QgsTextFormat()
                tf.setSize(8)
                buf = QgsTextBufferSettings()
                buf.setEnabled(True)
                buf.setSize(0.8)
                buf.setColor(QColor(255, 255, 255))
                tf.setBuffer(buf)
                pal.setFormat(tf)
                lyr.setLabeling(QgsVectorLayerSimpleLabeling(pal))
                lyr.setLabelsEnabled(True)
            except Exception as label_err:
                print(f"[Parcellation] label error (non-fatal): {label_err}")

            lyr.triggerRepaint()
            print(f"[Parcellation] plots layer OK: {lyr.featureCount()} features")

            # Perimeter outline on top
            self._layer_peri()

            # Road polygons
            if result.road_polygons:
                road_uri = f"Polygon?crs={self._crs()}"
                self._del_layer("road_polys")
                lr = QgsVectorLayer(road_uri, "Parcellation — Roads", "memory")
                if lr.isValid():
                    QgsProject.instance().addMapLayer(lr)
                    self._layers["road_polys"] = lr
                    lr.startEditing()
                    for rp in result.road_polygons:
                        fx = QgsFeature(lr.fields())
                        fx.setGeometry(QgsGeometry.fromPolygonXY(
                            [[QgsPointXY(e, n) for e, n in rp]]))
                        lr.addFeature(fx)
                    lr.commitChanges()
                    lr.updateExtents()
                    lr.setRenderer(QgsSingleSymbolRenderer(
                        QgsFillSymbol.createSimple({
                            "color": "80,80,80,100",
                            "outline_color": "50,50,50",
                            "outline_width": "0.4"})))
                    lr.triggerRepaint()

            # Zoom to result
            self._zoom()
            self.canvas.refresh()

        except Exception as ex:
            print(f"[Parcellation] result layers error: {ex}")
            traceback.print_exc()

    def _del_layer(self, key):
        lyr = self._layers.pop(key, None)
        if lyr:
            try:
                QgsProject.instance().removeMapLayer(lyr.id())
            except Exception:
                pass

    def _del_layers(self):
        for k in list(self._layers):
            self._del_layer(k)

    # ── Tables ────────────────────────────────────────────────────────────

    def _fill_c(self, result):
        self.tbl_c.setRowCount(0)
        group_colors = {}
        peri_bg = QColor(230, 245, 235)   # light green -- boundary
        road_bg = QColor(235, 235, 240)   # light grey -- roads
        for c in result.all_corners_for_setting_out():
            r = self.tbl_c.rowCount()
            self.tbl_c.insertRow(r)
            items = [
                QTableWidgetItem(c["group"]),
                QTableWidgetItem(c["point"]),
                QTableWidgetItem(f"{c['E']:.3f}"),
                QTableWidgetItem(f"{c['N']:.3f}"),
            ]
            bg = None
            if c["group"] == "PERIMETER":
                bg = peri_bg
            elif c["group"].startswith("ROAD "):
                bg = road_bg
            if bg is not None:
                for item in items:
                    item.setBackground(QBrush(bg))
            for ci, item in enumerate(items):
                self.tbl_c.setItem(r, ci, item)

    def _fill_a(self, result):
        self.tbl_a.setRowCount(0)
        tot = 0.0
        green = QColor(220, 255, 220)
        orange = QColor(255, 240, 200)
        for s in result.area_schedule():
            r = self.tbl_a.rowCount()
            self.tbl_a.insertRow(r)
            flag = "✅" if s["compliant"] else "⚠"
            items = [
                QTableWidgetItem(str(s["no"])),
                QTableWidgetItem(f"{flag} {s['plot_id']}"),
                QTableWidgetItem(f"{s['area_m2']:.2f}"),
                QTableWidgetItem(f"{s['area_ha']:.4f}"),
            ]
            bg = green if s["compliant"] else orange
            for ci, item in enumerate(items):
                item.setBackground(QBrush(bg))
                self.tbl_a.setItem(r, ci, item)
            tot += s["area_m2"]
        n = result.summary["n_plots"]
        nc = result.summary.get("n_compliant", 0)
        ne = result.summary.get("n_edge", 0)
        self.lbl_tot.setText(
            f"Total: {tot:,.2f} m²  ({tot/10000:.4f} ha)  —  "
            f"{n} plots  (✅ {nc} compliant  ⚠ {ne} edge)")

    def _clr_tables(self):
        self.tbl_c.setRowCount(0)
        self.tbl_a.setRowCount(0)
        self.lbl_tot.setText("")

    def _copy_c(self):
        from qgis.PyQt.QtWidgets import QApplication
        rows = ["Plot\tPoint\tEasting\tNorthing"]
        for r in range(self.tbl_c.rowCount()):
            rows.append("\t".join(
                self.tbl_c.item(r, c).text() for c in range(4)))
        QApplication.clipboard().setText("\n".join(rows))
        self._st(f"Copied {self.tbl_c.rowCount()} rows")

    def _copy_a(self):
        from qgis.PyQt.QtWidgets import QApplication
        rows = ["No.\tPlot ID\tArea (m²)\tArea (ha)"]
        for r in range(self.tbl_a.rowCount()):
            rows.append("\t".join(
                self.tbl_a.item(r, c).text() for c in range(4)))
        QApplication.clipboard().setText("\n".join(rows))
        self._st(f"Copied {self.tbl_a.rowCount()} rows")

    # ── DXF export ────────────────────────────────────────────────────────

    def _export(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DXF", "", "AutoCAD DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            from .parcellation_dxf import export_parcellation_dxf
            export_parcellation_dxf(self._result, path)
            self._st(f"Exported: {os.path.basename(path)}")
            QMessageBox.information(self, "Done",
                f"DXF exported:\n{path}\n\n"
                f"{self._result.summary['n_plots']} plots")
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", str(ex))

    # ── Excel export ─────────────────────────────────────────────────────

    def _export_xlsx(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Excel", "", "Excel Workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            from .report_export import export_parcellation_xlsx
            export_parcellation_xlsx(self._result, path)
            self._st(f"Exported: {os.path.basename(path)}")
            QMessageBox.information(self, "Done",
                f"Excel workbook exported:\n{path}\n\n"
                f"Sheets: Area Schedule, Coordinates")
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", str(ex))

    # ── Report (PDF / print) ─────────────────────────────────────────────

    def _save_report_pdf(self):
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Report", "", "PDF Document (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            from .report_export import save_report_pdf
            save_report_pdf(self._result, path)
            self._st(f"Report saved: {os.path.basename(path)}")
            QMessageBox.information(self, "Done", f"Report saved:\n{path}")
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", str(ex))

    def _print_report(self):
        if not self._result:
            return
        try:
            from .report_export import print_report
            printed = print_report(self._result, self)
            if printed:
                self._st("Report sent to printer.")
        except Exception as ex:
            QMessageBox.critical(self, "Print Error", str(ex))

    # ── Utility ───────────────────────────────────────────────────────────

    def _st(self, msg):
        self.lbl_st.setText(msg)

    def closeEvent(self, e):
        if self._draw_tool:
            self.canvas.setMapTool(self._prev_tool)
        self._del_layers()
        super().closeEvent(e)