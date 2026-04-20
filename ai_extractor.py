# -*- coding: utf-8 -*-
"""
ai_extractor.py  —  Survey Management System v1.2
AI-powered extraction of survey metadata and traverse data from DXF text.

Calls a free serverless backend (hosted on Vercel) that handles the Gemini API.
Users need NO API key and NO account — the server key is managed by the developer.

The server URL below points to the deployed extraction service.
Update SERVER_URL after deploying your own instance from the survey_ai_server/ repo.
"""

import json
import urllib.request
import urllib.error
import ssl


# ── Server endpoint ───────────────────────────────────────────────────────────
# The hosted AI extraction server URL.
# This is managed by ASTROMAT GEO-SERVICES — users need no account or API key.
# Update this URL after deploying your Vercel server instance.
SERVER_URL = "https://survey-ai-server-i7cm.vercel.app/api/extract"

# Vercel protection bypass secret — must match the value set in
# Vercel Dashboard → Settings → Security → Protection Bypass for Automation
BYPASS_SECRET = "18xNwbBxTEHymo6CcqRWIxwpsvmiR2iS"

# Plugin version sent with each request (for server-side analytics/debugging)
PLUGIN_VERSION = "1.2.0"

# Timeout in seconds for the server request
REQUEST_TIMEOUT = 25

# Cache the last server health check result to avoid repeated checks
_server_ok = None          # None = not yet checked
_server_last_check = 0.0   # epoch time of last check


# ── Main extraction function ───────────────────────────────────────────────────

def extract_with_ai(texts, points, api_key=None):
    """
    Extract survey metadata and traverse legs from DXF text content.

    Sends text to the hosted AI extraction server.
    Users need no API key — the server handles authentication.

    Falls back silently to None if:
    - Server is unreachable (no internet, server down)
    - Request fails for any reason
    The plugin continues with coordinate-only import in that case.

    Args:
        texts:   list of {"text": str, "x": float, "y": float, "layer": str}
        points:  list of {"x": float, "y": float}
        api_key: ignored (kept for API compatibility)

    Returns:
        dict with metadata/traverse_legs/beacon_descriptions, or None on failure
    """
    if not texts:
        return None

    # Build text content — sort top to bottom, group by row
    sorted_texts = sorted(texts, key=lambda t: -t.get("y", 0))
    raw_lines = []
    current_y = None
    current_row = []

    for t in sorted_texts:
        val = t.get("text", "").strip()
        if not val:
            continue
        y = t.get("y", 0)
        if current_y is None or abs(y - current_y) > 50:
            if current_row:
                raw_lines.append("  |  ".join(current_row))
            current_row = [val]
            current_y = y
        else:
            current_row.append(val)
    if current_row:
        raw_lines.append("  |  ".join(current_row))

    text_content = "\n".join(raw_lines)[:6000]

    # Points summary
    pts = points[:20]
    points_summary = "\n".join(
        f"Point {i+1}: E={p['x']:.3f}  N={p['y']:.3f}"
        for i, p in enumerate(pts)
    )
    if len(points) > 20:
        points_summary += f"\n... and {len(points)-20} more"

    # Call the server
    return _call_server(text_content, points_summary)


def check_server_available():
    """
    Quick health-check GET request to the server.
    Returns (True, "") if available, (False, reason_string) if not.
    Caches the result for 5 minutes to avoid repeated checks.
    """
    global _server_ok, _server_last_check
    import time

    # Return cached result if checked within last 5 minutes
    if _server_ok is not None and (time.time() - _server_last_check) < 300:
        if _server_ok:
            return True, ""
        return False, "AI server unavailable (cached)"

    # Health check: try the root URL (same handler, GET method)
    health_url = SERVER_URL.replace("/api/extract", "")
    req = urllib.request.Request(health_url, method="GET")

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=6) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                _server_ok = True
                _server_last_check = time.time()
                return True, ""
            else:
                _server_ok = False
                _server_last_check = time.time()
                return False, f"Server returned: {body}"
    except urllib.error.URLError as e:
        _server_ok = False
        _server_last_check = time.time()
        reason = str(e.reason)
        if "Name or service not known" in reason or "nodename nor servname" in reason:
            return False, "No internet connection or server URL not configured."
        return False, f"Server unreachable: {reason}"
    except Exception as e:
        _server_ok = False
        _server_last_check = time.time()
        return False, str(e)


