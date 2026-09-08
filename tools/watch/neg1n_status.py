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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
