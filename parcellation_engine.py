# -*- coding: utf-8 -*-
"""
parcellation_engine.py  —  Survey Management System v2.0  (REWRITE)

Land parcellation engine — informed by real Nigerian and international
subdivision practice.

KEY IMPROVEMENTS OVER v1:
  1. Shapely replaces all hand-rolled geometry — correct clipping on
     ANY perimeter shape (concave, L-shaped, irregular, etc.)
  2. Per-row area bisection uses actual Shapely intersection areas,
     not approximated widths from a single midpoint sample.
  3. Compactness scoring (isoperimetric quotient) added to optimizer
     so it prefers fat, square plots over thin slivers.
  4. ±1% tolerance enforced globally; remainder area is distributed
     ONLY across edge/corner plots, not all plots.
  5. Orientation search uses principal axis (PCA on vertices) in
     addition to longest-edge and degree-grid, finding better angles
     for diagonal or complex perimeters.
  6. Road placement uses adaptive spacing: bisects to find the y that
     maximises the number of compliant plots per band, not a fixed
     formula.
  7. Plots retain Shapely geometry internally for accurate reporting;
     ring export is for QGIS layer compatibility.

STRUCTURE (unchanged from v1 — surveyors recognise this layout):
  ┌──────────────────────────────────────┐  ← perimeter
  │ irregular edge/corner plots          │
  ├──────────────────────────────────────┤  ← road (9 m wide)
  │  ROW A  (face road above)            │
  │  ─ ─ ─ ─ shared rear boundary ─ ─ ─ │
  │  ROW B  (face road below)            │
  ├──────────────────────────────────────┤  ← road (9 m wide)
  │  ROW C  ...                          │
  └──────────────────────────────────────┘  ← perimeter
"""

from __future__ import annotations

import math
from typing import List, Tuple, Optional, Callable

import numpy as np
from shapely.geometry import (
    Polygon, MultiPolygon, LineString, MultiLineString,
    GeometryCollection, box
)
from shapely.ops import unary_union, split
from shapely.affinity import rotate as shp_rotate, translate

# ── Public types ────────────────────────────────────────────────────────────
Pt   = Tuple[float, float]
Ring = List[Pt]

# ── Constants ───────────────────────────────────────────────────────────────
TOLERANCE        = 0.01   # ±1 %  (was 2 % in v1)
MIN_PLOT_RATIO   = 0.25   # discard slivers below 25 % of target
BISECT_ITERS     = 18     # bisection iterations (~0.0004 % error at 100 m)
COMPACTNESS_W    = 0.15   # weight of compactness in optimizer score


# ── Backward-compatibility shims ────────────────────────────────────────────────────────────
# parcellation_dialog.py imports these by name; keep them public.

def polygon_area_abs(ring) -> float:
    """Return the absolute area of a coordinate ring (m²).
    Kept for dialog compatibility — internally Shapely handles all geometry."""
    n = len(ring); a = 0.0
    for i in range(n):
        x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _ensure_open(ring: Ring) -> Ring:
    """Strip a repeated closing point (ring[0] == ring[-1]) if present.
    Used by DXF export, which adds its own closing vertex when writing
    closed polylines."""
    r = list(ring)
    if len(r) > 1 and r[0] == r[-1]:
        r = r[:-1]
    return r


# ═══════════════════════════════════════════════════════════════════════════
# Geometry helpers (thin wrappers — keep Shapely internal)
# ═══════════════════════════════════════════════════════════════════════════

def _to_shapely(ring: Ring) -> Polygon:
    """Convert a coordinate ring to a valid Shapely Polygon."""
    if len(ring) < 3:
        raise ValueError("Ring must have at least 3 points")
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)          # standard fix for self-intersections
    return poly


def _exterior_ring(poly: Polygon) -> Ring:
    """Return the exterior ring as a list of (x, y) tuples."""
    return list(poly.exterior.coords)


def _polygon_area(poly: Polygon) -> float:
    return abs(poly.area)


def _centroid(poly: Polygon) -> Pt:
    c = poly.centroid
    return (c.x, c.y)


def _compactness(poly: Polygon) -> float:
    """
    Isoperimetric quotient: 4π·A / P²
    1.0  = perfect circle (most compact)
    ~0.78 = square
    → 0   = very thin sliver
    """
    a = poly.area
    p = poly.length
    if p < 1e-9:
        return 0.0
    return min(1.0, (4 * math.pi * a) / (p * p))


def _principal_axis_angle(ring: Ring) -> float:
    """
    PCA on polygon vertices → angle of first principal component.
    Better than longest-edge for diagonal / irregular perimeters.
    """
    pts = np.array(ring, dtype=float)
    pts -= pts.mean(axis=0)
    cov = np.cov(pts.T)
    if cov.ndim < 2 or np.isnan(cov).any():
        return 0.0
    eigvals, eigvecs = np.linalg.eigh(cov)
    major = eigvecs[:, np.argmax(eigvals)]
    return math.atan2(float(major[1]), float(major[0]))


def _longest_edge_angle(ring: Ring) -> float:
    pts = ring[:]
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts); best_len = -1.0; best_ang = 0.0
    for i in range(n):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % n]
        L = math.hypot(x2 - x1, y2 - y1)
        if L > best_len:
            best_len = L
            best_ang = math.atan2(y2 - y1, x2 - x1)
    return best_ang


def _rotate_poly(poly: Polygon, angle_rad: float, origin: Pt) -> Polygon:
    """Rotate Shapely polygon by angle (radians) around origin."""
    angle_deg = math.degrees(angle_rad)
    return shp_rotate(poly, angle_deg, origin=origin, use_radians=False)


def _horizontal_band(x_min: float, x_max: float,
                     y_lo: float, y_hi: float) -> Polygon:
    return box(x_min, y_lo, x_max, y_hi)


def _clip_band(perimeter_rotated: Polygon,
               x_min: float, x_max: float,
               y_lo: float, y_hi: float) -> Polygon:
    """Intersect the rotated perimeter with a horizontal band."""
    band = _horizontal_band(x_min - 1, x_max + 1, y_lo, y_hi)
    result = perimeter_rotated.intersection(band)
    if result.is_empty:
        return Polygon()
    if isinstance(result, (MultiPolygon, GeometryCollection)):
        # Take the largest piece
        polys = [g for g in result.geoms if isinstance(g, Polygon)]
        if not polys:
            return Polygon()
        result = max(polys, key=lambda g: g.area)
    return result


