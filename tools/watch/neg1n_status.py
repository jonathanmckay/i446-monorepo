#!/usr/bin/env python3
"""
neg1n_status — tiny status endpoint for the -1n Wear OS complication.

Reports which of the 5 -1neon block rituals (سمش/-1g/-1ibx/-1t/-1l) are
stamped done for the CURRENT 2-hour 地支 block, read straight from the same
source of truth the terminal/mobile dtd tools use: the block's header line
in build-order.md (e.g. "- 午 ☀️ 🎯 ⏱️ ✅ 📧 (15分, 136min) 😈 (...)"). Ritual
tag/emoji/desc definitions come from config/block-rituals.json so this never
drifts from the canonical list.

Run:   python3 neg1n_status.py            (binds 0.0.0.0:5562)
Fetch: http://ix:5562/api/neg1n

No auth — matches tools/dtd/dtd.py's own trust model (Tailscale/LAN-only,
single user).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

PORT = 5562
REPO = Path(__file__).resolve().parent.parent.parent
RITUALS_CFG = REPO / "config" / "block-rituals.json"
BUILD_ORDER = Path.home() / "vault" / "g245" / "5e-1" / "build-order.md"
DID_FAST = REPO / "tools" / "did" / "did-fast.py"

# Shared with day-points/hcb/hcmp below: refreshed every 30min by
# personal-dashboard/refresh-points-cache.sh straight from the live Neon
# workbook. A plain file read here (no xlwings/AppleScript in this
# process) — those complications don't need per-request freshness (nothing
# else in this whole sync chain promises sub-15min anyway: the phone syncs
# every 15min, complications refresh every 15min), and it keeps this
# process from ever blocking on Excel/AppleEvents, which would stall the
# -1n route too since they'd share this one Flask process/thread.
POINTS_CACHE = REPO / "tools" / "personal-dashboard" / ".points-cache.json"

# Block start hour (24h local) -> glyph. A block runs from its start hour up
# to (not including) the next block's start hour. Mirrors g245/CLAUDE.md's
# "-1₦ Block Rituals" table exactly (even-hour boundaries, not the
# traditional odd-centered Branch hours).
BLOCK_HOURS = [
    (4, "卯"), (6, "辰"), (8, "巳"), (10, "午"), (12, "未"),
    (14, "申"), (16, "酉"), (18, "戌"), (20, "亥"),
]

app = Flask(__name__)


def current_block(now: datetime | None = None) -> str | None:
    """The current 地支 glyph, or None during the 22:00-04:00 no-block window."""
    now = now or datetime.now()
    hour = now.hour
    glyph = None
    for start, g in BLOCK_HOURS:
        if hour >= start:
            glyph = g
        else:
            break
    if hour < BLOCK_HOURS[0][0]:
        return None  # before 04:00
    if hour >= 22:
        return None  # 亥 ends at 22:00, no block after that
    return glyph


def load_rituals() -> list[dict]:
    cfg = json.loads(RITUALS_CFG.read_text())
    return cfg["rituals"]


def block_header_line(text: str, glyph: str) -> str | None:
    """The single header line for `glyph`'s block (e.g. '- 午 ☀️ 🎯 ⏱️ ✅ 📧
    (15分, 136min) 😈 (...)'), or None if that block hasn't started today
    (no line yet) or the file is otherwise unreadable-for-this-glyph."""
    pat = re.compile(r"^- %s\b(.*)$" % re.escape(glyph), re.MULTILINE)
    m = pat.search(text)
    return m.group(0) if m else None


def compute_status(now: datetime | None = None) -> dict:
    glyph = current_block(now)
    rituals = load_rituals()
    if glyph is None:
        return {"block": None, "done": [], "not_done": [r["tag"] for r in rituals],
                "labels": {r["tag"]: r["desc"] for r in rituals},
                "emoji": {r["tag"]: r["emoji"] for r in rituals},
                "note": "outside the 04:00-22:00 block window"}
    try:
        text = BUILD_ORDER.read_text()
    except OSError as e:
        return {"error": f"can't read build-order.md: {e}"}
    line = block_header_line(text, glyph) or ""
    done, not_done = [], []
    for r in rituals:
        (done if r["emoji"] in line else not_done).append(r["tag"])
    return {
        "block": glyph,
        "done": done,
        "not_done": not_done,
        "labels": {r["tag"]: r["desc"] for r in rituals},
        "emoji": {r["tag"]: r["emoji"] for r in rituals},
    }


@app.route("/api/neg1n")
def api_neg1n():
    return jsonify(compute_status())


@app.route("/api/neg1n/complete", methods=["POST"])
def api_neg1n_complete():
    """Complete one of the current block's rituals from the watch's swipe
    list. Shells the exact same `did-fast.py --ritual <tag>` the desktop
    dtd/janus/inbound paths use — same Todoist close, same header stamp,
    same immediate 0分!P credit. Returns the freshly recomputed status so the
    caller (the phone, relaying for the watch) can push it straight back
    without a second round trip."""
    body = request.get_json(silent=True) or {}
    tag = body.get("tag", "")
    valid_tags = {r["tag"] for r in load_rituals()}
    if tag not in valid_tags:
        return jsonify({"error": f"unknown ritual tag {tag!r}", "known": sorted(valid_tags)}), 400
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(DID_FAST), "--ritual", tag],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        return jsonify({"error": f"did-fast.py failed to run: {e}"}), 500
    ritual_result = None
    brace = proc.stdout.find("{")
    if brace >= 0:
        try:
            ritual_result = json.loads(proc.stdout[brace:])
        except Exception:
            pass
    return jsonify({
        "ok": proc.returncode == 0,
        "tag": tag,
        "ritual_result": ritual_result,
        "stderr_tail": proc.stderr.strip()[-500:],
        "status": compute_status(),
    })


def _today_cache_entry() -> dict:
    """Today's row from .points-cache.json, or {} if the cache is missing/
    stale/unreadable — callers treat a missing key as 'no data yet', not an
    error, since the cache only refreshes every 30min and today's row won't
    exist until the first refresh after midnight."""
    try:
        cache = json.loads(POINTS_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return cache.get(datetime.now().date().isoformat(), {})


@app.route("/api/day-points")
def api_day_points():
    """Today's total 分 (0分!D, the grand-total column) for the quarter-circle
    arc complication. `max` is a fixed reference scale (not a real ceiling —
    days can and do exceed it), matching what the user asked the arc to be
    scaled against."""
    entry = _today_cache_entry()
    return jsonify({
        "date": datetime.now().date().isoformat(),
        "points": entry.get("__total__"),
        "max": 1440,
    })


@app.route("/api/hcb")
def api_hcb():
    """Today's calories eaten (hcbi!U) + today's hcbp+hcbc score
    (hcbi!Y+AA, today's row — a daily figure, matching the 131 daily goal;
    NOT the Q2+Q3 running total, which is a different, year-scale number)."""
    entry = _today_cache_entry()
    return jsonify({
        "date": datetime.now().date().isoformat(),
        "calories": entry.get("__hcb_kcal__"),
        "hcbp_hcbc": entry.get("__hcbp_hcbc__"),
        "goal": 131,
    })


@app.route("/api/hcmp")
def api_hcmp():
    """Today's prayer count (0n!AP, ص) and combined hcmp minutes (0n!AQ+AR+AS
    = o314 + 冥想 + 其他人)."""
    entry = _today_cache_entry()
    return jsonify({
        "date": datetime.now().date().isoformat(),
        "prayers": entry.get("__salat__"),
        "hcmp_minutes": entry.get("__hcmp_min__"),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
