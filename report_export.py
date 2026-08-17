# -*- coding: utf-8 -*-
"""
report_export.py — Survey Management System

Two independent features, both operating on a ParcellationResult:

1. write_xlsx() -- a minimal, dependency-free .xlsx writer. The plugin
   already had one dependency-bundling headache with ezdxf (see the
   sys.path fix in parcellation_dxf.py); rather than repeat that pattern
   for openpyxl, this writes valid OOXML directly with stdlib only
   (zipfile + string templates, inline strings so no shared-strings
   table is needed). Good enough for a coordinates/area-schedule export
   -- not a general-purpose spreadsheet engine.

2. render_report() -- draws the parcellation plan (perimeter, roads,
   plots with labels) plus a summary block and a paginated coordinate
   table using QPainter, shared by both the "print to a real printer"
   and "save as PDF" flows in the dialog so there's exactly one place
   that lays out the report.
"""

from __future__ import annotations

import os
import zipfile

# Bump this on every change to render_report()/_render_to_printer() so a
# generated PDF's title block unambiguously proves which code version
# produced it. Given the repeated "identical result after a full restart"
# reports, this is now load-bearing: it turns "is this stale code or a new
# bug?" from a guess into something readable directly off the PDF.
REPORT_ENGINE_VERSION = "report-engine-v5-2026-08-15"
from datetime import datetime
from typing import List, Sequence, Union
from xml.sax.saxutils import escape

Cell = Union[str, int, float]


# --------------------------------------------------------------------------- #
# 1. Dependency-free XLSX writer
# --------------------------------------------------------------------------- #

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
{sheet_overrides}
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{sheet_rels}
<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2">
<font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><name val="Calibri"/></font>
</fonts>
<fills count="2">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
</fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _col_letter(idx: int) -> str:
    """0-indexed column number -> Excel column letter (0->A, 25->Z, 26->AA)."""
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _sheet_xml(rows: Sequence[Sequence[Cell]], header: bool = True) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetData>']
    for r_idx, row in enumerate(rows):
        style = ' s="1"' if (header and r_idx == 0) else ''
        cells = []
        for c_idx, val in enumerate(row):
            ref = f"{_col_letter(c_idx)}{r_idx + 1}"
            if isinstance(val, bool):
                cells.append(f'<c r="{ref}"{style} t="inlineStr"><is><t>{escape(str(val))}</t></is></c>')
            elif isinstance(val, (int, float)):
                cells.append(f'<c r="{ref}"{style} t="n"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref}"{style} t="inlineStr"><is><t>{escape(str(val))}</t></is></c>')
        out.append(f'<row r="{r_idx + 1}">' + "".join(cells) + "</row>")
    out.append("</sheetData></worksheet>")
    return "\n".join(out)