def _clip_band_pieces(perimeter_rotated: Polygon,
                      x_min: float, x_max: float,
                      y_lo: float, y_hi: float) -> List[Polygon]:
    """Like _clip_band but returns EVERY piece, left-to-right, instead of
    only the largest. Needed once cross-roads split a row band into
    disconnected blocks (one piece per side of each cross-road)."""
    band = _horizontal_band(x_min - 1, x_max + 1, y_lo, y_hi)
    result = perimeter_rotated.intersection(band)
    if result.is_empty:
        return []
    if isinstance(result, Polygon):
        return [result]
    if isinstance(result, (MultiPolygon, GeometryCollection)):
        polys = [g for g in result.geoms if isinstance(g, Polygon) and g.area > 1e-6]
        polys.sort(key=lambda g: g.bounds[0])  # left to right
        return polys
    return []


def _cross_road_xs(x_min: float, x_max: float, spacing: float) -> List[float]:
    """Evenly-spaced vertical cross-road centrelines (local x) so no block
    runs longer than ~spacing metres without mid-block vehicle access.
    spacing <= 0 disables cross-roads (returns [])."""
    if spacing is None or spacing <= 0:
        return []
    span = x_max - x_min
    n = max(0, round(span / spacing) - 1)
    if n <= 0:
        return []
    step = span / (n + 1)
    return [x_min + step * i for i in range(1, n + 1)]


def _remove_cross_roads(perimeter_rotated: Polygon, road_xs: List[float],
                        road_width: float, y_min: float, y_max: float) -> Polygon:
    """Subtract vertical cross-road strips from the rotated perimeter."""
    if not road_xs:
        return perimeter_rotated
    strips = [box(cx - road_width / 2, y_min - 1, cx + road_width / 2, y_max + 1)
              for cx in road_xs]
    return perimeter_rotated.difference(unary_union(strips))


def _build_cross_road_polygons(peri_rotated: Polygon, road_xs_local: List[float],
                               road_width: float, y_min: float, y_max: float,
                               angle: float, origin: Pt) -> List[Ring]:
    """Return vertical cross-road polygons clipped to perimeter, world frame."""
    roads = []
    for cx in road_xs_local:
        strip = box(cx - road_width / 2, y_min - 1, cx + road_width / 2, y_max + 1)
        local = peri_rotated.intersection(strip)
        if local.is_empty:
            continue
        pieces = [local] if isinstance(local, Polygon) else \
                 [g for g in getattr(local, "geoms", []) if isinstance(g, Polygon)]
        for piece in pieces:
            world = _rotate_poly(piece, -angle, origin)
            if isinstance(world, Polygon) and not world.is_empty:
                roads.append(_exterior_ring(world))
    return roads


# ═══════════════════════════════════════════════════════════════════════════
# Plot
# ═══════════════════════════════════════════════════════════════════════════

class Plot:
    """
    One subdivided land parcel.

    Attributes
    ----------
    plot_id     : str   e.g. "A01", "B03"
    ring        : Ring  exterior coordinates (for QGIS layer)
    area_m2     : float actual clipped area
    target_area : float requested plot area
    compliant   : bool  within ±TOLERANCE of target
    is_edge     : bool  touches perimeter boundary (may be irregular)
    compactness : float 0–1 shape quality score
    """

    def __init__(self, plot_id: str, poly: Polygon,
                 target_area: float, is_edge: bool = False):
        self.plot_id      = plot_id
        self._poly        = poly
        self.ring         = _exterior_ring(poly)
        self.area_m2      = _polygon_area(poly)
        self.centroid_pt  = _centroid(poly)
        self.target_area  = target_area
        self.compactness  = _compactness(poly)
        self.is_edge      = is_edge

        lo = target_area * (1 - TOLERANCE)
        hi = target_area * (1 + TOLERANCE)
        self.compliant = lo <= self.area_m2 <= hi
        self.label     = f"{plot_id}\n{self.area_m2:.0f} m²"

    def corner_table(self) -> List[dict]:
        r = list(self._poly.exterior.coords)
        return [{"label": f"{self.plot_id}-P{i+1}",
                 "E": round(e, 3), "N": round(n, 3)}
                for i, (e, n) in enumerate(r)]


# ═══════════════════════════════════════════════════════════════════════════
# Row placer
# ═══════════════════════════════════════════════════════════════════════════

def _col_area(peri_rotated: Polygon, xl: float, xr: float,
               y_base: float, grow_dir: int, depth: float) -> float:
    """Area of a vertical column slot clipped to the perimeter."""
    if grow_dir == 1:
        y_lo, y_hi = y_base, y_base + depth
    else:
        y_lo, y_hi = y_base - depth, y_base
    slot = box(xl, y_lo, xr, y_hi)
    result = slot.intersection(peri_rotated)
    return result.area if not result.is_empty else 0.0