def _call_server(text_content, points_summary):
    """
    POST to the AI extraction server.
    Returns parsed JSON dict or None on any failure.
    Prints specific reason for failure to QGIS Python console.
    """
    payload = json.dumps({
        "text":    text_content,
        "points":  points_summary,
        "version": PLUGIN_VERSION
    }).encode("utf-8")

    req = urllib.request.Request(
        SERVER_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-vercel-protection-bypass": BYPASS_SECRET,
        },
        method="POST"
    )

    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=REQUEST_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))

            if not body.get("ok"):
                err_code = body.get("error", "")
                err_msg  = body.get("message", "Unknown server error")
                # Print specific error to QGIS Python console for debugging
                print(f"[AI Extractor] {err_code}: {err_msg}")
                return None

            return body.get("data")

    except urllib.error.URLError as e:
        # Network problem — update cache so we stop trying for 5 minutes
        global _server_ok, _server_last_check
        import time
        _server_ok = False
        _server_last_check = time.time()
        print(f"[AI Extractor] Server unreachable: {e.reason}")
        return None
    except Exception as e:
        print(f"[AI Extractor] Request failed: {e}")
        return None


# ── Merge AI result into DXFImportResult ─────────────────────────────────────

def merge_ai_into_result(ai_data, dxf_result):
    """
    Merge AI extraction output into the DXFImportResult.
    Overwrites regex-based metadata with AI values where available.
    Populates traverse legs that regex could not find.
    Returns the updated dxf_result.
    """
    if not ai_data:
        return dxf_result

    # ── Metadata ──────────────────────────────────────────────────────────────
    meta = ai_data.get("metadata", {})
    if meta:
        field_map = {
            "plan_number":   "plan_number",
            "owner_name":    "owner",
            "surveyor_name": "surveyor",
            "survey_date":   "survey_date",
            "lga":           "lga",
            "state":         "state",
            "description":   "description",
        }
        for ai_key, result_key in field_map.items():
            val = meta.get(ai_key)
            if val and str(val).strip() not in ("", "null", "None"):
                dxf_result.metadata[result_key] = str(val).strip()

    # ── Traverse legs ─────────────────────────────────────────────────────────
    ai_legs = ai_data.get("traverse_legs", [])
    if ai_legs:
        new_legs = []
        for leg in ai_legs:
            try:
                bearing_decimal = float(leg.get("bearing_decimal", 0))
                distance        = float(leg.get("distance_m", 0))
                bearing_dms     = leg.get("bearing_dms", "") or _decimal_to_dms(bearing_decimal)
                if 0 <= bearing_decimal < 360 and distance > 0:
                    new_legs.append({
                        "bearing_dms":     bearing_dms,
                        "bearing_decimal": bearing_decimal,
                        "distance":        distance,
                    })
            except (ValueError, TypeError, KeyError):
                continue

        if new_legs and len(new_legs) >= len(dxf_result.legs):
            dxf_result.legs = new_legs

    # ── Beacon descriptions ───────────────────────────────────────────────────
    beacon_descs = ai_data.get("beacon_descriptions", [])
    if beacon_descs and dxf_result.points:
        for bd in beacon_descs:
            pt_num = bd.get("point_number")
            beacon_id = bd.get("beacon_id", "") or ""
            desc = bd.get("description", "") or ""
            label = " — ".join(filter(None, [beacon_id, desc]))
            if pt_num and label:
                idx = int(pt_num) - 1
                if 0 <= idx < len(dxf_result.points):
                    dxf_result.points[idx]["desc"] = label

    # ── Confidence notes ──────────────────────────────────────────────────────
    confidence = ai_data.get("confidence", {})
    notes = confidence.get("notes", "")
    if notes and notes.lower() not in ("none", "null", ""):
        dxf_result.warnings.append(f"AI note: {notes}")

    return dxf_result


def _decimal_to_dms(decimal):
    """Convert decimal degrees to DMS string."""
    decimal = decimal % 360
    total_seconds = round(decimal * 3600, 3)
    d = int(total_seconds // 3600)
    remaining = total_seconds - d * 3600
    m = int(remaining // 60)
    s = remaining - m * 60
    if s >= 60:
        s -= 60; m += 1
    if m >= 60:
        m -= 60; d += 1
    return "%d°%02d'%05.2f\"" % (d % 360, m, s)