def write_xlsx(path: str, sheets: "dict[str, Sequence[Sequence[Cell]]]") -> None:
    """
    Write a minimal multi-sheet .xlsx file.

    sheets: ordered dict of {sheet_name: rows}, where rows is a list of
    lists of cell values (str/int/float). The first row of each sheet is
    treated as a bold header.
    """
    names = list(sheets.keys())

    sheet_overrides = "\n".join(
        f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(names)))

    sheet_rels = "\n".join(
        f'<Relationship Id="rId{i+1}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i+1}.xml"/>'
        for i in range(len(names)))

    workbook_sheets = "\n".join(
        f'<sheet name="{escape(name)}" sheetId="{i+1}" r:id="rId{i+1}"/>'
        for i, name in enumerate(names))

    workbook_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>
{workbook_sheets}
</sheets>
</workbook>"""

    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES.format(sheet_overrides=sheet_overrides))
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS.format(sheet_rels=sheet_rels))
        z.writestr("xl/styles.xml", _STYLES)
        for i, name in enumerate(names):
            z.writestr(f"xl/worksheets/sheet{i+1}.xml", _sheet_xml(sheets[name]))


def export_parcellation_xlsx(result: "ParcellationResult", path: str) -> None:
    """Write the area schedule + full setting-out coordinate table
    (plots + perimeter + roads) as a 2-sheet workbook."""
    area_rows: List[List[Cell]] = [["No.", "Plot ID", "Area (m2)", "Area (ha)", "Status"]]
    for s in result.area_schedule():
        area_rows.append([
            s["no"], s["plot_id"], round(s["area_m2"], 2), round(s["area_ha"], 4),
            "Compliant" if s["compliant"] else "Edge/partial",
        ])

    coord_rows: List[List[Cell]] = [["Group", "Point", "Easting", "Northing"]]
    for c in result.all_corners_for_setting_out():
        coord_rows.append([c["group"], c["point"], c["E"], c["N"]])

    write_xlsx(path, {"Area Schedule": area_rows, "Coordinates": coord_rows})


# --------------------------------------------------------------------------- #
# 2. Report rendering (shared by print + PDF export)
# --------------------------------------------------------------------------- #

# ---- Shared visual theme -------------------------------------------------- #
# One palette + one set of table-geometry constants, reused by the chrome
# bars, the plan legend, and the table drawer, so the whole report reads as
# one consistent document instead of several ad-hoc pieces.
_BRAND_DARK = (26, 92, 56)        # header/footer bars, perimeter outline
_BRAND_TEXT_ON_DARK = (255, 255, 255)
_HEADER_ROW_FILL = (26, 92, 56)
_ZEBRA_FILL = (240, 245, 242)
_RULE_COLOR = (200, 200, 200)
_MUTED_TEXT = (110, 110, 110)
_STATUS_GOOD = (35, 120, 60)
_STATUS_WARN = (185, 110, 15)
_KIND_COLORS = {
    "standard": (93, 168, 242),
    "merged": (95, 191, 107),
    "reduced": (179, 157, 219),
    "fringe": (244, 160, 32),
}
_KIND_LABELS = [
    ("standard", "Standard plot"),
    ("merged", "Merged plot"),
    ("reduced", "Reduced plot"),
    ("fringe", "Fringe/edge plot"),
    ("road", "Road corridor"),
]

_TABLE_ROW_H = 14
_TABLE_TITLE_H = 16
_TABLE_HEADER_ROW_H = 15
_TABLE_HEADER_H = _TABLE_TITLE_H + _TABLE_HEADER_ROW_H  # space before the first data row
_CHROME_HEADER_H = 20
_CHROME_FOOTER_H = 16
_CHROME_GAP = 6


def _text_width(painter, text):
    fm = painter.fontMetrics()
    return fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text)


def _draw_aligned_text(painter, text, x, y, col_width, align):
    """Draw `text` left/center/right-aligned within a column of the given
    width starting at x (used for table cells, so numbers line up on their
    ones digit instead of drifting left with the labels)."""
    if align == "r":
        painter.drawText(int(x + col_width - _text_width(painter, text) - 4), int(y), text)
    elif align == "c":
        painter.drawText(int(x + (col_width - _text_width(painter, text)) / 2), int(y), text)
    else:
        painter.drawText(int(x + 2), int(y), text)


def _pt_font(size, bold=False):
    """Build a QFont sized in report 'points' without Qt's automatic
    point->device-pixel DPI conversion.

    QFont.setPointSize() bakes in a conversion to the *target device's*
    real DPI at draw time -- but render_report() already applies its own
    explicit painter.scale(dpi/72) to move every coordinate (and, along
    with it, every glyph) from point-space into device pixels. Using
    setPointSize() here would apply that same dpi/72 factor a second
    time, inflating a 16pt title to ~200-270px on a HighResolution
    QPrinter (dpi ~1200) -- exactly the giant, overlapping text seen in
    the exported report. setPixelSize() is a literal size with no DPI
    conversion, so it only gets scaled the one time, by our own
    painter.scale() call, and comes out at the intended point size."""
    from qgis.PyQt.QtGui import QFont
    font = QFont("Arial")
    font.setPixelSize(size)
    if bold:
        font.setBold(True)
    return font


def render_report(painter, page_rect, result: "ParcellationResult"):
    """
    Draw the full report into `painter` across one or more pages of a
    QPrinter.

    page_rect: QRectF, the printable page area IN POINTS (1/72 inch),
        pre-computed by the caller from the device's actual pixel
        dimensions (see _render_to_printer) -- NOT from
        QPrinter.pageRect(QPrinter.Point), which is deprecated since
        Qt 5.3 and was verified to disagree with the printer's real
        orientation (landscape jobs still came back in portrait
        proportions), causing every point-based coordinate in this
        function to be stretched by the wrong, swapped aspect ratio --
        that's what turned a normal 16pt title into the huge distorted
        overlapping text seen in testing.

    This function establishes its OWN point->device-pixel scale (derived
    straight from painter.device().logicalDpiX/Y(), which is NOT
    deprecated and is always consistent with the device's actual
    orientation) and re-applies it after every newPage(), since newPage()
    resets the painter's transform just like it resets setWindow() --
    verified separately as the cause of the "clean page 1, garbled every
    page after" bug in an earlier round of this fix.
    """
    from qgis.PyQt.QtGui import QColor, QPen, QBrush
    from qgis.PyQt.QtCore import Qt, QRectF

    device = painter.device()
    dpi_x = device.logicalDpiX() or 96
    dpi_y = device.logicalDpiY() or 96
    scale_x = dpi_x / 72.0
    scale_y = dpi_y / 72.0

    def _apply_point_scale():
        painter.resetTransform()
        painter.scale(scale_x, scale_y)

    _apply_point_scale()

    margin = page_rect.width() * 0.03
    page_frame = QRectF(page_rect.left() + margin, page_rect.top() + margin,
                         page_rect.width() - 2 * margin, page_rect.height() - 2 * margin)

    s = result.summary
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Pre-build the two coordinate tables' row data up front (not just at
    # draw time) so we can work out how many pages each will need *before*
    # drawing page 1 -- that's what lets the header on every page show an
    # accurate "Page N of M" instead of an unknown total.
    area_rows_data = [[row["no"], row["plot_id"], f"{row['area_m2']:.2f}",
                        f"{row['area_ha']:.4f}",
                        "Compliant" if row["compliant"] else "Edge/partial"]
                       for row in result.area_schedule()]
    coord_rows_data = [[c["group"], c["point"], f"{c['E']:.3f}", f"{c['N']:.3f}"]
                        for c in result.all_corners_for_setting_out()]

    body_h_estimate = page_frame.height() - _CHROME_HEADER_H - _CHROME_FOOTER_H - 2 * _CHROME_GAP
    rows_per_page = max(1, int((body_h_estimate - _TABLE_HEADER_H) // _TABLE_ROW_H))

    def _pages_needed(n_rows):
        return 1 if n_rows == 0 else -(-n_rows // rows_per_page)  # ceil division

    total_pages = 1 + _pages_needed(len(area_rows_data)) + _pages_needed(len(coord_rows_data))
    page_no = [1]

    def _draw_chrome(section_label):
        """Slim branded header bar + footer bar drawn on every page, so the
        report reads as one consistent document rather than loose sheets.
        Returns the body QRectF -- the usable area below the header bar and
        above the footer bar -- that the rest of this page should draw into."""
        header_rect = QRectF(page_frame.left(), page_frame.top(),
                              page_frame.width(), _CHROME_HEADER_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(*_BRAND_DARK)))
        painter.drawRect(header_rect)
        painter.setFont(_pt_font(8, bold=True))
        painter.setPen(QPen(QColor(*_BRAND_TEXT_ON_DARK)))
        painter.drawText(int(header_rect.left() + 6), int(header_rect.top() + 14),
                          "SURVEY MANAGEMENT SYSTEM  \u2013  PARCELLATION REPORT")
        if section_label:
            painter.setFont(_pt_font(7))
            w = _text_width(painter, section_label)
            painter.drawText(int(header_rect.right() - w - 6), int(header_rect.top() + 14),
                              section_label)

        footer_top = page_frame.bottom() - _CHROME_FOOTER_H
        painter.setPen(QPen(QColor(*_RULE_COLOR), 0.6))
        painter.drawLine(int(page_frame.left()), int(footer_top),
                          int(page_frame.right()), int(footer_top))
        painter.setFont(_pt_font(6))
        painter.setPen(QPen(QColor(*_MUTED_TEXT)))
        painter.drawText(int(page_frame.left()), int(footer_top + 12),
                          f"Generated {generated_at}   [{REPORT_ENGINE_VERSION}]")
        page_label = f"Page {page_no[0]} of {total_pages}"
        w = _text_width(painter, page_label)
        painter.drawText(int(page_frame.right() - w), int(footer_top + 12), page_label)

        # Thin frame around the whole page -- gives every sheet a defined
        # edge instead of content floating on blank paper.
        painter.setPen(QPen(QColor(*_RULE_COLOR), 0.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(page_frame)

        return QRectF(page_frame.left(), header_rect.bottom() + _CHROME_GAP,
                       page_frame.width(), footer_top - header_rect.bottom() - 2 * _CHROME_GAP)

    def _next_page(section_label=""):
        device.newPage()
        _apply_point_scale()
        page_no[0] += 1
        return _draw_chrome(section_label)

    body = _draw_chrome("Plan Overview")

    # ---- Page 1: title + summary + plan ----
    y = body.top()

    title_font = _pt_font(16, bold=True)
    painter.setFont(title_font)
    painter.setPen(QPen(QColor(0, 0, 0)))
    painter.drawText(int(body.left()), int(y + 18), "PARCELLATION PLAN")
    y += 26

    sub_font = _pt_font(9)
    painter.setFont(sub_font)
    summary_lines = [
        f"Total plots: {s.get('n_plots', 0)}    "
        f"Standard: {s.get('n_standard', s.get('n_compliant', 0))}    "
        f"Merged: {s.get('n_merged', 0)}    "
        f"Reduced: {s.get('n_reduced', 0)}    "
        f"Landlocked: {s.get('n_landlocked', 0)}",
        f"Perimeter area: {s.get('perimeter_area_m2', 0):,.1f} m2    "
        f"Road area: {s.get('road_area_m2', 0):,.1f} m2    "
        f"Coverage: {s.get('coverage_pct', 0)}%",
        f"Target plot area: {s.get('target_plot_area_m2', 0):,.1f} m2",
    ]
    for line in summary_lines:
        painter.drawText(int(body.left()), int(y + 10), line)
        y += 13
    y += 6

    # ---- Plan drawing: fit perimeter+plots+roads into the remaining
    #      space on this page, with perimeter corner labels and a road
    #      label per road piece so a surveyor can tie the drawing
    #      directly to the coordinate tables that follow ----
    plan_top = y
    legend_h = 14
    plan_rect = QRectF(body.left(), plan_top, body.width(),
                        body.bottom() - plan_top - legend_h)

    xs = [pt[0] for pt in result.perimeter]
    ys = [pt[1] for pt in result.perimeter]
    for rp in result.road_polygons:
        xs += [pt[0] for pt in rp]
        ys += [pt[1] for pt in rp]
    # Access annotations are drawn deliberately OUTSIDE the perimeter, so
    # they must be included in the bounding box here too -- otherwise the
    # scale/origin below is computed from the perimeter alone and the
    # access line would land off the edge of the plan area.
    access_lines = getattr(result, "access_lines", [])
    for al in access_lines:
        xs += [pt[0] for pt in al]
        ys += [pt[1] for pt in al]
    if xs and ys:
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        gw, gh = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
        scale = min(plan_rect.width() / gw, plan_rect.height() / gh) * 0.96
        ox = plan_rect.left() + (plan_rect.width() - gw * scale) / 2
        oy = plan_rect.top() + (plan_rect.height() - gh * scale) / 2

        def to_px(pt):
            # flip Y (screen y grows downward, survey N grows upward)
            return (ox + (pt[0] - minx) * scale,
                    oy + (maxy - pt[1]) * scale)

        # ---- OSM basemap, drawn first so everything else layers on top.
        #      The fetch extent is the INVERSE of to_px applied to the
        #      full plan_rect (not just the tight content bbox) -- this
        #      guarantees the basemap image aligns pixel-for-pixel with
        #      the roads/plots/perimeter drawn afterward via to_px,
        #      rather than fetching a separately-fitted extent that could
        #      drift out of alignment with the vector overlay. ----
        basemap_drawn = False
        if getattr(result, "show_basemap", False):
            def _px_to_world(x_px, y_px):
                return (minx + (x_px - ox) / scale, maxy - (y_px - oy) / scale)

            bm_minx, bm_maxy = _px_to_world(plan_rect.left(), plan_rect.top())
            bm_maxx, bm_miny = _px_to_world(plan_rect.right(), plan_rect.bottom())
            dest_crs = getattr(result, "crs_obj", None)
            basemap_img = None
            if dest_crs is not None and dest_crs.isValid():
                basemap_img = _render_osm_basemap(
                    bm_minx, bm_miny, bm_maxx, bm_maxy, dest_crs,
                    px_w=plan_rect.width() * 4, px_h=plan_rect.height() * 4)
            if basemap_img is not None:
                painter.drawImage(QRectF(plan_rect.left(), plan_rect.top(),
                                          plan_rect.width(), plan_rect.height()),
                                   basemap_img)
                basemap_drawn = True

        # Roads, each with a small centroid label ("ROAD n")
        painter.setBrush(QBrush(QColor(217, 217, 217)))
        painter.setPen(QPen(QColor(136, 136, 136), 0.5))
        road_label_font = _pt_font(5)
        for ri, rp in enumerate(result.road_polygons):
            poly = _qpolygon(rp, to_px)
            if poly:
                painter.drawPolygon(poly)
                cx, cy = _centroid_px(rp, to_px)
                painter.setFont(road_label_font)
                painter.setPen(QPen(QColor(80, 80, 80)))
                painter.drawText(int(cx - 8), int(cy), f"R{ri+1}")
                painter.setPen(QPen(QColor(136, 136, 136), 0.5))

        # Plots
        painter.setPen(QPen(QColor(0, 0, 0), 0.4))
        label_font = _pt_font(5)
        painter.setFont(label_font)
        for p in result.plots:
            kind = getattr(p, "kind", "standard" if getattr(p, "compliant", False) else "reduced")
            painter.setBrush(QBrush(QColor(*_KIND_COLORS.get(kind, _KIND_COLORS["standard"]))))
            poly = _qpolygon(p.ring, to_px)
            if poly:
                painter.drawPolygon(poly)
                cx, cy = to_px(p.centroid_pt)
                painter.setPen(QPen(QColor(0, 0, 0)))
                painter.drawText(int(cx - 8), int(cy), str(p.plot_id))
                painter.setPen(QPen(QColor(0, 0, 0), 0.4))

        # Perimeter outline on top, with a numbered label at every
        # boundary corner (BP1, BP2, ...) matching perimeter_corner_table()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(26, 92, 56), 1.5))
        poly = _qpolygon(result.perimeter, to_px)
        if poly:
            painter.drawPolygon(poly)

        peri_ring = list(result.perimeter)
        if peri_ring and peri_ring[0] == peri_ring[-1]:
            peri_ring = peri_ring[:-1]
        bp_font = _pt_font(5, bold=True)
        painter.setFont(bp_font)
        painter.setPen(QPen(QColor(26, 92, 56)))
        for i, pt in enumerate(peri_ring):
            px, py = to_px(pt)
            painter.drawText(int(px + 3), int(py - 3), f"BP{i+1}")

        # ---- Existing-access annotations: reference-only lines showing
        #      where a real road outside the perimeter meets it. Drawn
        #      dashed/amber to read clearly as "not part of the
        #      subdivision" -- matching the same colour used for this in
        #      the QGIS canvas layer, so the plan and the live dialog agree. ----
        if access_lines:
            access_font = _pt_font(5, bold=True)
            access_pen = QPen(QColor(244, 160, 32), 1.2)
            access_pen.setStyle(Qt.DashLine)
            for ai, al in enumerate(access_lines):
                pts_px = [to_px(pt) for pt in al]
                if len(pts_px) < 2:
                    continue
                painter.setPen(access_pen)
                for j in range(len(pts_px) - 1):
                    painter.drawLine(int(pts_px[j][0]), int(pts_px[j][1]),
                                      int(pts_px[j+1][0]), int(pts_px[j+1][1]))
                mx, my = pts_px[len(pts_px) // 2]
                painter.setFont(access_font)
                painter.setPen(QPen(QColor(180, 110, 10)))
                label = "Existing Access" if len(access_lines) == 1 else f"Existing Access {ai+1}"
                painter.drawText(int(mx + 4), int(my - 4), label)

        # ---- OSM attribution -- required by OSM's licence whenever the
        #      basemap actually rendered; omitted entirely if it didn't
        #      (no basemap drawn = nothing to attribute). Small white
        #      backing strip so it stays legible over varying imagery. ----
        if basemap_drawn:
            attrib_font = _pt_font(4)
            painter.setFont(attrib_font)
            attrib_text = "\u00a9 OpenStreetMap contributors"
            tw = _text_width(painter, attrib_text)
            ax = plan_rect.left() + 2
            ay = plan_rect.bottom() - 3
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(255, 255, 255, 210)))
            painter.drawRect(int(ax - 2), int(ay - 8), int(tw + 4), 10)
            painter.setPen(QPen(QColor(60, 60, 60)))
            painter.drawText(int(ax), int(ay), attrib_text)

        # ---- Legend: colour key for the plot kinds + roads, directly
        #      under the plan so it's readable without a separate page ----
        legend_font = _pt_font(6)
        painter.setFont(legend_font)
        lx = plan_rect.left()
        ly = plan_rect.bottom() + 11
        swatch = 7
        for kind, label in _KIND_LABELS:
            if kind == "road":
                painter.setPen(QPen(QColor(136, 136, 136), 0.5))
                painter.setBrush(QBrush(QColor(217, 217, 217)))
            else:
                painter.setPen(QPen(QColor(0, 0, 0), 0.4))
                painter.setBrush(QBrush(QColor(*_KIND_COLORS[kind])))
            painter.drawRect(int(lx), int(ly - swatch), swatch, swatch)
            painter.setPen(QPen(QColor(0, 0, 0)))
            painter.drawText(int(lx + swatch + 4), int(ly), label)
            lx += swatch + 4 + _text_width(painter, label) + 16

        if access_lines:
            painter.setPen(QPen(QColor(244, 160, 32), 1.2))
            pen = painter.pen(); pen.setStyle(Qt.DashLine); painter.setPen(pen)
            painter.drawLine(int(lx), int(ly - swatch // 2), int(lx + 14), int(ly - swatch // 2))
            painter.setPen(QPen(QColor(0, 0, 0)))
            painter.drawText(int(lx + 18), int(ly), "Existing access (reference only)")

    # ---- Following page(s): area schedule ----
    body = _next_page("Schedule of Areas")
    _draw_table_pages(
        painter, body, _next_page,
        title="SCHEDULE OF AREAS",
        headers=["No.", "Plot ID", "Area (m2)", "Area (ha)", "Status"],
        aligns=["c", "l", "r", "r", "l"],
        col_fracs=[0.08, 0.18, 0.20, 0.20, 0.34],
        rows=area_rows_data,
        section_label="Schedule of Areas",
    )

    # ---- Following page(s): full setting-out coordinate table --
    # plots, then perimeter, then each road -- everything a surveyor
    # needs to stake the design out on site.
    body = _next_page("Setting-Out Coordinates")
    _draw_table_pages(
        painter, body, _next_page,
        title="SETTING-OUT COORDINATES",
        headers=["Group", "Point", "Easting (m)", "Northing (m)"],
        aligns=["l", "c", "r", "r"],
        col_fracs=[0.30, 0.20, 0.25, 0.25],
        rows=coord_rows_data,
        section_label="Setting-Out Coordinates",
    )


def _draw_table_pages(painter, body, next_page, title, headers, aligns, col_fracs, rows, section_label):
    """Generic paginated table drawer, in point-units (see render_report's
    docstring for why that matters). Shared by the area schedule and the
    setting-out coordinate table so pagination logic lives in one place.
    next_page: callback that starts a new page (chrome + all) and returns
    the new body QRectF to draw into -- must be used instead of calling
    device.newPage() directly."""
    from qgis.PyQt.QtGui import QColor, QPen, QBrush
    from qgis.PyQt.QtCore import Qt, QRectF

    row_h = _TABLE_ROW_H
    title_h = _TABLE_TITLE_H
    header_row_h = _TABLE_HEADER_ROW_H

    def draw_header(body, heading):
        painter.setFont(_pt_font(11, bold=True))
        painter.setPen(QPen(QColor(0, 0, 0)))
        painter.drawText(int(body.left()), int(body.top() + 12), heading)

        header_row_top = body.top() + title_h + 4
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(*_HEADER_ROW_FILL)))
        painter.drawRect(QRectF(body.left(), header_row_top, body.width(), header_row_h))

        painter.setFont(_pt_font(8, bold=True))
        painter.setPen(QPen(QColor(*_BRAND_TEXT_ON_DARK)))
        x = body.left()
        for h, frac, align in zip(headers, col_fracs, aligns):
            w = body.width() * frac
            _draw_aligned_text(painter, h, x, header_row_top + 11, w, align)
            x += w
        return header_row_top + header_row_h  # top of first data row

    cursor_y = draw_header(body, title)
    painter.setFont(_pt_font(7))
    max_y = body.bottom()

    for row_idx, vals in enumerate(rows):
        if cursor_y + row_h > max_y:
            body = next_page(section_label)
            cursor_y = draw_header(body, f"{title} (cont'd)")
            painter.setFont(_pt_font(7))
            max_y = body.bottom()

        row_top = cursor_y
        if row_idx % 2 == 1:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(*_ZEBRA_FILL)))
            painter.drawRect(QRectF(body.left(), row_top, body.width(), row_h))

        x = body.left()
        baseline = row_top + row_h - 4
        for col_idx, (v, frac, align) in enumerate(zip(vals, col_fracs, aligns)):
            w = body.width() * frac
            text = str(v)
            if headers[col_idx] == "Status":
                painter.setPen(QPen(QColor(*(_STATUS_GOOD if text == "Compliant" else _STATUS_WARN))))
            else:
                painter.setPen(QPen(QColor(30, 30, 30)))
            _draw_aligned_text(painter, text, x, baseline, w, align)
            x += w
        cursor_y += row_h

    painter.setPen(QPen(QColor(*_RULE_COLOR), 0.6))
    painter.drawLine(int(body.left()), int(cursor_y + 1), int(body.right()), int(cursor_y + 1))


def _centroid_px(ring, to_px):
    """Approximate centroid (average of vertices) in already-projected
    page coordinates -- good enough for label placement."""
    pts = [to_px(pt) for pt in ring]
    if not pts:
        return (0, 0)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return (cx, cy)


def _qpolygon(ring, to_px):
    from qgis.PyQt.QtGui import QPolygonF
    from qgis.PyQt.QtCore import QPointF
    if not ring:
        return None
    pts = [QPointF(*to_px(pt)) for pt in ring]
    return QPolygonF(pts)


def _render_osm_basemap(minx, miny, maxx, maxy, dest_crs, px_w, px_h, timeout_ms=12000):
    """Render an OSM XYZ basemap for the given extent to a QImage, entirely
    in-process via QGIS's own map renderer (handles reprojection from OSM's
    native Web Mercator into the destination CRS, and tile fetch/caching).

    dest_crs is a QgsCoordinateReferenceSystem object, not a string -- the
    caller (parcellation_dialog.py) gets it directly from the Working CRS
    picker widget, so there's no string round-trip and no "which EPSG
    string format does this API accept" guessing anywhere in this path.

    Returns None on ANY failure -- no network, DNS failure, slow tile
    server, whatever -- so the caller falls back to today's vector-only
    plan with zero behaviour change. This is a deliberate product decision:
    report generation must never hang or error out because a tile server
    is unreachable, given this plugin's real-world use in areas with
    unreliable connectivity. Bounded by timeout_ms via an event-loop timer
    rather than relying on any per-job timeout API, since QGIS's map
    renderer job doesn't expose one directly across versions.

    timeout_ms defaults to 12s, not 4s -- a *fresh*, uncached tile fetch at
    4x-oversampled print resolution can genuinely take several seconds,
    and report generation is a one-shot action the user already expects
    to take a moment, unlike an interactive canvas redraw. If the basemap
    still doesn't appear, check the QGIS Log Messages panel (View ->
    Panels -> Log Messages) for a "Parcellation" entry -- every failure
    path below logs the real reason instead of swallowing it silently.
    """
    def _log(msg):
        try:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(msg, "Parcellation", Qgis.Warning)
        except Exception:
            pass

    try:
        from qgis.core import (QgsRasterLayer, QgsRectangle, QgsMapSettings,
                                QgsMapRendererCustomPainterJob)
        from qgis.PyQt.QtGui import QImage, QPainter as QtPainter, QColor as QtColor
        from qgis.PyQt.QtCore import QSize, QEventLoop, QTimer

        osm_uri = ("type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/"
                   "%7By%7D.png&zmax=19&zmin=0")
        osm = QgsRasterLayer(osm_uri, "osm_report_basemap_tmp", "wms")
        if not osm.isValid():
            _log(f"basemap: OSM layer invalid ({osm.error().summary() if osm.error() else 'unknown'})")
            return None

        if dest_crs is None or not dest_crs.isValid():
            _log(f"basemap: destination CRS is not valid ({dest_crs})")
            return None

        ms = QgsMapSettings()
        ms.setDestinationCrs(dest_crs)
        ms.setLayers([osm])
        ms.setBackgroundColor(QtColor(255, 255, 255))
        ms.setOutputSize(QSize(max(1, int(px_w)), max(1, int(px_h))))
        ms.setExtent(QgsRectangle(minx, miny, maxx, maxy))

        img = QImage(ms.outputSize(), QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QtPainter(img)

        loop = QEventLoop()
        job = QgsMapRendererCustomPainterJob(ms, p)
        job.finished.connect(loop.quit)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)
        job.start()
        loop.exec_()
        timer.stop()

        if job.isActive():
            job.cancel()
            p.end()
            _log(f"basemap: timed out after {timeout_ms}ms (fresh tile "
                 "fetch may just need longer -- see timeout_ms)")
            return None
        p.end()
        return img
    except Exception as ex:
        # Never let a basemap failure break report generation -- but do
        # log what actually happened instead of failing silently with no
        # trace at all.
        import traceback
        _log(f"basemap: unexpected error: {ex}\n{traceback.format_exc()}")
        return None


def _setup_landscape_printer():
    """Build a QPrinter and, in a version-tolerant way, set it to
    landscape (older PyQt uses printer.setOrientation(QPrinter.Landscape);
    newer PyQt5/Qt6 use QPageLayout instead)."""
    from qgis.PyQt.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.HighResolution)
    try:
        from qgis.PyQt.QtGui import QPageLayout
        printer.setPageOrientation(QPageLayout.Landscape)
    except Exception:
        try:
            printer.setOrientation(QPrinter.Landscape)
        except Exception:
            pass
    return printer


def _render_to_printer(printer, result):
    """Shared drawing entry point. Computes the page size in points from
    the printer's actual pixel width()/height() and logicalDpiX/Y() --
    always self-consistent with the real device orientation, unlike
    QPrinter.pageRect(QPrinter.Point) (deprecated since Qt 5.3; verified
    to disagree with landscape orientation in testing, stretching every
    coordinate in the report by the wrong aspect ratio). render_report()
    derives the same scale internally and applies it via painter.scale(),
    so this just needs to hand it a correctly-proportioned page_rect."""
    from qgis.PyQt.QtGui import QPainter
    from qgis.PyQt.QtCore import QRectF

    painter = QPainter(printer)
    try:
        dpi_x = printer.logicalDpiX() or 96
        dpi_y = printer.logicalDpiY() or 96
        page_w_pt = printer.width() / (dpi_x / 72.0)
        page_h_pt = printer.height() / (dpi_y / 72.0)
        page_rect = QRectF(0, 0, page_w_pt, page_h_pt)
        render_report(painter, page_rect, result)
    finally:
        painter.end()


def save_report_pdf(result: "ParcellationResult", path: str) -> None:
    """Render the report straight to a PDF file (no print dialog)."""
    from qgis.PyQt.QtPrintSupport import QPrinter

    printer = _setup_landscape_printer()
    printer.setOutputFormat(QPrinter.PdfFormat)
    printer.setOutputFileName(path)
    _render_to_printer(printer, result)


def print_report(result: "ParcellationResult", parent=None) -> bool:
    """Open a print dialog and, if accepted, print the report. Returns
    True if printing was actually performed."""
    from qgis.PyQt.QtPrintSupport import QPrintDialog

    printer = _setup_landscape_printer()
    dlg = QPrintDialog(printer, parent)
    if dlg.exec_() != QPrintDialog.Accepted:
        return False

    _render_to_printer(printer, result)
    return True