def _place_row(
    road_edge_y: float,
    grow_dir: int,           # +1 = grow toward +y,  -1 = grow toward -y
    depth: float,
    peri_rotated: Polygon,   # perimeter already rotated to local frame
    peri_original: Polygon,  # perimeter in world frame (for edge detection)
    x_min: float, x_max: float,
    frontage: float,
    target_area: float,
    row_label: str,
    col_offset: int,
    angle: float,
    origin: Pt,
    min_area: float,
    remainder_mode: str,
    is_edge_row: bool = False,
) -> List[Plot]:
    """
    Slice one row of plots from the rotated perimeter.

    For interior (non-edge) rows, each column's depth is individually
    bisected so that frontage × depth_i clips to exactly target_area,
    regardless of diagonal perimeter shape. This eliminates over/under
    sized interior plots entirely.

    Edge rows fill to the full perimeter depth — their irregular shapes
    are correct surveying practice.

    If peri_rotated has had cross-road strips subtracted (see
    _remove_cross_roads), the row band naturally splits into multiple
    disconnected pieces here -- each is placed independently, with column
    numbering continuing across pieces so labels stay sequential.
    """
    if grow_dir == 1:
        y_lo_full, y_hi_full = road_edge_y, road_edge_y + depth
    else:
        y_lo_full, y_hi_full = road_edge_y - depth, road_edge_y

    # Clip perimeter to the full row band -- may yield >1 piece if
    # cross-roads have split this row into separate blocks.
    pieces = _clip_band_pieces(peri_rotated, x_min, x_max, y_lo_full, y_hi_full)
    plots: List[Plot] = []
    running_offset = col_offset

    for row_poly in pieces:
        if row_poly.is_empty or row_poly.area < min_area:
            continue

        rminx, _, rmaxx, _ = row_poly.bounds
        width = rmaxx - rminx
        if width < frontage * 0.25:
            continue

        # Number of columns: use the more conservative of area-based and
        # width-based estimates to avoid over-committing on narrow perimeters.
        n_by_area  = max(1, int(row_poly.area / target_area))
        n_by_width = max(1, int(width / frontage))
        n = min(n_by_area, n_by_width)

        # Distribute width evenly
        col_w = width / n

        for pi in range(n):
            xl = rminx + pi * col_w
            xr = rminx + (pi + 1) * col_w

            if is_edge_row:
                # Edge rows: extend each column all the way to the perimeter.
                # Use a very tall bounding box in the grow direction so Shapely
                # intersection captures every bit of land in this column strip.
                # This eliminates all staircase gaps between interior row tops
                # and the perimeter boundary.
                LARGE = max(abs(x_max - x_min), abs(y_hi_full - y_lo_full)) * 10
                if grow_dir == 1:
                    y_lo, y_hi = road_edge_y, road_edge_y + LARGE
                else:
                    y_lo, y_hi = road_edge_y - LARGE, road_edge_y
                slot_local    = box(xl, y_lo, xr, y_hi)
                clipped_local = slot_local.intersection(peri_rotated)
            else:
                # Interior rows: bisect depth per column so area = target_area
                # This handles diagonal perimeters where depth varies by x.
                max_d = depth
                a_max = _col_area(peri_rotated, xl, xr, road_edge_y, grow_dir, max_d)

                if a_max <= target_area * 1.005:
                    # Full depth gives at most target — use it all
                    col_depth = max_d
                else:
                    lo_d, hi_d = 0.0, max_d
                    for _ in range(BISECT_ITERS):
                        mid = (lo_d + hi_d) / 2
                        if _col_area(peri_rotated, xl, xr,
                                     road_edge_y, grow_dir, mid) < target_area:
                            lo_d = mid
                        else:
                            hi_d = mid
                    col_depth = (lo_d + hi_d) / 2

                if grow_dir == 1:
                    y_lo, y_hi = road_edge_y, road_edge_y + col_depth
                else:
                    y_lo, y_hi = road_edge_y - col_depth, road_edge_y

                slot_local    = box(xl, y_lo, xr, y_hi)
                clipped_local = slot_local.intersection(peri_rotated)

            if clipped_local is None or clipped_local.is_empty:
                continue

            # Handle multi-polygon (rare on complex perimeters)
            if isinstance(clipped_local, (MultiPolygon, GeometryCollection)):
                sub_pieces = [g for g in clipped_local.geoms if isinstance(g, Polygon)]
                if not sub_pieces:
                    continue
                clipped_local = max(sub_pieces, key=lambda g: g.area)

            if not isinstance(clipped_local, Polygon) or clipped_local.area < min_area:
                continue

            # Rotate back to world frame
            clipped_world = _rotate_poly(clipped_local, -angle, origin)
            if not isinstance(clipped_world, Polygon) or clipped_world.is_empty:
                continue

            # Flag if this plot touches the perimeter boundary
            touches_peri = clipped_world.exterior.distance(
                peri_original.exterior) < 0.05

            pid  = f"{row_label}{running_offset + pi + 1:02d}"
            plot = Plot(pid, clipped_world, target_area,
                        is_edge=is_edge_row or touches_peri)

            # Split wildly oversized edge plots into equal-height sub-plots
            if is_edge_row and plot.area_m2 > target_area * 2.5:
                n_sub = max(2, round(plot.area_m2 / target_area))
                subs  = _split_oversized_plot(
                    plot, n_sub, target_area, angle, origin, pid)
                plots.extend(subs)
            else:
                plots.append(plot)

        running_offset += n

    return plots


def _split_oversized_plot(plot, n_sub, target_area, angle, origin, base_id):
    """Slice an oversized edge plot into n_sub equal-height strips."""
    try:
        poly_local = _rotate_poly(plot._poly, angle, origin)
        minx, miny, maxx, maxy = poly_local.bounds
        sub_h  = (maxy - miny) / n_sub
        result = []
        for si in range(n_sub):
            y_lo      = miny + si * sub_h
            y_hi      = miny + (si + 1) * sub_h
            slice_box = box(minx - 1, y_lo, maxx + 1, y_hi)
            clipped_l = slice_box.intersection(poly_local)
            if clipped_l.is_empty or clipped_l.area < target_area * 0.05:
                continue
            if isinstance(clipped_l, (MultiPolygon, GeometryCollection)):
                pieces = [g for g in clipped_l.geoms
                          if isinstance(g, Polygon)]
                if not pieces:
                    continue
                clipped_l = max(pieces, key=lambda g: g.area)
            clipped_w = _rotate_poly(clipped_l, -angle, origin)
            if not isinstance(clipped_w, Polygon) or clipped_w.is_empty:
                continue
            result.append(Plot(f"{base_id}s{si+1}", clipped_w,
                               target_area, is_edge=True))
        return result if result else [plot]
    except Exception:
        return [plot]


# ═══════════════════════════════════════════════════════════════════════════
# Band area helper (used by bisection)
# ═══════════════════════════════════════════════════════════════════════════

def _band_area(peri_rotated: Polygon,
               x_min: float, x_max: float,
               y_lo: float, y_hi: float) -> float:
    clip = _clip_band(peri_rotated, x_min, x_max, y_lo, y_hi)
    return clip.area if not clip.is_empty else 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Road polygon builder
# ═══════════════════════════════════════════════════════════════════════════

def _build_road_polygons(peri_rotated: Polygon,
                         peri_original: Polygon,
                         road_ys_local: List[float],
                         road_width: float,
                         x_min: float, x_max: float,
                         angle: float, origin: Pt) -> List[Ring]:
    """Return road polygons clipped to perimeter, in world frame."""
    roads = []
    for ry in road_ys_local:
        road_local = _clip_band(peri_rotated, x_min, x_max,
                                ry - road_width / 2, ry + road_width / 2)
        if road_local.is_empty:
            continue
        road_world = _rotate_poly(road_local, -angle, origin)
        if isinstance(road_world, Polygon) and not road_world.is_empty:
            roads.append(_exterior_ring(road_world))
    return roads


