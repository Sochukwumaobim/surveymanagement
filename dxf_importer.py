# -*- coding: utf-8 -*-
"""
dxf_importer.py  —  Survey Management System v1.2
AutoCAD DXF/DWG import engine.
"""

import os
import re
import math
import subprocess
import tempfile
import sys
from datetime import datetime


def _get_lib_dir():
    """Return the plugin lib/ directory path."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")


def _ensure_ezdxf():
    """
    Import ezdxf and return the module.
    Adds lib/ to sys.path AFTER the plugins folder so the plugin package
    itself is never shadowed. Never adds lib/ at index 0.
    """
    lib = _get_lib_dir()

    if os.path.isdir(lib) and lib not in sys.path:
        # Find the plugins folder position and insert lib/ just after it
        # so plugin imports take priority over lib/ contents
        plugins_idx = None
        for i, p in enumerate(sys.path):
            if "plugins" in str(p).lower() and "surveymanagement" not in str(p).lower():
                plugins_idx = i
                break
        if plugins_idx is not None:
            sys.path.insert(plugins_idx + 1, lib)
        else:
            sys.path.append(lib)  # safest fallback: add at end

    # If already loaded in this session, return it directly
    if "ezdxf" in sys.modules and sys.modules["ezdxf"] is not None:
        return sys.modules["ezdxf"]

    # Fresh import
    try:
        import ezdxf
        return ezdxf
    except ImportError as e:
        ezdxf_dir = os.path.join(lib, "ezdxf")
        raise ImportError(
            f"Cannot import ezdxf: {e}\n\n"
            f"lib/ path: {lib}\n"
            f"ezdxf folder exists: {os.path.isdir(ezdxf_dir)}\n"
            f"lib/ contents: {os.listdir(lib) if os.path.isdir(lib) else 'MISSING'}\n\n"
            f"Fix: delete the lib/ folder and click Import from DXF/DWG again."
        )


# ── Nigerian state names for metadata matching ────────────────────────────────
NIGERIA_STATES = [
    "abia","adamawa","akwa ibom","anambra","bauchi","bayelsa","benue","borno",
    "cross river","delta","ebonyi","edo","ekiti","enugu","gombe","imo","jigawa",
    "kaduna","kano","katsina","kebbi","kogi","kwara","lagos","nasarawa","niger",
    "ogun","ondo","osun","oyo","plateau","rivers","sokoto","taraba","yobe",
    "zamfara","fct","abuja"
]

# Common plan-number patterns used in Nigerian survey firms
PLAN_NUMBER_PATTERNS = [
    r'\b[A-Z]{2,4}/[A-Z]{2,5}/\d{4}/\d{3,5}\b',   # IMO/OWM/2024/0147
    r'\bPLT[-/]\d{4}[-/]\d{3,5}\b',                 # PLT-2024-001
    r'\b[A-Z]{2,4}[/\-]\d{3,6}\b',                  # LG/2024/045
    r'\b\d{4}/[A-Z]{2,4}/\d{3,5}\b',                # 2024/IMO/0147
]

# Bearing patterns (DMS whole-circle and quadrant)
BEARING_PATTERNS = [
    # Whole circle: 45°30'15"  or  45d30m15s  or  045.3042°
    r'(\d{1,3})[°d](\d{1,2})[\'m](\d{1,2}(?:\.\d+)?)[\"s]',
    # Quadrant: N45°30'15"E  S30d15m20sW
    r'([NSEW])(\d{1,3})[°d](\d{1,2})[\'m]?(\d{1,2}(?:\.\d+)?)[\"s]?([NSEW])?',
    # Decimal degrees
    r'(\d{1,3}\.\d{3,6})[°]?',
]

# Distance patterns (metres, links, chains)
DISTANCE_PATTERNS = [
    r'(\d{1,4}\.\d{1,4})\s*m\b',    # 87.250m
    r'(\d{1,4}\.\d{1,4})\s*M\b',    # 87.250M
    r'D[=:\s]*(\d{1,4}\.\d{1,4})',  # D=87.250
    r'(\d{1,4}\.\d{3})\b',           # bare 3dp number  e.g. 87.250
]


# ═══════════════════════════════════════════════════════════════════════════════
class DXFImportResult:
    """Container for everything extracted from a DXF file."""

    def __init__(self):
        self.points     = []   # list of {"x": float, "y": float, "layer": str, "desc": str}
        self.polylines  = []   # list of [{"x":f,"y":f}, ...]  (one list per polyline)
        self.texts      = []   # list of {"text": str, "x": f, "y": f, "layer": str}
        self.bearings   = []   # list of {"dms": str, "decimal": float, "x": f, "y": f, "raw": str}
        self.distances  = []   # list of {"metres": float, "x": f, "y": f, "raw": str}
        self.legs       = []   # list of {"bearing_dms":str, "bearing_decimal":f, "distance":f}
        self.metadata   = {}   # {"plan_number":str, "owner":str, "surveyor":str, "date":str, ...}
        self.layers     = []   # all layer names found
        self.warnings   = []   # non-fatal issues
        self.errors     = []   # fatal issues
        self.ai_extraction = False  # True if AI extractor ran successfully
        self.start_point   = None  # {"x": f, "y": f, "label": str} — traverse start beacon
        self._beacon_map   = {}    # label → point dict, built during extraction

    def summary(self):
        # Count texts by source type
        dim_count    = sum(1 for t in self.texts if t.get("source","").startswith("DIMENSION"))
        attrib_count = sum(1 for t in self.texts if t.get("source","").startswith("ATTRIB"))
        leader_count = sum(1 for t in self.texts
                          if t.get("source","") in ("LEADER","MULTILEADER"))
        plain_count  = len(self.texts) - dim_count - attrib_count - leader_count

        lines = [
            f"Points found:          {len(self.points)}",
            f"Polyline vertices:     {sum(len(p) for p in self.polylines)}",
            f"Text entities:         {plain_count} TEXT/MTEXT"
            + (f"  +  {dim_count} DIMENSION" if dim_count else "")
            + (f"  +  {attrib_count} block attributes" if attrib_count else "")
            + (f"  +  {leader_count} leaders" if leader_count else ""),
            f"Bearings detected:     {len(self.bearings)}",
            f"Distances detected:    {len(self.distances)}",
            f"Matched legs:          {len(self.legs)}",
            f"Layers present:        {', '.join(self.layers[:8]) or 'none'}",
        ]
        if self.metadata:
            lines.append("Metadata extracted:")
            for k, v in self.metadata.items():
                lines.append(f"  {k}: {v}")
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
class DXFImporter:
    """
    Main import class.  Usage:
        imp = DXFImporter()
        result = imp.import_file("/path/to/plan.dxf")
        # result.points, result.legs, result.metadata ...
    """

    def __init__(self, preferred_layers=None, point_layer=None):
        """
        preferred_layers  – list of layer name substrings to prefer when multiple
                            layers contain points (e.g. ['BEACON','POINT','SURVEY'])
        point_layer       – if set, only import points from this exact layer
        """
        self.preferred_layers = preferred_layers or ["BEACON","SURVEY","POINT","BOUNDARY","TRIG"]
        self.point_layer = point_layer

    # ── Public entry point ────────────────────────────────────────────────────

    def import_file(self, filepath):
        """
        Import a DXF or DWG file.
        Returns a DXFImportResult.
        """
        result = DXFImportResult()

        # Load ezdxf at runtime — never at module load time.
        # _ensure_ezdxf() adds lib/ to sys.path and clears import cache
        # so packages installed during this QGIS session are found.
        try:
            ezdxf = _ensure_ezdxf()
        except ImportError as e:
            result.errors.append(str(e))
            return result

        ext = os.path.splitext(filepath)[1].lower()

        if ext == ".dwg":
            dxf_path = self._convert_dwg_to_dxf(filepath, result)
            if dxf_path is None:
                return result
        elif ext == ".dxf":
            dxf_path = filepath
        else:
            result.errors.append(f"Unsupported file type: {ext}. Please use .dxf or .dwg")
            return result

        try:
            # Use ezdxf.read() with an open file stream — always available
            # immediately after import, unlike readfile which is lazy-loaded
            # and causes AttributeError on Windows QGIS in some load sequences.
            with open(dxf_path, "r", encoding="utf-8", errors="replace") as fh:
                doc = ezdxf.read(fh)
        except Exception as e:
            result.errors.append(f"Cannot read DXF file: {str(e)}")
            return result

        msp = doc.modelspace()

        # Collect all layer names
        result.layers = [layer.dxf.name for layer in doc.layers]

        # Extract geometry and text
        self._extract_points(msp, result)
        self._extract_polylines(msp, result)
        self._extract_texts(msp, result)

        # ── Primary: build traverse from Boundary polyline ────────────────────
        # The Boundary LWPOLYLINE contains vertices in the EXACT traversal order.
        # V0 is the starting point (P1). Each segment gives a leg bearing+distance.
        # We then match the bearing/distance TEXT annotations to each segment
        # to get the annotated values (which may differ slightly from computed).
        boundary_built = self._build_traverse_from_boundary(msp, result)

        if not boundary_built:
            # ── Fallback: proximity-based leg matching ────────────────────────
            self._parse_bearings(result)
            self._parse_distances(result)
            self._match_legs(result)
            if result.legs and (result.points or result.polylines):
                self._sort_legs_by_points(result)

        # Extract grid control coordinate from MTEXT annotations
        self._extract_grid_origin(msp, result)

        # Extract metadata from text entities (regex-based, fast)
        self._extract_metadata(result)

        # ── AI-powered metadata extraction ───────────────────────────────────
        # Calls the hosted server — no API key needed, works for all users.
        # Falls back gracefully if server unreachable or no internet.
        if result.texts:
            try:
                from .ai_extractor import extract_with_ai, merge_ai_into_result
                # Attempt extraction directly — no pre-flight health check
                # (health checks get blocked by Vercel bot protection)
                all_points = list(result.points)
                for poly in result.polylines:
                    all_points.extend(poly)
                ai_data = extract_with_ai(result.texts, all_points)
                if ai_data:
                    result = merge_ai_into_result(ai_data, result)
                    result.ai_extraction = True
                    result.ai_server_reason = ""
                else:
                    result.ai_extraction = False
                    result.ai_server_reason = "Server unavailable or no internet"
            except Exception as e:
                print(f"[DXFImporter] AI extraction error: {e}")
                result.ai_extraction = False
                result.ai_server_reason = str(e)
        else:
            result.ai_extraction = False
            result.ai_server_reason = "No text found in DXF"

        # Clean temp file if we created one
        if ext == ".dwg" and dxf_path != filepath:
            try:
                os.remove(dxf_path)
            except Exception:
                pass

        return result

    # ── DWG → DXF conversion ─────────────────────────────────────────────────

    def _convert_dwg_to_dxf(self, dwg_path, result):
        """
        Try ODA File Converter (free tool from opendesign.com).
        Returns path to converted DXF, or None on failure.
        """
        # Common install locations on Windows
        oda_candidates = [
            r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA\ODAFileConverter\ODAFileConverter.exe",
            r"C:\ODAFileConverter\ODAFileConverter.exe",
        ]
        oda_exe = None
        for c in oda_candidates:
            if os.path.exists(c):
                oda_exe = c
                break

        if oda_exe is None:
            result.errors.append(
                "DWG file detected but ODA File Converter is not installed.\n\n"
                "Options:\n"
                "1. Install ODA File Converter (free) from opendesign.com\n"
                "2. Open the DWG in AutoCAD and use File → Save As → DXF\n"
                "3. Use the free online converter at cloudconvert.com\n\n"
                "Then re-import the resulting .dxf file."
            )
            return None

        try:
            out_dir = tempfile.mkdtemp()
            input_dir = os.path.dirname(dwg_path)
            filename   = os.path.basename(dwg_path)

            subprocess.run([
                oda_exe,
                input_dir, out_dir,
                "ACAD2010", "DXF", "0", "1",
                filename
            ], timeout=60, check=True, capture_output=True)

            dxf_name = os.path.splitext(filename)[0] + ".dxf"
            dxf_path = os.path.join(out_dir, dxf_name)

            if not os.path.exists(dxf_path):
                result.errors.append("ODA converter ran but no DXF file was produced.")
                return None

            return dxf_path

        except subprocess.TimeoutExpired:
            result.errors.append("DWG conversion timed out (>60 s). Try exporting DXF from AutoCAD manually.")
            return None
        except Exception as e:
            result.errors.append(f"DWG conversion failed: {str(e)}")
            return None

    # ── Geometry extraction ───────────────────────────────────────────────────

    def _build_traverse_from_boundary(self, msp, result):
        """
        Build the traverse directly from the Boundary LWPOLYLINE.

        The Boundary polyline on a Nigerian survey plan contains the beacon
        coordinates as vertices in correct traversal order. Vertex 0 is always
        the starting beacon (P1). The actual bearing/distance TEXT annotations
        are then matched to each polyline segment by proximity to the segment
        midpoint.

        This is the definitive method — the polyline defines the sequence.
        Returns True if successfully built, False to trigger fallback.
        """
        import math as _math

        # Find the boundary polyline — try 'Boundary' layer first, then
        # any closed polygon polyline with enough vertices near the beacons
        boundary_poly = None
        for entity in msp.query("LWPOLYLINE"):
            layer = getattr(entity.dxf, 'layer', '').lower()
            if 'boundary' in layer or 'verg' in layer:
                verts = list(entity.vertices())
                if len(verts) >= 3:
                    boundary_poly = verts
                    break

        if boundary_poly is None:
            # Try any polyline whose vertices match known beacon coordinates
            beacon_coords = {(round(p['x'],1), round(p['y'],1))
                             for p in result.points}
            for entity in msp.query("LWPOLYLINE"):
                verts = list(entity.vertices())
                if len(verts) < 3:
                    continue
                matches = sum(1 for v in verts
                              if (round(v[0],1), round(v[1],1)) in beacon_coords)
                if matches >= len(verts) - 1:  # allow one repeat (closed)
                    boundary_poly = verts
                    break

        if boundary_poly is None:
            return False

        # Remove duplicate closing vertex if present
        verts = boundary_poly
        if (len(verts) > 1 and
                abs(verts[0][0]-verts[-1][0]) < 0.01 and
                abs(verts[0][1]-verts[-1][1]) < 0.01):
            verts = verts[:-1]

        if len(verts) < 3:
            return False

        # Build segments from consecutive vertices
        segments = []
        n = len(verts)
        for i in range(n):
            j = (i + 1) % n
            e1, n1 = verts[i][0], verts[i][1]
            e2, n2 = verts[j][0], verts[j][1]
            computed_bearing = _math.degrees(
                _math.atan2(e2 - e1, n2 - n1)
            ) % 360
            computed_dist = _math.sqrt((e2-e1)**2 + (n2-n1)**2)
            mid_x = (e1 + e2) / 2
            mid_y = (n1 + n2) / 2
            segments.append({
                'e1': e1, 'n1': n1, 'e2': e2, 'n2': n2,
                'bearing': computed_bearing,
                'distance': computed_dist,
                'mid_x': mid_x, 'mid_y': mid_y
            })

        # For each segment, find the nearest bearing and distance annotations
        # Already parsed into result.bearings/distances if texts extracted
        # If not yet parsed, extract now
        if not result.bearings:
            self._parse_bearings(result)
        if not result.distances:
            self._parse_distances(result)

        def _bdiff(a, b):
            d = abs(a - b) % 360
            return d if d <= 180 else 360 - d

        legs = []
        for seg in segments:
            # Find bearing annotation nearest to this segment midpoint
            # whose value is close to the computed bearing (within 0.5°)
            best_bearing_dms  = self._decimal_to_dms(seg['bearing'])
            best_bearing_dec  = seg['bearing']
            best_dist         = seg['distance']

            # Match bearing text
            if result.bearings:
                nearest_b = min(result.bearings,
                    key=lambda b: _math.sqrt(
                        (b['x']-seg['mid_x'])**2 + (b['y']-seg['mid_y'])**2
                    ))
                sep = _math.sqrt((nearest_b['x']-seg['mid_x'])**2 +
                                 (nearest_b['y']-seg['mid_y'])**2)
                bdiff = _bdiff(nearest_b['decimal'], seg['bearing'])
                if sep < 200 and bdiff < 1.0:
                    best_bearing_dms = nearest_b['dms']
                    best_bearing_dec = nearest_b['decimal']

            # Match distance text
            if result.distances:
                nearest_d = min(result.distances,
                    key=lambda d: _math.sqrt(
                        (d['x']-seg['mid_x'])**2 + (d['y']-seg['mid_y'])**2
                    ))
                sep = _math.sqrt((nearest_d['x']-seg['mid_x'])**2 +
                                 (nearest_d['y']-seg['mid_y'])**2)
                ddiff = abs(nearest_d['metres'] - seg['distance'])
                if sep < 200 and ddiff < 1.0:
                    best_dist = nearest_d['metres']

            legs.append({
                'bearing_dms':     best_bearing_dms,
                'bearing_decimal': best_bearing_dec,
                'distance':        best_dist,
            })

        result.legs = legs

        # Set starting point from vertex 0
        result.start_point = {
            'x':     round(verts[0][0], 3),
            'y':     round(verts[0][1], 3),
            'label': ''  # will be filled in by _extract_points label match
        }

        # Match label from beacon_map if available
        beacon_map = getattr(result, '_beacon_map', {})
        for label, pt in beacon_map.items():
            if (abs(pt['x'] - result.start_point['x']) < 0.01 and
                    abs(pt['y'] - result.start_point['y']) < 0.01):
                result.start_point['label'] = label
                break

        return True

    def _extract_grid_origin(self, msp, result):
        """
        Extract the grid control coordinate from MTEXT annotations.
        Nigerian survey plans annotate one beacon's full coordinate
        with grid crosshair lines and MTEXT labels like '499120.400 mE'
        and '164650.289 mN'. This validates the data and confirms the CRS.
        """
        import re as _re

        grid_e = None
        grid_n = None

        for entity in msp.query("MTEXT"):
            try:
                txt = entity.plain_text()
            except AttributeError:
                txt = getattr(entity.dxf, 'text', '')

            # Strip RTF/MTEXT formatting codes
            clean = _re.sub(r'\{[^}]*\}', '', txt)
            clean = _re.sub(r'\\[a-zA-Z][^;]*;', '', clean).strip()

            # Match coordinate values with E/N suffix
            m_e = _re.search(r'(\d{5,7}\.\d{1,4})\s*m?E\b', clean, _re.IGNORECASE)
            m_n = _re.search(r'(\d{5,7}\.\d{1,4})\s*m?N\b', clean, _re.IGNORECASE)

            if m_e:
                grid_e = float(m_e.group(1))
            if m_n:
                grid_n = float(m_n.group(1))

        if grid_e is not None and grid_n is not None:
            result.metadata['grid_origin_e'] = grid_e
            result.metadata['grid_origin_n'] = grid_n

            # Find which beacon this corresponds to
            import math as _math
            for pt in result.points:
                if (abs(pt['x'] - grid_e) < 0.01 and
                        abs(pt['y'] - grid_n) < 0.01):
                    result.metadata['grid_control_beacon'] = pt.get('desc', '')
                    break

    def _extract_points(self, msp, result):
        """
        Extract POINT entities and match them to nearby beacon label texts.
        Stores the label (P1, P2, etc.) as the point description.
        Also stores the labeled beacon map in result for use by _sort_legs_by_points.
        """
        import math as _math

        # First collect all point coordinates
        raw_points = []
        for entity in msp.query("POINT"):
            loc = entity.dxf.location
            layer = getattr(entity.dxf, "layer", "")
            if self.point_layer and layer.upper() != self.point_layer.upper():
                continue
            raw_points.append({
                "x": round(loc.x, 3),
                "y": round(loc.y, 3),
                "layer": layer,
                "desc": ""
            })

        # Collect beacon label texts from any layer with pillar/beacon names
        # Match labels like P1, P2, P3... or B1, B2... or any short text near a point
        label_candidates = []
        for entity in msp.query("TEXT"):
            txt = entity.dxf.text.strip()
            # Short alphanumeric labels — likely beacon IDs
            import re as _re
            if _re.match(r'^[A-Za-z]?\d{1,3}$', txt) or \
               _re.match(r'^[A-Za-z]{1,3}\d{1,3}$', txt):
                label_candidates.append({
                    "text": txt,
                    "x": entity.dxf.insert.x,
                    "y": entity.dxf.insert.y
                })

        # Match each label to nearest point (within 5m)
        beacon_map = {}  # label → point dict
        for lbl in label_candidates:
            if not raw_points:
                break
            nearest = min(raw_points, key=lambda p:
                _math.sqrt((p["x"]-lbl["x"])**2 + (p["y"]-lbl["y"])**2))
            sep = _math.sqrt((nearest["x"]-lbl["x"])**2 + (nearest["y"]-lbl["y"])**2)
            if sep <= 5.0:
                # Only take the closest label per point
                existing = nearest.get("desc", "")
                if not existing:
                    nearest["desc"] = lbl["text"]
                    beacon_map[lbl["text"]] = nearest

        result.points = raw_points
        # Store beacon_map for use by _sort_legs_by_points
        result._beacon_map = beacon_map

    def _extract_polylines(self, msp, result):
        """Extract LWPOLYLINE and POLYLINE vertices."""
        for entity in msp.query("LWPOLYLINE"):
            layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""
            if self.point_layer and layer.upper() != self.point_layer.upper():
                continue
            verts = []
            for v in entity.vertices():
                verts.append({"x": round(v[0], 3), "y": round(v[1], 3), "layer": layer})
            if len(verts) >= 3:
                result.polylines.append(verts)

        for entity in msp.query("POLYLINE"):
            layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""
            verts = []
            try:
                for v in entity.vertices:
                    loc = v.dxf.location
                    verts.append({"x": round(loc.x, 3), "y": round(loc.y, 3), "layer": layer})
                if len(verts) >= 3:
                    result.polylines.append(verts)
            except Exception:
                pass

    def _extract_texts(self, msp, result):
        """
        Extract all text-bearing entities from the DXF modelspace.
        Handles: TEXT, MTEXT, DIMENSION, INSERT+ATTRIB, LEADER, MULTILEADER.
        Nigerian survey plans use all of these depending on firm and AutoCAD version.
        """

        # ── Standard text entities ────────────────────────────────────────────
        for entity in msp.query("TEXT"):
            try:
                result.texts.append({
                    "text":  entity.dxf.text,
                    "x":     round(entity.dxf.insert.x, 3),
                    "y":     round(entity.dxf.insert.y, 3),
                    "layer": getattr(entity.dxf, "layer", "")
                })
            except Exception:
                pass

        for entity in msp.query("MTEXT"):
            try:
                # plain_text() is the correct method in ezdxf 1.x
                # plain_mtext() was removed in ezdxf 1.0
                try:
                    txt = entity.plain_text()
                except AttributeError:
                    txt = getattr(entity.dxf, "text", "") or ""
                txt = txt.strip()
                if txt:
                    result.texts.append({
                        "text":  txt,
                        "x":     round(entity.dxf.insert.x, 3),
                        "y":     round(entity.dxf.insert.y, 3),
                        "layer": getattr(entity.dxf, "layer", "")
                    })
            except Exception:
                pass

        # ── DIMENSION entities ────────────────────────────────────────────────
        # AutoCAD dimensions store the measured value in dxf.text or dxf.measurement.
        # This is the most common way Nigerian surveyors add distance labels.
        for entity in msp.query("DIMENSION"):
            try:
                layer = getattr(entity.dxf, "layer", "")

                # Prefer the explicit text string (may include units like "87.250m")
                dim_text = ""
                if hasattr(entity.dxf, "text") and entity.dxf.text not in ("", "<>", None):
                    dim_text = str(entity.dxf.text).strip()
                elif hasattr(entity.dxf, "measurement") and entity.dxf.measurement:
                    dim_text = f"{entity.dxf.measurement:.3f}"

                if not dim_text:
                    continue

                # Get insertion point
                x, y = 0.0, 0.0
                for attr in ("text_midpoint", "defpoint", "defpoint2", "defpoint3"):
                    if hasattr(entity.dxf, attr):
                        pt = getattr(entity.dxf, attr)
                        if pt is not None:
                            try:
                                x, y = round(float(pt.x), 3), round(float(pt.y), 3)
                                break
                            except Exception:
                                pass

                result.texts.append({
                    "text":  dim_text,
                    "x":     x,
                    "y":     y,
                    "layer": layer,
                    "source": "DIMENSION"
                })
            except Exception:
                pass

        # ── INSERT block attributes ───────────────────────────────────────────
        # Survey firms use blocks for beacon symbols; the beacon number/ID
        # is stored as an ATTRIB (attribute) inside the block insertion.
        for entity in msp.query("INSERT"):
            try:
                ins_x = round(entity.dxf.insert.x, 3)
                ins_y = round(entity.dxf.insert.y, 3)
                layer = getattr(entity.dxf, "layer", "")

                # Block name may itself be meaningful (e.g. "PILLAR", "BEACON")
                blk_name = getattr(entity.dxf, "name", "")

                for attrib in entity.attribs:
                    try:
                        val = attrib.dxf.text.strip()
                        tag = getattr(attrib.dxf, "tag", "").strip()
                        if val:
                            result.texts.append({
                                "text":  val,
                                "x":     ins_x,
                                "y":     ins_y,
                                "layer": layer,
                                "source": f"ATTRIB:{tag or blk_name}"
                            })
                    except Exception:
                        pass
            except Exception:
                pass

        # ── LEADER and MULTILEADER text ───────────────────────────────────────
        for entity in msp.query("LEADER"):
            try:
                if hasattr(entity, "text") and entity.text:
                    verts = list(entity.vertices)
                    x = round(verts[-1][0], 3) if verts else 0.0
                    y = round(verts[-1][1], 3) if verts else 0.0
                    result.texts.append({
                        "text":  str(entity.text).strip(),
                        "x":     x,
                        "y":     y,
                        "layer": getattr(entity.dxf, "layer", ""),
                        "source": "LEADER"
                    })
            except Exception:
                pass

        for entity in msp.query("MULTILEADER"):
            try:
                ctx = entity.context
                if ctx and hasattr(ctx, "mtext") and ctx.mtext:
                    mtext_obj = ctx.mtext
                    raw = getattr(mtext_obj, "default_content", "") or ""
                    # Strip MTEXT formatting codes
                    import re as _re
                    clean = _re.sub(r'\{\\[^}]+\}|\\[A-Za-z][^;]*;|[{}]', '', raw).strip()
                    if clean:
                        loc = getattr(ctx.mtext, "insert", None)
                        x = round(float(loc.x), 3) if loc else 0.0
                        y = round(float(loc.y), 3) if loc else 0.0
                        result.texts.append({
                            "text":  clean,
                            "x":     x,
                            "y":     y,
                            "layer": getattr(entity.dxf, "layer", ""),
                            "source": "MULTILEADER"
                        })
            except Exception:
                pass

    # ── Annotation parsing ────────────────────────────────────────────────────

    def _parse_bearings(self, result):
        """Scan text entities for bearing values."""
        for t in result.texts:
            raw = t["text"].strip()
            decimal, dms = self._try_parse_bearing(raw)
            if decimal is not None:
                result.bearings.append({
                    "dms":     dms,
                    "decimal": decimal,
                    "x":       t["x"],
                    "y":       t["y"],
                    "raw":     raw
                })

    def _parse_distances(self, result):
        """Scan text entities for distance values."""
        for t in result.texts:
            raw = t["text"].strip()
            dist = self._try_parse_distance(raw)
            if dist is not None and 0.5 <= dist <= 5000:  # plausible survey distance
                result.distances.append({
                    "metres": dist,
                    "x":      t["x"],
                    "y":      t["y"],
                    "raw":    raw
                })

    def _match_legs(self, result):
        """
        Match bearing+distance pairs into traverse legs.
        Strategy: sort both lists by Y position (top to bottom on plan),
        then pair them in order if counts match.
        As a fallback, use proximity pairing (bearing label near distance label).
        """
        if not result.bearings or not result.distances:
            return

        bearings  = sorted(result.bearings,  key=lambda b: (-b["y"], b["x"]))
        distances = sorted(result.distances, key=lambda d: (-d["y"], d["x"]))

        # If counts match exactly, pair in order
        if len(bearings) == len(distances):
            for b, d in zip(bearings, distances):
                result.legs.append({
                    "bearing_dms":     b["dms"],
                    "bearing_decimal": b["decimal"],
                    "distance":        d["metres"]
                })
            return

        # Proximity pairing: each bearing finds its nearest distance label
        used_distances = set()
        for b in bearings:
            best_idx  = None
            best_dist = 1e9
            for i, d in enumerate(distances):
                if i in used_distances:
                    continue
                sep = math.sqrt((b["x"]-d["x"])**2 + (b["y"]-d["y"])**2)
                if sep < best_dist:
                    best_dist = sep
                    best_idx  = i
            if best_idx is not None and best_dist < 500:  # within 500 drawing units
                used_distances.add(best_idx)
                result.legs.append({
                    "bearing_dms":     b["dms"],
                    "bearing_decimal": b["decimal"],
                    "distance":        distances[best_idx]["metres"]
                })

        if not result.legs:
            result.warnings.append(
                "Could not automatically pair bearings with distances. "
                "You may need to select layers manually or enter legs by hand."
            )

    def _sort_legs_by_points(self, result):
        """
        Sort extracted traverse legs into correct sequence using beacon labels.

        Strategy:
        1. Use the beacon_map (label→coordinate) built during _extract_points
        2. Find the starting beacon (lowest-numbered label: P1, B1, etc.)
        3. Walk through labels in numeric order to build the sorted leg list
        4. For each step, match the leg whose bearing/distance matches 
           the actual point-to-point connection
        5. Store the starting coordinate in result.start_point

        This ensures traverse points land exactly on the beacon coordinates
        from the coordinate tab, not offset from a heuristic starting point.
        """
        import math as _math
        import re as _re

        beacon_map = getattr(result, '_beacon_map', {})

        # ── Strategy 1: use beacon labels to determine order ──────────────────
        if len(beacon_map) >= 2:
            # Sort labels: P1,P2,P3... or B1,B2... numerically
            def _label_key(lbl):
                nums = _re.findall(r'\d+', lbl)
                return int(nums[0]) if nums else 0

            sorted_labels = sorted(beacon_map.keys(), key=_label_key)
            ordered_points = [beacon_map[lbl] for lbl in sorted_labels]

            # Set start point from first label (P1)
            start = ordered_points[0]
            result.start_point = {
                "x":     start["x"],
                "y":     start["y"],
                "label": sorted_labels[0]
            }

            # Build expected leg bearings/distances between consecutive points
            expected = []
            n = len(ordered_points)
            for i in range(n):
                p1 = ordered_points[i]
                p2 = ordered_points[(i+1) % n]
                bearing = _math.degrees(
                    _math.atan2(p2["x"]-p1["x"], p2["y"]-p1["y"])
                ) % 360
                distance = _math.sqrt(
                    (p2["x"]-p1["x"])**2 + (p2["y"]-p1["y"])**2
                )
                expected.append({
                    "bearing": bearing,
                    "distance": distance,
                    "from": sorted_labels[i],
                    "to": sorted_labels[(i+1) % n]
                })

            # Match each expected leg to an extracted leg
            remaining = list(result.legs)
            sorted_legs = []

            for exp in expected:
                best = None
                best_score = 999

                for leg in remaining:
                    bd = abs(leg["bearing_decimal"] - exp["bearing"]) % 360
                    if bd > 180: bd = 360 - bd
                    dd = abs(leg["distance"] - exp["distance"])

                    if bd < 1.0 and dd < 1.0:
                        score = bd + dd * 0.1
                        if score < best_score:
                            best_score = score
                            best = leg

                if best:
                    best["from_beacon"] = exp["from"]
                    best["to_beacon"]   = exp["to"]
                    sorted_legs.append(best)
                    remaining.remove(best)

            if len(sorted_legs) == len(result.legs):
                result.legs = sorted_legs
                return  # Done — label-based sort succeeded

        # ── Strategy 2: fallback — sort by actual point positions ─────────────
        all_points = list(result.points)
        if not all_points or len(result.legs) < 2:
            return

        def _bearing(e1, n1, e2, n2):
            return _math.degrees(_math.atan2(e2-e1, n2-n1)) % 360

        def _dist(e1, n1, e2, n2):
            return _math.sqrt((e2-e1)**2 + (n2-n1)**2)

        def _bdiff(a, b):
            d = abs(a-b) % 360
            return d if d <= 180 else 360-d

        # Start from highest-northing point
        start_pt = max(all_points, key=lambda p: p['y'])
        result.start_point = {"x": start_pt["x"], "y": start_pt["y"],
                              "label": start_pt.get("desc", "")}
        remaining_pts  = [p for p in all_points if p is not start_pt]
        remaining_legs = list(result.legs)
        sorted_legs    = []
        current        = start_pt

        for step in range(len(result.legs)):
            best_leg = best_pt = None
            best_score = 999
            candidates = remaining_pts if remaining_pts else [start_pt]

            for leg in remaining_legs:
                for pt in candidates:
                    ab = _bearing(current['x'], current['y'], pt['x'], pt['y'])
                    ad = _dist(current['x'], current['y'], pt['x'], pt['y'])
                    bd = _bdiff(leg['bearing_decimal'], ab)
                    dd = abs(leg['distance'] - ad)
                    if bd < 1.0 and dd < 1.0:
                        score = bd + dd * 0.1
                        if score < best_score:
                            best_score, best_leg, best_pt = score, leg, pt

            if best_leg:
                sorted_legs.append(best_leg)
                remaining_legs.remove(best_leg)
                if best_pt in remaining_pts:
                    remaining_pts.remove(best_pt)
                current = best_pt
            else:
                result.warnings.append(
                    f"Could not sort traverse leg {step+1} — "
                    "legs loaded in extraction order."
                )
                return

        if len(sorted_legs) == len(result.legs):
            result.legs = sorted_legs



    def _extract_metadata(self, result):
        """
        Extract survey metadata from text entities.
        Uses layer names as hints (BEARING, DISTANCE layers skipped),
        and applies Nigerian-specific patterns to find plan number,
        owner, surveyor, date, LGA, state, and description.
        """
        # Build a combined text string from non-geometry layers only
        # (skip BEARING/DISTANCE layers — those contain measurements, not metadata)
        skip_layers = {"BEARING", "DISTANCE", "DIMENSION_LINE",
                       "GRID", "ROAD", "VERG", "VEGETATION", "RIVER"}
        meta_texts = [
            t for t in result.texts
            if t.get("layer", "").upper() not in skip_layers
            and t.get("source", "") not in ("DIMENSION",)
        ]
        all_text = "\n".join(t["text"] for t in meta_texts)

        # ── Plan number ───────────────────────────────────────────────────────
        # Extend standard patterns with MFP.J / firm-specific prefixes
        extended_patterns = PLAN_NUMBER_PATTERNS + [
            r'\b[A-Z]{2,5}\.?[A-Z]?\s*/\s*\d{3,5}\s*/\s*[A-Z]{1,4}\s*/\s*\d{4}\b',  # MFP.J/1442/IM/2018
            r'\bPLAN\s+NO\.?\s*:?\s*([A-Z0-9][A-Z0-9\s/\.\-]{6,40})',
        ]
        for pat in extended_patterns:
            m = re.search(pat, all_text, re.IGNORECASE)
            if m:
                val = (m.group(1) if m.lastindex else m.group(0)).strip()
                # Clean up whitespace within the plan number
                val = re.sub(r'\s+', '', val).upper()
                result.metadata["plan_number"] = val
                break

        # ── Survey date ───────────────────────────────────────────────────────
        date_patterns = [
            r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})',
            r'(\d{1,2})\s+(January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+(\d{4})',
            r'(January|February|March|April|May|June|July|August|'
            r'September|October|November|December)\s+(\d{4})',
        ]
        for pat in date_patterns:
            m = re.search(pat, all_text, re.IGNORECASE)
            if m:
                result.metadata["survey_date"] = m.group(0).strip()
                break

        # ── State ─────────────────────────────────────────────────────────────
        for state in NIGERIA_STATES:
            if re.search(r'\b' + re.escape(state) + r'\b', all_text, re.IGNORECASE):
                result.metadata["state"] = state.title()
                break

        # ── Surveyor ──────────────────────────────────────────────────────────
        surv_match = re.search(
            r'(?:Surv\.?\s+|SURV\.?\s+)([A-Z][A-Za-z\s\.]{3,50}?)(?:\n|,|\d|RLS|FNIS|$)',
            all_text, re.IGNORECASE
        )
        if surv_match:
            name = surv_match.group(1).strip().rstrip(".,")
            # Take only the first line if multi-line
            name = name.split("\n")[0].strip()
            if 4 <= len(name) <= 60:
                result.metadata["surveyor"] = name.title()

        # ── LGA ───────────────────────────────────────────────────────────────
        lga_match = re.search(
            r'([A-Z][a-zA-Z\s]{3,30})\s+(?:Local\s+Government\s+Area|LGA)',
            all_text, re.IGNORECASE
        )
        if lga_match:
            result.metadata["lga"] = lga_match.group(1).strip().title()

        # ── Owner name ────────────────────────────────────────────────────────
        # Nigerian plans: "PLAN SHEWING LANDED PROPERTY OF MR X" or
        # "BEING THE PROPERTY OF CHIEF X" or "MR X" near top of title block
        owner_patterns = [
            # "property of MR/MRS/CHIEF/DR..." on same or next line
            r'(?:property\s+of\s*\n?\s*)((?:Mr\.?|Mrs\.?|Miss|Dr\.?|Chief|Alhaji|Engr\.?|Prof\.?)\s+[A-Z][A-Za-z\s\.]{3,50})',
            # Inline: "property of MR FERDINAND UGOKWE"
            r'(?:property\s+of\s+)((?:Mr\.?|Mrs\.?|Miss|Dr\.?|Chief|Alhaji|Engr\.?)\s+[A-Z][A-Za-z\s\.]{3,50})',
            # "belonging to / prepared for"
            r'(?:belonging\s+to|prepared\s+for|client\s*:)\s*((?:Mr\.?|Mrs\.?|Chief|Dr\.?)\s+[A-Z][A-Za-z\s\.]{3,50})',
        ]
        for pat in owner_patterns:
            m = re.search(pat, all_text, re.IGNORECASE)
            if m:
                owner = m.group(1).strip()
                # Take first line only — avoid pulling in the address
                owner = owner.split("\n")[0].strip().rstrip(".,")
                if 5 <= len(owner) <= 60:
                    result.metadata["owner"] = owner.title()
                    break

        # ── Description / site location ───────────────────────────────────────
        # "AT NWOGWEIME FARM LAND AMAPUOKUKU" etc — appears after owner block
        loc_match = re.search(
            r'\bAT\b\s*\n?\s*([A-Z][A-Za-z\s,\.]{5,80})',
            all_text, re.IGNORECASE
        )
        if loc_match:
            loc = loc_match.group(1).strip().split("\n")[0].strip()
            if len(loc) >= 5:
                result.metadata["description"] = loc.title()

    # ── DMS / distance parsers ────────────────────────────────────────────────

    def _try_parse_bearing(self, text):
        """
        Parse text as a WCB bearing.
        Handles AutoCAD %%D code, degrees+minutes only (Nigerian plans),
        degrees+minutes+seconds, quadrant, and decimal formats.
        Returns (decimal_wcb, dms_string) or (None, None).
        """
        text = text.strip()
        # AutoCAD special codes
        text = text.replace("%%D", chr(176)).replace("%%d", chr(176))
        # Collapse multiple spaces
        text = re.sub(r" {2,}", " ", text)

        deg_sym = chr(176)  # °

        # Quadrant: N42°18'30"E  /  N42°18'E  /  N42°E
        pat = r"^([NS])\s*(\d{1,3})[" + deg_sym + r"d]\s*(\d{1,2})?[\' ]?\s*(\d{1,2}(?:\.\d+)?)?\s*[\"s]?\s*([EW])$"
        qm = re.match(pat, text, re.IGNORECASE)
        if qm:
            ns, deg, mn, sec, ew = qm.groups()
            dec = float(deg) + float(mn or 0)/60 + float(sec or 0)/3600
            if   ns.upper()=="N" and ew.upper()=="E": wcb = dec
            elif ns.upper()=="S" and ew.upper()=="E": wcb = 180 - dec
            elif ns.upper()=="S" and ew.upper()=="W": wcb = 180 + dec
            else:                                      wcb = 360 - dec
            return wcb % 360, self._decimal_to_dms(wcb % 360)

        # WCB deg+min+sec: 42°18'30"
        pat2 = r"^(\d{1,3})[" + deg_sym + r"d]\s*(\d{1,2})[\' ]\s*(\d{1,2}(?:\.\d+)?)[\"\s]?$"
        wm = re.match(pat2, text, re.IGNORECASE)
        if wm:
            deg, mn, sec = wm.groups()
            wcb = float(deg) + float(mn)/60 + float(sec)/3600
            if 0 <= wcb < 360:
                return wcb, self._decimal_to_dms(wcb)

        # WCB deg+min only: 167°52'  — most common in Nigerian survey DXFs
        pat3 = r"^(\d{1,3})[" + deg_sym + r"d]\s*(\d{1,2})[\' ]?$"
        wm2 = re.match(pat3, text, re.IGNORECASE)
        if wm2:
            deg, mn = wm2.groups()
            wcb = float(deg) + float(mn)/60
            if 0 <= wcb < 360:
                return wcb, self._decimal_to_dms(wcb)

        # WCB degrees only: 167°
        pat4 = r"^(\d{1,3})[" + deg_sym + r"d]\s*$"
        wm3 = re.match(pat4, text)
        if wm3:
            wcb = float(wm3.group(1))
            if 0 <= wcb < 360:
                return wcb, self._decimal_to_dms(wcb)

        # Decimal degrees: 167.8667
        dm = re.match(r"^(\d{1,3}\.\d{3,6})" + deg_sym + r"?$", text)
        if dm:
            wcb = float(dm.group(1))
            if 0 <= wcb < 360:
                return wcb, self._decimal_to_dms(wcb)

        return None, None

    def _try_parse_distance(self, text):
        """
        Parse text as a survey distance in metres.  Handles:
          • Explicit m suffix:   30.00m  7.5m  87.250m  87m
          • D= prefix:           D=87.250
          • Bare number:         30.00  87.250
        Returns float or None.
        """
        text = text.strip()

        # m/M suffix — most common format in Nigerian DXFs
        m = re.match(r"^([\d]+(?:\.\d+)?)\s*[mM]\b", text)
        if m:
            val = float(m.group(1))
            if 0.5 <= val <= 5000:
                return val

        # D= prefix
        m = re.match(r"^D[=:\s]+([\d]+(?:\.\d+)?)$", text, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if 0.5 <= val <= 5000:
                return val

        # Bare number with 2-3 decimal places
        m = re.match(r"^(\d{1,4}\.\d{2,3})$", text)
        if m:
            val = float(m.group(1))
            if 1 <= val <= 3000:
                return val

        return None

    @staticmethod
    def _decimal_to_dms(decimal):
        """Convert decimal degrees to DMS string, handling float rounding."""
        decimal = decimal % 360
        # Round to nearest 0.001 second to avoid floating point artefacts
        total_seconds = round(decimal * 3600, 3)
        d = int(total_seconds // 3600)
        remaining = total_seconds - d * 3600
        m = int(remaining // 60)
        s = remaining - m * 60
        # Handle carry from rounding
        if s >= 60:
            s -= 60
            m += 1
        if m >= 60:
            m -= 60
            d += 1
        d = d % 360
        return f"{d}°{m:02d}'{s:05.2f}\""
