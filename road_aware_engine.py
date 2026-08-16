# -*- coding: utf-8 -*-
"""
road_aware_engine.py — Survey Management System

Alternate parcellation engine using exact-area Brent's-method bisection,
ported from the standalone road_aware_subdivision_v2.py prototype.

How this differs from parcellation_engine.py's default (column-bisection +
angle/road-count optimizer) engine:

  - Each row is sliced left-to-right with a vertical cut line solved by
    scipy.optimize.brentq so the AREA of every cut piece is exactly the
    target plot area, regardless of how the boundary bends through that
    slice (parcellation_engine.py bisects DEPTH per column instead; both
    are valid, this one tends to produce very clean, consistent columns
    on sites with a strong dominant edge).
  - Classifies every plot into one of four kinds instead of a binary
    compliant/edge split: "standard" (~target area), "merged" (an
    oversized-but-legitimate plot that absorbed a fringe sliver),
    "reduced" (a genuine, smaller, road-fronting corner/apex plot), and
    "fringe" (should not survive to the final result -- cross_row_merge
    eliminates these by construction).
  - Reclaims the ring-road strip along the first/last row when that row
    already has an internal road on its other side (see
    extend_end_rows_to_boundary in build_subdivision).
  - Supports evenly-spaced perpendicular cross-roads so long rows get
    mid-block vehicle access, same as the cross_road_spacing feature
    added to parcellation_engine.py.

This module owns the geometry algorithm; RoadAwareEngine at the bottom
adapts it to the same public shape as ParcellationEngine (perimeter in,
ParcellationResult out) so the dialog and DXF export work unchanged
regardless of which engine the user picks.
"""

from __future__ import annotations

import math
from typing import List, Optional, Callable

from shapely.geometry import Polygon, box, GeometryCollection, Point, LineString
from shapely.affinity import rotate as shp_rotate
from shapely.ops import unary_union, nearest_points
from scipy.optimize import brentq

