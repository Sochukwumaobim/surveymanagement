# -*- coding: utf-8 -*-
"""
parcellation_dxf.py — Survey Management System v2.0
DXF export for parcellation results.

Exports:
  - PERIMETER layer: the outer boundary
  - PERIMETER_BEACONS / PERIMETER_LABELS: boundary corner beacons + E/N
    coordinate text (BP1, BP2, ... -- matches perimeter_corner_table())
  - ROAD layer: road corridor polygons
  - ROAD_LABELS: road piece ID at centroid (ROAD 1, ROAD 2, ...) plus
    corner beacons + E/N coordinate text at each road corner
  - PLOT_BOUNDARY layer: individual plot polygons
  - PLOT_LABELS layer: plot ID + area text at centroid
  - PLOT_BEACONS layer: corner point markers
  - COORDINATES layer: coordinate annotation text at each plot corner

Every point a surveyor needs to stake out on site -- plot corners,
boundary corners, and road corners -- gets its own beacon marker and
E/N coordinate label, on separate layers so they can be toggled
independently in AutoCAD/QGIS without cluttering the drawing.
"""

import math
from typing import List
from .parcellation_engine import ParcellationResult, Ring, Pt, _ensure_open


def export_parcellation_dxf(result: ParcellationResult, path: str):
    """
    Export a ParcellationResult to an AutoCAD DXF file.
    Uses ezdxf for reliable DXF output.
    """
    try:
        # The plugin ships ezdxf bundled in lib/, but that folder only gets
        # added to sys.path lazily -- previously only the DXF *import* flow
        # did that (dxf_importer._ensure_ezdxf()), so export would raise
        # ImportError on a fresh QGIS session even though ezdxf was right
        # there in lib/. Reuse the same path-fixing helper here so export
        # works standalone, without needing an import first.
        from .dxf_importer import _ensure_ezdxf
        ezdxf = _ensure_ezdxf()
    except ImportError as e:
        raise ImportError(
            "ezdxf is required for DXF export, and could not be found "
            "even in the plugin's bundled lib/ folder.\n\n"
            f"{e}\n\n"
            "Try reinstalling the plugin, or run in the OSGeo4W Shell:\n"
            "  python -m pip install ezdxf")

    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 6      # metres
    doc.header["$MEASUREMENT"] = 1   # metric

    msp = doc.modelspace()

    # Create layers
    _make_layer(doc, "PERIMETER",         color=3,  lw=50)   # green, thick
    _make_layer(doc, "PERIMETER_BEACONS", color=3,  lw=13)
    _make_layer(doc, "PERIMETER_LABELS",  color=3,  lw=9)
    _make_layer(doc, "ROAD",              color=8,  lw=30)   # dark grey
    _make_layer(doc, "ROAD_BEACONS",      color=8,  lw=13)
    _make_layer(doc, "ROAD_LABELS",       color=8,  lw=9)
    _make_layer(doc, "PLOT_BOUNDARY",     color=5,  lw=13)   # blue
    _make_layer(doc, "PLOT_LABELS",       color=2,  lw=13)   # yellow/text
    _make_layer(doc, "PLOT_BEACONS",      color=1,  lw=13)   # red
    _make_layer(doc, "COORDINATES",       color=7,  lw=9)    # white/small text

    # 1 — Perimeter: outline + a beacon and E/N label at every boundary
    #     corner (BP1, BP2, ... matches ParcellationResult.perimeter_corner_table())
    peri_ring = _ensure_open(result.perimeter)
    _add_closed_polyline(msp, peri_ring, "PERIMETER")
    for i, pt in enumerate(peri_ring):
        msp.add_circle(center=pt, radius=0.4,
                        dxfattribs={"layer": "PERIMETER_BEACONS"})
        label = f"BP{i+1}: E={pt[0]:.3f}\nN={pt[1]:.3f}"
        _add_text(msp, pt, label, height=1.0,
                  layer="PERIMETER_LABELS", offset=(0.6, 0.6))

    # 2 — Road polygons: each disjoint road piece gets a centroid label
    #     (ROAD 1, ROAD 2, ...) plus a beacon and E/N label at every
    #     corner, matching ParcellationResult.road_corner_tables()
    for ri, rp in enumerate(result.road_polygons):
        _add_closed_polyline(msp, rp, "ROAD")
        rring = _ensure_open(rp)
        if rring:
            cx = sum(p[0] for p in rring) / len(rring)
            cy = sum(p[1] for p in rring) / len(rring)
            _add_text(msp, (cx, cy), f"ROAD {ri+1}", height=1.2, layer="ROAD_LABELS")
        for i, pt in enumerate(rring):
            msp.add_circle(center=pt, radius=0.3,
                            dxfattribs={"layer": "ROAD_BEACONS"})
            label = f"R{ri+1}P{i+1}: E={pt[0]:.3f}\nN={pt[1]:.3f}"
            _add_text(msp, pt, label, height=0.8,
                      layer="ROAD_LABELS", offset=(0.5, 0.5))

    # 3 — Plot boundaries + labels + beacons
    for plot in result.plots:
        ring = _ensure_open(plot.ring)

        # Plot boundary polyline
        _add_closed_polyline(msp, ring, "PLOT_BOUNDARY")

        # Beacon markers at each corner
        for pt in ring:
            msp.add_circle(
                center=pt,
                radius=0.3,
                dxfattribs={"layer": "PLOT_BEACONS"}
            )

        # Coordinate labels at each corner
        for i, pt in enumerate(ring):
            label = f"P{i+1}: E={pt[0]:.3f}\nN={pt[1]:.3f}"
            _add_text(msp, pt, label, height=0.8,
                      layer="COORDINATES", offset=(0.5, 0.5))

        # Plot label at centroid: plot_id + area
        cx, cy = plot.centroid_pt
        area_text = f"{plot.plot_id}"
        area_line2 = f"{plot.area_m2:.1f} m\u00b2"
        _add_text(msp, (cx, cy + 1.5), area_text,
                  height=1.5, layer="PLOT_LABELS")
        _add_text(msp, (cx, cy - 0.5), area_line2,
                  height=1.2, layer="PLOT_LABELS")

    # 4 — Title block (bottom left of drawing)
    xs = [p[0] for p in result.perimeter]
    ys = [p[1] for p in result.perimeter]
    tx = min(xs)
    ty = min(ys) - 20

    summary = result.summary
    title_lines = [
        f"PARCELLATION PLAN",
        f"Plots: {summary['n_plots']}   Blocks: {summary['n_loops']}",
        f"Perimeter area: {summary['perimeter_area_m2']:,.1f} m²",
        f"Target plot area: {summary['target_plot_area_m2']:,.1f} m²",
        f"Generated by Survey Management System v2.0",
    ]
    _make_layer(doc, "TITLE", color=7, lw=13)
    for i, line in enumerate(title_lines):
        h = 3.0 if i == 0 else 1.8
        _add_text(msp, (tx, ty - i * 4), line, height=h, layer="TITLE")

    doc.saveas(path)


def _make_layer(doc, name: str, color: int = 7, lw: int = 13):
    if name not in doc.layers:
        doc.layers.new(name, dxfattribs={"color": color, "lineweight": lw})


def _add_closed_polyline(msp, ring: Ring, layer: str):
    r = _ensure_open(ring)
    if len(r) < 2:
        return
    pts = [(p[0], p[1]) for p in r]
    try:
        msp.add_lwpolyline(
            pts,
            format="xy",
            close=True,
            dxfattribs={"layer": layer}
        )
    except Exception:
        pass


def _add_text(msp, pos: Pt, text: str, height: float = 1.5,
              layer: str = "PLOT_LABELS", offset: tuple = (0, 0)):
    try:
        msp.add_text(
            text,
            dxfattribs={
                "insert": (pos[0] + offset[0], pos[1] + offset[1]),
                "height": height,
                "layer": layer,
                "halign": 1,  # centre
                "valign": 0,  # baseline
            }
        )
    except Exception:
        pass