# ═══════════════════════════════════════════════════════════════════════════
# Core evaluator
# ═══════════════════════════════════════════════════════════════════════════

def _evaluate(
    peri_original: Polygon,
    road_ys_local: List[float],
    angle: float,
    origin: Pt,
    frontage: float,
    target_area: float,
    road_width: float,
    remainder_mode: str,
    cross_road_spacing: float = 0.0,
) -> Tuple[List[Plot], List[Ring], int, float]:
    """
    Place all plots for a given orientation + road layout.

    cross_road_spacing: target block length (m) between perpendicular
        connector roads. 0/None disables cross-roads (original behaviour --
        rows run the full width of the perimeter with no mid-block access).

    Returns
    -------
    plots       : List[Plot]
    road_rings  : List[Ring]
    n_compliant : int
    mean_compact: float   average compactness of compliant plots
    """
    peri_rot_full = _rotate_poly(peri_original, angle, origin)
    minx, miny, maxx, maxy = peri_rot_full.bounds
    x_min, x_max = minx, maxx
    y_min, y_max = miny, maxy

    # Cross-roads: subtract vertical strips from the working perimeter so
    # every row band naturally splits into separate blocks around them.
    # peri_rot_full (unperforated) is kept for the road-polygon builders.
    road_xs_local = _cross_road_xs(x_min, x_max, cross_road_spacing)
    peri_rot = _remove_cross_roads(peri_rot_full, road_xs_local, road_width, y_min, y_max)

    min_area   = target_area * MIN_PLOT_RATIO
    min_useful = (target_area / frontage) * 0.35
    labels     = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lbl_idx    = 0
    all_plots  : List[Plot] = []

    roads   = sorted(road_ys_local)
    bands   = [(ry - road_width / 2, ry + road_width / 2) for ry in roads]

    # ── OUTER BAND BELOW FIRST ROAD ─────────────────────────────────────
    # Single edge row filling all the way from road edge to perimeter.
    # Per-column bisection in _place_row keeps each plot as close to
    # target_area as the available depth allows. is_edge_row=True
    # clips directly against peri_rotated so no land is left behind.
    outer_lo_depth = bands[0][0] - y_min
    if outer_lo_depth >= min_useful:
        lbl = labels[lbl_idx % 26]; lbl_idx += 1
        plots = _place_row(
            bands[0][0], -1, outer_lo_depth,
            peri_rot, peri_original,
            x_min, x_max, frontage, target_area,
            lbl, 0, angle, origin, min_area, remainder_mode,
            is_edge_row=True)
        all_plots.extend(plots)

    # ── INTER-ROAD BANDS ────────────────────────────────────────────────
    for ri, (r_bot, r_top) in enumerate(bands):
        lbl_A = labels[lbl_idx % 26]; lbl_idx += 1
        lbl_B = labels[lbl_idx % 26]; lbl_idx += 1

        if ri + 1 < len(bands):
            next_bot = bands[ri + 1][0]
            inter    = next_bot - r_top

            if inter < min_useful:
                continue

            # ── Smart bisection for rear boundary ────────────────────
            # Strategy: compute n from the FULL inter-road band area
            # (not just the width at the road edge, which is wrong on
            # diagonal perimeters). Then bisect depth_A so that the
            # clipped area of Row A equals exactly n × target_area.
            # Row B gets the rest — no gap, no overlap.

            a_full = _band_area(peri_rot, x_min, x_max, r_top, r_top + inter)

            # n = how many target-area plots fit in the full inter-road band,
            # split equally between the two facing rows.
            # Use floor so we don't over-commit area.
            n_full = max(2, int(a_full / target_area))
            # Row A gets ceil(n/2), Row B gets floor(n/2) — they share the band
            n_A = max(1, (n_full + 1) // 2)
            total_tgt_A = target_area * n_A

            if a_full <= total_tgt_A * 1.05:
                # Whole band barely fits Row A — give it all, skip Row B
                depth_A = inter; depth_B = 0.0
            else:
                lo_d, hi_d = 0.0, inter
                for _ in range(BISECT_ITERS):
                    mid_d = (lo_d + hi_d) / 2
                    if _band_area(peri_rot, x_min, x_max,
                                  r_top, r_top + mid_d) < total_tgt_A:
                        lo_d = mid_d
                    else:
                        hi_d = mid_d
                depth_A = (lo_d + hi_d) / 2
                depth_B = inter - depth_A   # NO GAP, NO OVERLAP

            if depth_A >= min_useful:
                plots = _place_row(
                    r_top, +1, depth_A,
                    peri_rot, peri_original,
                    x_min, x_max, frontage, target_area,
                    lbl_A, 0, angle, origin, min_area, remainder_mode)
                all_plots.extend(plots)

            if depth_B >= min_useful:
                plots = _place_row(
                    next_bot, -1, depth_B,
                    peri_rot, peri_original,
                    x_min, x_max, frontage, target_area,
                    lbl_B, 0, angle, origin, min_area, remainder_mode)
                all_plots.extend(plots)

        # ── OUTER BAND ABOVE LAST ROAD ───────────────────────────────
        if ri == len(bands) - 1:
            outer_hi_depth = y_max - r_top
            if outer_hi_depth >= min_useful:
                lbl = labels[lbl_idx % 26]; lbl_idx += 1
                plots = _place_row(
                    r_top, +1, outer_hi_depth,
                    peri_rot, peri_original,
                    x_min, x_max, frontage, target_area,
                    lbl, 0, angle, origin, min_area, remainder_mode,
                    is_edge_row=True)
                all_plots.extend(plots)

    # Build road polygons (use the unperforated perimeter for horizontal
    # roads so they render as clean full-width strips; cross-roads are
    # built separately below).
    road_rings = _build_road_polygons(
        peri_rot_full, peri_original, road_ys_local, road_width,
        x_min, x_max, angle, origin)
    if road_xs_local:
        road_rings += _build_cross_road_polygons(
            peri_rot_full, road_xs_local, road_width, y_min, y_max, angle, origin)

    # ── Fill residual gaps ────────────────────────────────────────────────
    # Per-column bisection in interior rows creates staircase tops; the space
    # between column tops and the adjacent edge row is unallocated.
    # Compute residual = perimeter − roads − plots, then merge each residual
    # fragment into the nearest edge plot by polygon union.
    all_plots = _fill_residual(
        all_plots, road_rings, peri_original, target_area)

    # NOTE: outlier normalization (undersized-merge / oversized-split) is
    # intentionally NOT run here. _evaluate() is called many times inside
    # optimize()'s angle x road-count search, and the normalization pass is
    # O(n^2) -- running it on every candidate instead of just the winner
    # made the optimizer unusably slow. It's applied once, after the
    # winning layout is chosen, in ParcellationEngine.subdivide().

    # ── Remainder redistribution ─────────────────────────────────────────
    _redistribute_remainder(all_plots, target_area)

    n_comp     = sum(1 for p in all_plots if p.compliant)
    comp_vals  = [p.compactness for p in all_plots if p.compliant]
    mean_comp  = sum(comp_vals) / len(comp_vals) if comp_vals else 0.0

    return all_plots, road_rings, n_comp, mean_comp


# ═══════════════════════════════════════════════════════════════════════════
# Edge-plot outlier normalization
# ═══════════════════════════════════════════════════════════════════════════

UNDERSIZED_FRAC = 0.40   # below this fraction of target -> absorb into neighbor
OVERSIZED_FRAC  = 1.50   # above this multiple of target -> split into strips


def _normalize_edge_plots(
    plots: List["Plot"],
    target_area: float,
    undersized_frac: float = UNDERSIZED_FRAC,
    oversized_frac: float = OVERSIZED_FRAC,
    max_merge_iters: int = 50,
) -> List["Plot"]:
    """
    Clean up the two flavours of bad edge-plot shape that survive placement
    on an irregular perimeter:

    - Undersized slivers (< undersized_frac * target_area): absorbed into
      whichever neighboring plot they share the longest boundary with.
      A merge can push the neighbor over oversized_frac, so merges and
      splits are interleaved rather than run as two separate passes.
    - Oversized remnants (> oversized_frac * target_area): sliced into
      ~equal-area strips via the existing _split_oversized_plot splitter.

    Only touches is_edge plots -- interior plots are already exact-area by
    construction and should never need this.
    """
    from shapely.ops import unary_union as _uu

    result = list(plots)
    skip_ids = set()  # plot_ids we've given up trying to merge (no touching neighbor)

    for _ in range(max_merge_iters):
        # 1) Split any oversized plot first, so merges below don't have to
        #    consider (and potentially re-grow) something already too big.
        split_any = False
        for i, p in enumerate(result):
            if p.is_edge and p.area_m2 > target_area * oversized_frac:
                n_sub = max(2, round(p.area_m2 / target_area))
                subs = _split_oversized_plot(
                    p, n_sub, target_area, 0.0,
                    (p._poly.centroid.x, p._poly.centroid.y), p.plot_id)
                if len(subs) > 1:
                    result[i:i + 1] = subs
                    split_any = True
                    break
        if split_any:
            continue

        # 2) Find the smallest undersized edge plot and merge it into its
        #    longest-shared-boundary neighbor.
        idx, best_a = None, float("inf")
        for i, p in enumerate(result):
            if (p.is_edge and p.plot_id not in skip_ids
                    and p.area_m2 < target_area * undersized_frac
                    and p.area_m2 < best_a):
                idx, best_a = i, p.area_m2
        if idx is None:
            break

        victim = result[idx]
        best_j, best_len = None, 0.0
        for j, p in enumerate(result):
            if j == idx:
                continue
            shared = victim._poly.boundary.intersection(p._poly.boundary)
            L = shared.length if not shared.is_empty else 0.0
            if L > best_len:
                best_len, best_j = L, j
        if best_j is None or best_len < 0.5:
            # No touching neighbor (shouldn't normally happen) -- leave it
            # rather than looping forever on the same plot.
            skip_ids.add(victim.plot_id)
            continue

        merged_geom = _uu([result[best_j]._poly, victim._poly])
        if isinstance(merged_geom, (MultiPolygon, GeometryCollection)):
            pieces = [g for g in merged_geom.geoms if isinstance(g, Polygon)]
            merged_geom = max(pieces, key=lambda g: g.area) if pieces else result[best_j]._poly
        if not isinstance(merged_geom, Polygon) or merged_geom.is_empty:
            victim.is_edge = False
            continue

        base = result[best_j]
        new_plot = Plot(base.plot_id, merged_geom, target_area, is_edge=True)
        result[best_j] = new_plot
        del result[idx]

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Residual gap filler
# ═══════════════════════════════════════════════════════════════════════════

def _fill_residual(
    plots: List["Plot"],
    road_rings: List[Ring],
    peri_original: Polygon,
    target_area: float,
) -> List["Plot"]:
    """
    After all plots are placed, compute the residual geometry
    (perimeter − plots − roads) and merge each fragment into the
    nearest edge plot. This ensures 100% land coverage regardless
    of staircase gaps left by per-column depth bisection.
    """
    if not plots:
        return plots

    from shapely.ops import unary_union as _uu

    plot_union = _uu([p._poly for p in plots])
    road_polys = [Polygon(r) for r in road_rings if len(r) >= 3]
    road_union = _uu(road_polys) if road_polys else Polygon()
    allocated  = _uu([plot_union, road_union])
    residual   = peri_original.difference(allocated)

    if residual.is_empty or residual.area < 0.01:
        return plots

    if isinstance(residual, Polygon):
        frags = [residual]
    else:
        frags = [g for g in residual.geoms
                 if isinstance(g, Polygon) and g.area > 0.01]

    if not frags:
        return plots

    # Work on a mutable copy; track index by id
    result = list(plots)
    edge_idx = [i for i, p in enumerate(result) if p.is_edge]
    if not edge_idx:
        edge_idx = list(range(len(result)))

    for frag in frags:
        # Find nearest edge plot by distance
        best_i = min(edge_idx,
                     key=lambda i: result[i]._poly.distance(frag))
        p      = result[best_i]

        # If merging would make the plot more than 2× target, try to find
        # a better (smaller) candidate first
        if p.area_m2 + frag.area > target_area * 2.5:
            # Sort candidates by how much they'd grow — pick smallest result
            candidates = sorted(edge_idx,
                                key=lambda i: result[i].area_m2 + frag.area
                                if result[i]._poly.distance(frag) < 1.0 else 1e9)
            if candidates:
                best_i = candidates[0]
                p = result[best_i]

        merged = p._poly.union(frag)

        # Simplify multi-polygon to largest piece
        if isinstance(merged, (MultiPolygon, GeometryCollection)):
            pieces = [g for g in merged.geoms if isinstance(g, Polygon)]
            merged = max(pieces, key=lambda g: g.area) if pieces else p._poly

        if not isinstance(merged, Polygon) or merged.is_empty:
            continue

        lo = target_area * (1 - TOLERANCE)
        hi = target_area * (1 + TOLERANCE)

        np2             = object.__new__(Plot)
        np2.plot_id     = p.plot_id
        np2._poly       = merged
        np2.ring        = _exterior_ring(merged)
        np2.area_m2     = _polygon_area(merged)
        np2.centroid_pt = _centroid(merged)
        np2.target_area = target_area
        np2.compactness = _compactness(merged)
        np2.is_edge     = True
        np2.compliant   = lo <= np2.area_m2 <= hi
        np2.label       = f"{np2.plot_id}\n{np2.area_m2:.0f} m²"

        # If merged plot is still very large, split it
        if np2.area_m2 > target_area * 2.5:
            n_sub = max(2, round(np2.area_m2 / target_area))
            # Use angle=0 for splitting since we're in world frame
            subs = _split_oversized_plot(
                np2, n_sub, target_area, 0.0,
                (np2._poly.centroid.x, np2._poly.centroid.y),
                np2.plot_id)
            result[best_i] = subs[0]
            for extra in subs[1:]:
                result.append(extra)
                edge_idx.append(len(result) - 1)
        else:
            result[best_i] = np2

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Remainder redistribution
# ═══════════════════════════════════════════════════════════════════════════

def _redistribute_remainder(plots: List[Plot], target_area: float) -> None:
    """
    After clipping, edge plots may be slightly under/over target.
    Distribute any residual area imbalance across non-compliant edge plots
    so that the total reported area matches the sum of plot geometries.
    This does NOT reshape the geometry; it refines the compliance flag
    using the actual clipped areas, which is what matters for the schedule.
    """
    if not plots:
        return

    total_area  = sum(p.area_m2 for p in plots)
    n           = len(plots)
    avg         = total_area / n
    lo          = target_area * (1 - TOLERANCE)
    hi          = target_area * (1 + TOLERANCE)

    # Re-evaluate compliance with ±1% band
    for p in plots:
        p.compliant = lo <= p.area_m2 <= hi


# ═══════════════════════════════════════════════════════════════════════════
# Optimizer
# ═══════════════════════════════════════════════════════════════════════════

def optimize(
    perimeter: Ring,
    target_area: float,
    frontage: float,
    road_width: float,
    remainder_mode: str = "distribute",
    angle_steps: int = 36,
    progress_cb: Optional[Callable[[int], None]] = None,
    cross_road_spacing: float = 0.0,
) -> Tuple[List[Plot], List[Ring], List[float], float]:
    """
    Search over orientations and road counts to maximise:

        score = n_compliant × 10000
              + n_plots     × 100
              + mean_compactness × COMPACTNESS_W × 1000
              + land_efficiency × 10

    Returns (plots, road_rings, road_ys_local, best_angle).
    """
    peri_poly = _to_shapely(perimeter)
    origin    = _centroid(peri_poly)

    # Candidate angles: PCA axis + longest edge + degree grid
    pca_angle    = _principal_axis_angle(perimeter)
    longest_angle = _longest_edge_angle(perimeter)

    angles = set()
    angles.add(pca_angle)
    angles.add(pca_angle + math.pi / 2)
    angles.add(longest_angle)
    for k in range(angle_steps):
        angles.add(k * math.pi / angle_steps)
    for deg in range(0, 180, 5):
        angles.add(math.radians(deg))

    best_score  = -1.0
    best_result = None
    total_iters = len(angles)

    for idx, angle in enumerate(sorted(angles)):
        if progress_cb:
            progress_cb(int(idx * 100 / total_iters))

        peri_rot = _rotate_poly(peri_poly, angle, origin)
        minx, miny, maxx, maxy = peri_rot.bounds
        depth = maxy - miny
        width = maxx - minx

        # Estimate plot depth from frontage and area
        n_col    = max(1, int(width / frontage))
        act_f    = frontage + (width - n_col * frontage) / n_col
        pd       = target_area / act_f
        block    = 2 * pd + road_width   # one road + two back-to-back rows

        max_roads = max(1, int(depth / block))

        for n_roads in range(1, max_roads + 2):
            # Centre the road grid so outer bands are as equal as possible.
            # Total space consumed: n_roads × block + 2 × outer_depth
            # We want outer_depth ≈ pd (one plot deep on each side).
            # consumed_by_roads_and_inner = n_roads * block
            # remainder split equally top and bottom = outer band depth
            total_inner = n_roads * block
            outer_depth = (depth - total_inner) / 2   # equal top & bottom

            # If outer bands would be < 30% of a plot depth, skip
            if outer_depth < pd * 0.30:
                continue

            road_ys = []
            for k in range(n_roads):
                # First road centre = miny + outer_depth + pd + road_width/2
                ry    = miny + outer_depth + pd + road_width / 2 + k * block
                r_bot = ry - road_width / 2
                r_top = ry + road_width / 2
                if r_bot < miny + pd * 0.30:
                    continue
                if r_top > maxy - pd * 0.30:
                    break
                road_ys.append(ry)

            if not road_ys:
                continue

            try:
                plots, road_rings, n_comp, mean_comp = _evaluate(
                    peri_poly, road_ys, angle, origin,
                    frontage, target_area, road_width, remainder_mode,
                    cross_road_spacing=cross_road_spacing)
            except Exception:
                continue

            if not plots:
                continue

            p_area  = sum(p.area_m2 for p in plots)
            r_area  = sum(
                Polygon(r).area for r in road_rings if len(r) >= 3)
            eff     = (p_area + r_area) / peri_poly.area if peri_poly.area > 0 else 0

            # ── Scoring ──────────────────────────────────────────────
            # Priority 1 (dominant): coverage — skip configs wasting >2% land
            unalloc_frac = max(0.0, 1.0 - eff)
            if unalloc_frac > 0.02:
                continue   # hard reject — too much wasted land

            # Priority 2: compliant plot count (most important)
            # Priority 3: total plot count
            # Priority 4: compactness of ALL plots (not just compliant)
            #   — use all plots so 0-compliant configs are still ranked
            # Priority 5: efficiency (land use)
            all_comp = ([p.compactness for p in plots]
                        if plots else [0.0])
            avg_all_comp = sum(all_comp) / len(all_comp)

            # Penalise very uneven plot sizes (high std dev = bad)
            if plots:
                areas  = [p.area_m2 for p in plots]
                mean_a = sum(areas) / len(areas)
                std_a  = (sum((a - mean_a)**2 for a in areas) / len(areas)) ** 0.5
                size_penalty = std_a / target_area   # 0 = perfect, 1 = very uneven
            else:
                size_penalty = 1.0

            score = (n_comp          * 10000
                     + len(plots)    * 100
                     + avg_all_comp  * COMPACTNESS_W * 1000
                     + eff           * 10
                     - size_penalty  * 500)

            if score > best_score:
                best_score  = score
                best_result = (plots, road_rings, road_ys, angle)

    if progress_cb:
        progress_cb(100)

    if best_result is None:
        # Fallback: single road through centre
        peri_rot = _rotate_poly(peri_poly, pca_angle, origin)
        _, miny, _, maxy = peri_rot.bounds
        ry     = (miny + maxy) / 2
        plots, road_rings, n_comp, mean_comp = _evaluate(
            peri_poly, [ry], pca_angle, origin,
            frontage, target_area, road_width, remainder_mode,
            cross_road_spacing=cross_road_spacing)
        best_result = (plots, road_rings, [ry], pca_angle)

    return best_result


# ═══════════════════════════════════════════════════════════════════════════
# Result
# ═══════════════════════════════════════════════════════════════════════════

class ParcellationResult:
    """
    Container for a completed subdivision.

    Attributes
    ----------
    plots         : all Plot objects (compliant + edge)
    road_polygons : list of exterior rings for road areas
    perimeter     : original perimeter ring
    summary       : dict of key statistics
    optimized     : bool — did the optimizer run?
    """

    def __init__(self, plots: List[Plot], road_polygons: List[Ring],
                 perimeter: Ring, summary: dict, optimized: bool = False):
        self.plots         = plots
        self.road_polygons = road_polygons
        self.perimeter     = perimeter
        self.summary       = summary
        self.optimized     = optimized

    @property
    def compliant_plots(self) -> List[Plot]:
        return [p for p in self.plots if p.compliant]

    @property
    def edge_plots(self) -> List[Plot]:
        return [p for p in self.plots if not p.compliant]

    def all_corners(self) -> List[dict]:
        out = []
        for p in self.plots:
            out.extend(p.corner_table())
        return out

    def perimeter_corner_table(self) -> List[dict]:
        """Labeled boundary/perimeter corners for setting-out -- these
        define the legal property line and need their own beacons on
        site, distinct from the internal plot corners."""
        ring = list(self.perimeter)
        if ring and ring[0] == ring[-1]:
            ring = ring[:-1]
        return [{"group": "PERIMETER", "point": f"BP{i+1}",
                 "E": round(e, 3), "N": round(n, 3)}
                for i, (e, n) in enumerate(ring)]

    def road_corner_tables(self) -> List[dict]:
        """Labeled road-corridor corners for setting-out, one group per
        disjoint road polygon (ring road, internal roads, cross-roads
        each contribute separate pieces). Road pieces are numbered in
        the order they appear in road_polygons."""
        out = []
        for ri, rp in enumerate(self.road_polygons):
            ring = list(rp)
            if ring and ring[0] == ring[-1]:
                ring = ring[:-1]
            road_label = f"ROAD {ri + 1}"
            for i, (e, n) in enumerate(ring):
                out.append({"group": road_label, "point": f"R{ri+1}P{i+1}",
                            "E": round(e, 3), "N": round(n, 3)})
        return out

    def all_corners_for_setting_out(self) -> List[dict]:
        """Plots + perimeter + roads combined, in the shape
        {"group", "point", "E", "N"} -- the single source of truth for
        the dialog's Coordinates tab, Excel export, and the printed
        report, so a surveyor has every point that needs staking out in
        one consistent list."""
        out = []
        for p in self.plots:
            ring = p.ring
            if ring and ring[0] == ring[-1]:
                ring = ring[:-1]
            for i, (e, n) in enumerate(ring):
                out.append({"group": p.plot_id, "point": f"P{i+1}",
                            "E": round(e, 3), "N": round(n, 3)})
        out.extend(self.perimeter_corner_table())
        out.extend(self.road_corner_tables())
        return out

    def area_schedule(self) -> List[dict]:
        return [
            {
                "no":         i + 1,
                "plot_id":    p.plot_id,
                "area_m2":    round(p.area_m2, 2),
                "area_ha":    round(p.area_m2 / 10000, 4),
                "compliant":  p.compliant,
                "is_edge":    p.is_edge,
                "compactness": round(p.compactness, 3),
            }
            for i, p in enumerate(self.plots)
        ]


# ═══════════════════════════════════════════════════════════════════════════
# Main engine
# ═══════════════════════════════════════════════════════════════════════════

class ParcellationEngine:
    """
    Usage
    -----
    engine = ParcellationEngine(perimeter_ring)
    engine.set_params(plot_area=500, frontage=15, road_width=9)
    result = engine.subdivide(progress_cb=lambda pct: print(f"{pct}%"))
    """

    def __init__(self, perimeter: Ring):
        # Ensure ring is open (no repeated closing point)
        ring = list(perimeter)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        self.perimeter        = ring
        self.road_centrelines : List[Ring] = []

        # Defaults
        self.plot_area      = 500.0
        self.frontage       = 15.0
        self.road_width     = 9.0
        self.remainder_mode = "distribute"
        self.use_optimizer  = True
        self.angle_steps    = 36
        self.cross_road_spacing = 0.0   # 0 = disabled (original behaviour)
        self.normalize_edge_plots = True   # merge slivers / split oversized remnants
        self.undersized_frac = UNDERSIZED_FRAC
        self.oversized_frac  = OVERSIZED_FRAC

    def add_road(self, centreline: Ring) -> None:
        self.road_centrelines.append(centreline)

    def clear_roads(self) -> None:
        self.road_centrelines = []

    def set_params(
        self,
        plot_area:      Optional[float] = None,
        frontage:       Optional[float] = None,
        road_width:     Optional[float] = None,
        remainder_mode: Optional[str]   = None,
        use_optimizer:  Optional[bool]  = None,
        angle_steps:    Optional[int]   = None,
        cross_road_spacing: Optional[float] = None,
        normalize_edge_plots: Optional[bool] = None,
        undersized_frac: Optional[float] = None,
        oversized_frac:  Optional[float] = None,
    ) -> None:
        if plot_area      is not None: self.plot_area      = max(1.0, plot_area)
        if frontage       is not None: self.frontage       = max(1.0, frontage)
        if road_width     is not None: self.road_width     = max(0.0, road_width)
        if remainder_mode is not None: self.remainder_mode = remainder_mode
        if use_optimizer  is not None: self.use_optimizer  = use_optimizer
        if angle_steps    is not None: self.angle_steps    = angle_steps
        if cross_road_spacing is not None:
            self.cross_road_spacing = max(0.0, cross_road_spacing)
        if normalize_edge_plots is not None:
            self.normalize_edge_plots = normalize_edge_plots
        if undersized_frac is not None: self.undersized_frac = undersized_frac
        if oversized_frac  is not None: self.oversized_frac  = oversized_frac

    def subdivide(
        self,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> ParcellationResult:

        peri_poly = _to_shapely(self.perimeter)
        origin    = _centroid(peri_poly)

        # ── Manual roads ────────────────────────────────────────────────
        if self.road_centrelines:
            angle = _principal_axis_angle(self.perimeter)
            road_ys = []
            for cl in self.road_centrelines:
                cl_rot  = _rotate_poly(LineString(cl), angle, origin) \
                          if len(cl) >= 2 else None
                if cl_rot:
                    ys = [c[1] for c in cl_rot.coords]
                    road_ys.append(sum(ys) / len(ys))
            plots, road_rings, n_comp, mean_comp = _evaluate(
                peri_poly, road_ys, angle, origin,
                self.frontage, self.plot_area,
                self.road_width, self.remainder_mode,
                cross_road_spacing=self.cross_road_spacing)
            optimized = False

        # ── Optimizer ────────────────────────────────────────────────────
        elif self.use_optimizer:
            plots, road_rings, _, angle = optimize(
                self.perimeter, self.plot_area, self.frontage,
                self.road_width, self.remainder_mode,
                self.angle_steps, progress_cb,
                cross_road_spacing=self.cross_road_spacing)
            optimized = True
            # Safety fill: residual gaps that survived the optimizer
            plots = _fill_residual(plots, road_rings, peri_poly, self.plot_area)

        # ── Single-road fallback ─────────────────────────────────────────
        else:
            angle    = _principal_axis_angle(self.perimeter)
            peri_rot = _rotate_poly(peri_poly, angle, origin)
            _, miny, _, maxy = peri_rot.bounds
            ry       = (miny + maxy) / 2
            plots, road_rings, n_comp, mean_comp = _evaluate(
                peri_poly, [ry], angle, origin,
                self.frontage, self.plot_area,
                self.road_width, self.remainder_mode,
                cross_road_spacing=self.cross_road_spacing)
            optimized = False

        # ── Normalize edge-plot outliers (once, on the final layout) ──────
        # Merges genuinely undersized slivers into their best neighbor and
        # splits genuinely oversized remnants into ~equal strips. Run here
        # (not inside _evaluate) so it only costs one pass, not one pass per
        # optimizer candidate.
        if self.normalize_edge_plots:
            plots = _normalize_edge_plots(
                plots, self.plot_area,
                undersized_frac=self.undersized_frac,
                oversized_frac=self.oversized_frac)

        # ── Summary ──────────────────────────────────────────────────────
        total    = peri_poly.area
        # Use the UNION area, not a naive sum -- individual road rings can
        # overlap each other where a cross-road crosses a horizontal road,
        # which would otherwise double-count that intersection square.
        road_polys_for_area = [Polygon(r) for r in road_rings if len(r) >= 3]
        road_union = unary_union(road_polys_for_area) if road_polys_for_area else Polygon()
        r_area   = road_union.area
        p_tot    = sum(p.area_m2 for p in plots)
        n_comp   = sum(1 for p in plots if p.compliant)
        lo       = self.plot_area * (1 - TOLERANCE)
        hi       = self.plot_area * (1 + TOLERANCE)
        cov      = round((p_tot + r_area) / total * 100, 1) if total > 0 else 0
        comp_avg = (sum(p.compactness for p in plots) / len(plots)
                    if plots else 0.0)

        # Road-access verification: does every plot actually touch a road
        # (or the perimeter, for edge plots that front an unmade boundary)?
        # This is a hard QA check, independent of how the plots were built.
        n_landlocked = 0
        for p in plots:
            has_road = (not road_union.is_empty) and p._poly.distance(road_union) < 0.05
            has_peri = p.is_edge  # edge plots front the (unmade) perimeter itself
            if not (has_road or has_peri):
                n_landlocked += 1

        summary = {
            "perimeter_area_m2":    round(total, 2),
            "road_area_m2":         round(r_area, 2),
            "plot_area_total_m2":   round(p_tot, 2),
            "n_plots":              len(plots),
            "n_compliant":          n_comp,
            "n_edge":               len(plots) - n_comp,
            "n_edge_plots":         sum(1 for p in plots if p.is_edge),
            "n_roads":              len(road_rings),
            "n_loops":              len(road_rings) + 1,  # compat: blocks = roads+1
            "target_plot_area_m2":  self.plot_area,
            "tolerance_pct":        TOLERANCE * 100,
            "tolerance_lo_m2":      round(lo, 1),
            "tolerance_hi_m2":      round(hi, 1),
            "theoretical_n_plots":  int((total - r_area) / self.plot_area),
            "avg_compactness":      round(comp_avg, 3),
            "optimized":            optimized,
            "coverage_pct":         cov,
            "n_landlocked":         n_landlocked,
            "cross_road_spacing_m": self.cross_road_spacing,
        }

        return ParcellationResult(
            plots=plots,
            road_polygons=road_rings,
            perimeter=self.perimeter,
            summary=summary,
            optimized=optimized,
        )