from .parcellation_engine import (
    Plot, ParcellationResult, Ring, Pt, TOLERANCE, _to_shapely, _centroid,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

MIN_STD_FRAC     = 0.90   # area fraction (of target) to call a plot "standard"
REDUCED_FRAC     = 0.50   # below this and above MIN_MERGE_FRAC -> "reduced"
MAX_WIDTH_FACTOR = 6.0    # cap on how wide a single exact-area cut may be
MIN_MERGE_FRAC   = 0.35   # below this fraction, fold straight into previous cut


def _classify(frac: float) -> str:
    if frac >= MIN_STD_FRAC:
        return "standard"
    if frac >= REDUCED_FRAC:
        return "reduced"
    return "fringe"


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def dominant_angle(poly: Polygon) -> float:
    """Angle (degrees) of the polygon's longest minimum-rotated-rectangle edge."""
    mrr = poly.minimum_rotated_rectangle
    coords = list(mrr.exterior.coords)
    best_len, best_ang = -1.0, 0.0
    for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length > best_len:
            best_len, best_ang = length, math.degrees(math.atan2(dy, dx))
    return best_ang % 90


def as_polys(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out.extend(as_polys(g))
        return out
    return []


def dissolve_holes_to_simple(poly: Polygon, _depth: int = 0):
    """
    Any polygon with interior rings (holes) -- e.g. a ring-road annulus --
    cannot round-trip through the plugin's Ring type (a single list of
    (x,y) points, no holes) or through the dialog's QGIS layer / DXF
    export, both of which only write a single exterior ring per feature.
    Silently taking `.exterior.coords` on a hole-containing polygon drops
    the hole and turns a thin ring into what looks like a solid polygon
    covering everything the hole used to represent -- a real, easy-to-miss
    correctness bug (it happened here).

    This slits each hole open with a thin cut from the nearest point on
    the hole to the nearest point on the outer boundary, turning the
    annulus into an equivalent simple (hole-free) polygon or polygons.
    Safe to call on a polygon with no holes (returns it unchanged).
    """
    if poly.is_empty:
        return []
    if not poly.interiors:
        return [poly]
    if _depth > 8:  # pathological case guard -- shouldn't happen in practice
        return [Polygon(poly.exterior)]

    hole = poly.interiors[0]
    p_out, p_in = nearest_points(poly.exterior, hole)
    # The cut must actually CROSS both boundaries, not just touch them --
    # a flat-capped buffer ending exactly on the boundary points doesn't
    # reliably connect the exterior to the hole cavity (verified: it
    # removed area but left the hole intact). Extend a bit past each end
    # along the same line so it punches all the way through.
    dx, dy = p_out.x - p_in.x, p_out.y - p_in.y
    seg_len = math.hypot(dx, dy) or 1.0
    ext = 0.5  # metres past each boundary -- plenty relative to a 1cm cut width
    ux, uy = dx / seg_len, dy / seg_len
    a = (p_in.x - ux * ext, p_in.y - uy * ext)
    b = (p_out.x + ux * ext, p_out.y + uy * ext)
    cut = LineString([a, b]).buffer(0.01, cap_style=2)
    sliced = poly.difference(cut)

    out = []
    for piece in as_polys(sliced):
        if piece.interiors:
            out.extend(dissolve_holes_to_simple(piece, _depth + 1))
        else:
            out.append(piece)
    return out


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


# --------------------------------------------------------------------------- #
# Core: exact-area bisection of one row/chunk
# --------------------------------------------------------------------------- #

def bisect_chunk_by_area(chunk: Polygon, target_area: float, nominal_w: float,
                          max_width_factor: float = MAX_WIDTH_FACTOR):
    """
    Slice a roughly-horizontal polygon chunk left-to-right into plots of
    exactly `target_area`, using Brent's method to solve the cut-line
    x-position. Falls back to a fixed-width cut (accepting area deviation)
    only when the exact cut would need an unreasonably wide plot (i.e. local
    depth has pinched below the nominal depth near a sharp boundary corner).
    Returns (plots: list[Polygon], kinds: list[str]).
    """
    plots, kinds = [], []
    remaining = chunk
    guard = 0
    while remaining and not remaining.is_empty and remaining.area > 1.0 and guard < 500:
        guard += 1
        parts = sorted(as_polys(remaining), key=lambda p: p.area, reverse=True)
        remaining = parts[0]
        leftover_parts = parts[1:]

        minx, miny, maxx, maxy = remaining.bounds
        total_area = remaining.area

        if total_area < MIN_MERGE_FRAC * target_area:
            if plots:
                plots[-1] = unary_union([plots[-1], remaining])
                kinds[-1] = "merged"
            else:
                plots.append(remaining); kinds.append("fringe")
            remaining = unary_union(leftover_parts) if leftover_parts else None
            continue

        if total_area <= target_area * 1.02:
            frac = total_area / target_area
            plots.append(remaining)
            kinds.append(_classify(frac))
            remaining = unary_union(leftover_parts) if leftover_parts else None
            continue

        def cut_area(x):
            lb = box(minx - 1, miny - 1, x, maxy + 1)
            return remaining.intersection(lb).area

        try:
            x_cut = brentq(lambda x: cut_area(x) - target_area, minx, maxx, xtol=1e-4)
            width = x_cut - minx
            if width > max_width_factor * nominal_w:
                raise ValueError("cut too wide")
            piece = max(as_polys(remaining.intersection(
                box(minx - 1, miny - 1, x_cut, maxy + 1))), key=lambda p: p.area)
            rest = remaining.difference(box(minx - 1, miny - 1, x_cut, maxy + 1))
            plots.append(piece); kinds.append(_classify(piece.area / target_area))
        except Exception:
            x_cut = min(minx + nominal_w, maxx)
            piece_parts = as_polys(remaining.intersection(
                box(minx - 1, miny - 1, x_cut, maxy + 1)))
            if not piece_parts:
                remaining = unary_union(leftover_parts) if leftover_parts else None
                continue
            piece = max(piece_parts, key=lambda p: p.area)
            rest = remaining.difference(box(minx - 1, miny - 1, x_cut, maxy + 1))
            plots.append(piece)
            kinds.append(_classify(piece.area / target_area))

        all_leftover = as_polys(rest) + leftover_parts
        remaining = unary_union(all_leftover) if all_leftover else None

    return plots, kinds


# --------------------------------------------------------------------------- #
# Cross-row cleanup: absorb every remaining fringe piece into its best neighbor
# --------------------------------------------------------------------------- #

def cross_row_merge(plots, kinds, labels, target_area, tries=30):
    """Same as the standalone prototype, plus a `labels` list carried in
    parallel so plot IDs survive merges (the survivor keeps its own label)."""
    plots = list(plots)
    kinds = list(kinds)
    labels = list(labels)
    for _ in range(tries):
        idx, best_a = None, float("inf")
        for i, (p, k) in enumerate(zip(plots, kinds)):
            if k == "fringe" and p.area < best_a:
                idx, best_a = i, p.area
        if idx is None:
            break
        victim = plots[idx]
        best_j, best_len = None, 0.0
        for j, p in enumerate(plots):
            if j == idx:
                continue
            shared = victim.boundary.intersection(p.boundary)
            L = shared.length if not shared.is_empty else 0.0
            if L > best_len:
                best_len, best_j = L, j
        if best_j is None or best_len < 0.5:
            # no shared-boundary neighbor found (can happen with a corner
            # sliver) -- fall back to nearest plot by distance instead of
            # leaving it stranded as fringe.
            for j, p in enumerate(plots):
                if j == idx:
                    continue
                d = victim.distance(p)
                if d < 3.0:
                    best_j, best_len = j, 1.0
                    break
        if best_j is None:
            kinds[idx] = "fringe_isolated"  # genuinely no usable neighbor nearby
            continue
        merged = max(as_polys(unary_union([plots[best_j], victim])), key=lambda p: p.area)
        plots[best_j], kinds[best_j] = merged, "merged"
        # labels[best_j] unchanged -- survivor keeps its own plot_id
        del plots[idx]; del kinds[idx]; del labels[idx]
    return plots, ["fringe" if k == "fringe_isolated" else k for k in kinds], labels


# --------------------------------------------------------------------------- #
# Full pipeline
# --------------------------------------------------------------------------- #

def build_subdivision(perimeter: Polygon, plot_w: float, plot_d: float,
                       road_w: float, min_row_depth_frac: float = 0.15,
                       cross_road_spacing: float = 0.0,
                       extend_end_rows_to_boundary: bool = True):
    """
    min_row_depth_frac: a trailing sliver row (e.g. wedged against a
        tapering apex) is still tiled into plots as long as its depth is at
        least this fraction of plot_d -- below that, exact-area cuts would
        need absurdly wide plots, so it's folded into road verge instead.
    cross_road_spacing: target block length (m) between perpendicular
        connector roads. 0 disables cross-roads (rows run the full width
        of the site with no mid-block access).
    extend_end_rows_to_boundary: the first and last row already have an
        internal road on their inner edge (the road that separates them
        from the next row in). That means the ring-road strip on their
        OUTER edge is redundant for them specifically -- they don't lose
        access if it's removed. When True, those two rows are extended
        out to the true property line, reclaiming that strip as extra
        plot depth. Only applied where the row genuinely has an alternate
        road on its other side.

    Returns a dict: perimeter_area, plots (Shapely Polygons, world frame),
    kinds (parallel list), labels (parallel list, e.g. "A01"), roads
    (Shapely Polygons, world frame), target_area.
    """
    if not perimeter.is_valid:
        perimeter = perimeter.buffer(0)
    perimeter_area = perimeter.area

    # Perimeter ring road
    ring_w = road_w
    interior = perimeter.buffer(-ring_w)
    if interior.is_empty or interior.area < 100:
        ring_w = road_w / 2
        interior = perimeter.buffer(-ring_w)
    ring_road = perimeter.difference(interior)

    # Align to dominant edge for clean row-banding. IMPORTANT: use one fixed
    # pivot point for every rotation (forward and back) -- shp_rotate's
    # origin="centroid" shortcut uses each geometry's OWN centroid, so
    # rotating `interior` and `perimeter` that way uses two different
    # pivots, then rotating everything back around a third (perimeter.centroid)
    # introduces a real positional offset between plots and roads on any
    # site where those centroids don't coincide. One fixed point throughout
    # avoids that entirely.
    center = perimeter.centroid
    angle = dominant_angle(interior)
    rot = shp_rotate(interior, -angle, origin=center)
    rot_full = shp_rotate(perimeter, -angle, origin=center)  # true boundary
    minx, miny, maxx, maxy = rot.bounds
    full_maxy = rot_full.bounds[3]

    target_area = plot_w * plot_d

    # Band pattern: two back-to-back plot rows, then a road, repeating.
    road_bands, row_ranges = [], []
    y, pair_idx = miny, 0
    while y < maxy - 1:
        y0, y1 = y, min(y + plot_d, maxy)
        if (y1 - y0) < min_row_depth_frac * plot_d:
            break
        row_ranges.append((y0, y1))
        y += plot_d
        pair_idx += 1
        if pair_idx % 2 == 0 and y < maxy - 1:
            remaining_after_road = maxy - (y + road_w)
            if remaining_after_road < min_row_depth_frac * plot_d and remaining_after_road > -1:
                break
            road_bands.append((y, min(y + road_w, maxy)))
            y += road_w

    internal_roads = [box(minx - 1, ry0, maxx + 1, ry1).intersection(rot)
                       for ry0, ry1 in road_bands]

    # Extend the last (topmost) row to the true boundary where a road
    # already exists on its inner side.
    row_clip_source = [rot] * len(row_ranges)
    if extend_end_rows_to_boundary and row_ranges and len(road_bands) > 0:
        y0, y1 = row_ranges[-1]
        if any(abs(rb[1] - y0) < 0.5 for rb in road_bands):
            row_ranges[-1] = (y0, full_maxy)
            row_clip_source[-1] = rot_full

    # Perpendicular cross-roads for mid-block vehicle access.
    road_xs_local = _cross_road_xs(minx, maxx, cross_road_spacing)
    vertical_roads = []
    cross_cut_box = None
    if road_xs_local:
        strips = [box(cx - road_w / 2, miny - 1, cx + road_w / 2, maxy + 1)
                  for cx in road_xs_local]
        cross_cut_box = unary_union(strips)
        for s in strips:
            vertical_roads.append(s.intersection(rot))

    all_plots, all_kinds, all_labels = [], [], []
    tiled_union_parts = []
    reclaimed_chunks = []  # chunks from a boundary-extended row (see below)
    row_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for ri, ((y0, y1), clip_src) in enumerate(zip(row_ranges, row_clip_source)):
        band_poly = box(minx - 1, y0, maxx + 1, y1).intersection(clip_src)
        if cross_cut_box is not None:
            band_poly = band_poly.difference(cross_cut_box)
        row_lbl = row_letters[ri % 26] * (1 + ri // 26)
        col_no = 0
        # left-to-right so column numbers read naturally on the plan
        chunks = sorted(as_polys(band_poly), key=lambda c: c.bounds[0])
        for chunk in chunks:
            if chunk.area < 5:
                continue
            plots, kinds = bisect_chunk_by_area(chunk, target_area, plot_w)
            for p, k in zip(plots, kinds):
                col_no += 1
                all_plots.append(p)
                all_kinds.append(k)
                all_labels.append(f"{row_lbl}{col_no:02d}")
            tiled_union_parts.append(chunk)
            if clip_src is rot_full:
                reclaimed_chunks.append(chunk)

    final_plots = [shp_rotate(p, angle, origin=center) for p in all_plots]

    road_geoms = [g for g in (internal_roads + vertical_roads) if g and not g.is_empty]
    roads_footprint = unary_union(road_geoms) if road_geoms else GeometryCollection()

    tiled_footprint = unary_union(tiled_union_parts) if tiled_union_parts else GeometryCollection()
    verge = rot.difference(unary_union([tiled_footprint, roads_footprint]))

    # Only subtract the SPECIFIC boundary-extension strip from the ring
    # road, not the whole plot union. Everywhere else, plots and ring_road
    # only ever share a zero-width edge (they don't actually overlap), and
    # subtracting a polygon whose boundary is coincident with the ring's
    # own hole boundary along nearly its entire length is a known GEOS
    # overlay pathology -- it can erase the hole entirely instead of
    # leaving it alone, silently turning the thin ring into what looks
    # like a solid polygon covering the whole site. Restricting the
    # subtraction to just the small reclaimed area (which genuinely does
    # overlap the ring) avoids that failure mode.
    if reclaimed_chunks:
        reclaimed_world = unary_union(
            [shp_rotate(c, angle, origin=center) for c in reclaimed_chunks])
        ring_road_trimmed = ring_road.difference(reclaimed_world)
    else:
        ring_road_trimmed = ring_road
    final_roads = []
    for piece in as_polys(ring_road_trimmed):
        final_roads.extend(dissolve_holes_to_simple(piece))
    final_roads += [shp_rotate(r, angle, origin=center) for r in as_polys(roads_footprint)]
    final_roads += [shp_rotate(r, angle, origin=center) for r in as_polys(verge) if r.area > 1]

    final_plots, all_kinds, all_labels = cross_row_merge(
        final_plots, all_kinds, all_labels, target_area)

    return {
        "perimeter_area": perimeter_area,
        "plots": final_plots,
        "kinds": all_kinds,
        "labels": all_labels,
        "roads": final_roads,
        "target_area": target_area,
    }


# --------------------------------------------------------------------------- #
# Plugin adapter: same public shape as ParcellationEngine
# --------------------------------------------------------------------------- #

class RoadAwareEngine:
    """
    Drop-in alternative to ParcellationEngine using the exact-area Brent's
    method algorithm above. Same public surface (perimeter in, set_params,
    subdivide -> ParcellationResult) so the dialog and DXF export work
    unchanged regardless of which engine is selected.

    Manual road centrelines (add_road) are NOT supported by this engine --
    it computes its own road grid from plot_area/frontage/road_width. Any
    added centrelines are stored but ignored; the dialog should disable
    manual road drawing when this engine is selected.
    """

    def __init__(self, perimeter: Ring):
        ring = list(perimeter)
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        self.perimeter = ring
        self.road_centrelines: List[Ring] = []  # stored, not used

        # Defaults (mirrors ParcellationEngine where the concept overlaps)
        self.plot_area          = 500.0
        self.frontage           = 15.0
        self.road_width         = 9.0
        self.cross_road_spacing = 0.0
        self.min_row_depth_frac = 0.15
        self.extend_end_rows_to_boundary = True

    def add_road(self, centreline: Ring) -> None:
        self.road_centrelines.append(centreline)

    def clear_roads(self) -> None:
        self.road_centrelines = []

    def set_params(
        self,
        plot_area:      Optional[float] = None,
        frontage:       Optional[float] = None,
        road_width:     Optional[float] = None,
        remainder_mode: Optional[str]   = None,   # accepted, not used
        use_optimizer:  Optional[bool]  = None,   # accepted, not used
        angle_steps:    Optional[int]   = None,   # accepted, not used
        cross_road_spacing: Optional[float] = None,
        normalize_edge_plots: Optional[bool] = None,  # accepted, not used
        undersized_frac: Optional[float] = None,  # accepted, not used
        oversized_frac:  Optional[float] = None,  # accepted, not used
    ) -> None:
        if plot_area  is not None: self.plot_area  = max(1.0, plot_area)
        if frontage   is not None: self.frontage   = max(1.0, frontage)
        if road_width is not None: self.road_width = max(0.0, road_width)
        if cross_road_spacing is not None:
            self.cross_road_spacing = max(0.0, cross_road_spacing)

    def subdivide(
        self,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> ParcellationResult:
        if progress_cb:
            progress_cb(10)

        peri_poly   = _to_shapely(self.perimeter)
        plot_d      = self.plot_area / self.frontage

        res = build_subdivision(
            peri_poly,
            plot_w=self.frontage,
            plot_d=plot_d,
            road_w=self.road_width,
            min_row_depth_frac=self.min_row_depth_frac,
            cross_road_spacing=self.cross_road_spacing,
            extend_end_rows_to_boundary=self.extend_end_rows_to_boundary,
        )

        if progress_cb:
            progress_cb(80)

        # ── Convert to plugin Plot objects ─────────────────────────────
        plots: List[Plot] = []
        for poly, kind, label in zip(res["plots"], res["kinds"], res["labels"]):
            plot = Plot(label, poly, self.plot_area, is_edge=(kind != "standard"))
            plot.kind = kind  # extra attribute -- not part of the base Plot
            # "standard" plots are exact-area by construction; classify
            # compliance by kind rather than the generic ±1% window, since
            # a "reduced" corner plot at 55% of target is a legitimate
            # plot, not a near-miss.
            plot.compliant = (kind == "standard")
            plots.append(plot)

        road_rings: List[Ring] = []
        for r in res["roads"]:
            for poly in as_polys(r):
                for simple_poly in dissolve_holes_to_simple(poly):
                    if simple_poly.area > 0.5:
                        road_rings.append(list(simple_poly.exterior.coords))

        if progress_cb:
            progress_cb(95)

        # ── Road-access verification ────────────────────────────────────
        road_union = unary_union([Polygon(r) for r in road_rings if len(r) >= 3]) \
                     if road_rings else Polygon()
        n_landlocked = 0
        for p in plots:
            has_road = (not road_union.is_empty) and p._poly.distance(road_union) < 0.05
            if not has_road:
                n_landlocked += 1

        # ── Summary ──────────────────────────────────────────────────────
        total    = res["perimeter_area"]
        r_area   = road_union.area
        p_tot    = sum(p.area_m2 for p in plots)
        n_std    = sum(1 for p in plots if p.kind == "standard")
        n_merged = sum(1 for p in plots if p.kind == "merged")
        n_reduced = sum(1 for p in plots if p.kind == "reduced")
        n_fringe = sum(1 for p in plots if p.kind == "fringe")
        lo       = self.plot_area * (1 - TOLERANCE)
        hi       = self.plot_area * (1 + TOLERANCE)
        cov      = round((p_tot + r_area) / total * 100, 1) if total > 0 else 0
        comp_avg = (sum(p.compactness for p in plots) / len(plots)
                    if plots else 0.0)

        summary = {
            "perimeter_area_m2":    round(total, 2),
            "road_area_m2":         round(r_area, 2),
            "plot_area_total_m2":   round(p_tot, 2),
            "n_plots":              len(plots),
            "n_compliant":          n_std,
            "n_edge":               len(plots) - n_std,
            "n_edge_plots":         sum(1 for p in plots if p.is_edge),
            "n_roads":              len(road_rings),
            "n_loops":              len(road_rings) + 1,
            "target_plot_area_m2":  self.plot_area,
            "tolerance_pct":        TOLERANCE * 100,
            "tolerance_lo_m2":      round(lo, 1),
            "tolerance_hi_m2":      round(hi, 1),
            "theoretical_n_plots":  int((total - r_area) / self.plot_area) if total > 0 else 0,
            "avg_compactness":      round(comp_avg, 3),
            "optimized":            False,
            "coverage_pct":         cov,
            "n_landlocked":         n_landlocked,
            "cross_road_spacing_m": self.cross_road_spacing,
            "engine":               "road_aware",
            "n_standard":           n_std,
            "n_merged":             n_merged,
            "n_reduced":            n_reduced,
            "n_fringe":             n_fringe,
        }

        if progress_cb:
            progress_cb(100)

        return ParcellationResult(
            plots=plots,
            road_polygons=road_rings,
            perimeter=self.perimeter,
            summary=summary,
            optimized=False,
        )
