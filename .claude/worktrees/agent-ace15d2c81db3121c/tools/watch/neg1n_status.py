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


GOAL_PROJECT_ID = "6XfvCQ3p8Gq6fhGR"  # Todoist project "0g"
GOAL_LABEL = "#-1g"


def _goal_plan(text: str, block: str) -> dict:
    """Pure part of /api/neg1n/goal: what would be written for `text`."""
    import sys as _s
    _s.path.insert(0, str(REPO / "lib"))
    import neon_blocks as nb
    goals, todos = [], []
    for item in nb.parse_goals_text(text):
        if nb.is_plain_todo(item):
            content, dom = nb.split_goal_domain(item, block)
            todos.append({"content": content, "labels": [dom]})
        else:
            content, dom = nb.split_goal_domain(item, block)
            goals.append({"content": nb.ensure_goal_points(content), "labels": [GOAL_LABEL, dom]})
    return {"goals": goals, "todos": todos}


@app.route("/api/neg1n/goal", methods=["POST"])
def api_neg1n_goal():
    """Set the current block's -1g goal from the watch (RitualListActivity's
    tap on the -1g row -> Wear RemoteInput -> phone relay). Body: {"text":
    "...", "dry_run": bool}. Does what /-1g does, without the LLM: parse
    items ({N} = block goal, [N]-only = plain todo, @code / keyword / block
    default -> domain), append the goals under the current 地支 header in
    build-order.md (locked, retry-safe, stamps 🎯), create the Todoist
    tasks (dedup by content against open 0g tasks), then in a background
    thread refresh the dtd cache and close the 😈 -1g ritual card via the
    same did-fast path as a watch swipe. Responds as soon as the build
    order + Todoist are written (the phone's client allows 45s; the ritual
    close takes several more and its result is pushed by the next sync).
    The build-order write happens ON THIS HOST'S copy: this service runs on
    Ix, the single writer -- see the /-1g skill's single-writer note."""
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    dry_run = bool(body.get("dry_run"))
    if not text:
        return jsonify({"ok": False, "error": "empty goal text"}), 400
    block = current_block()
    if block is None:
        return jsonify({"ok": False, "error": "outside the 04:00-22:00 block window"}), 400
    plan = _goal_plan(text, block)
    if not plan["goals"] and not plan["todos"]:
        return jsonify({"ok": False, "error": "no goal parsed from text"}), 400
    if dry_run:
        return jsonify({"ok": True, "dry_run": True, "block": block, **plan})

    import sys as _s
    _s.path.insert(0, str(REPO / "lib"))
    import neon_blocks as nb
    import todoist as td

    out: dict = {"ok": True, "block": block, "written": [], "todoist": [], "warnings": []}
    goal_contents = [g["content"] for g in plan["goals"]]
    if goal_contents:
        try:
            out["written"] = nb.append_block_goals(block, goal_contents)
        except Exception as e:  # noqa: BLE001 -- keep going: Todoist + ritual still worth doing
            out["warnings"].append(f"build-order write failed: {e}")

    # Todoist: dedup against open 0g tasks by content (retry-safe).
    try:
        existing = {t.get("content", "").strip()
                    for t in (td._request("GET", f"/tasks?project_id={GOAL_PROJECT_ID}&limit=200") or {}).get("results", [])}
    except Exception as e:  # noqa: BLE001
        existing = set()
        out["warnings"].append(f"todoist dedup fetch failed: {e}")
    for g in plan["goals"]:
        if g["content"].strip() in existing:
            out["todoist"].append({"content": g["content"], "skipped": "exists"})
            continue
        try:
            t = td.create_task(g["content"], labels=g["labels"], due_string="today",
                               priority=4, project_id=GOAL_PROJECT_ID)  # API 4 == UI p1
            out["todoist"].append({"content": g["content"], "id": t.get("id")})
        except Exception as e:  # noqa: BLE001
            out["warnings"].append(f"todoist create failed for {g['content']!r}: {e}")
    for t_ in plan["todos"]:
        try:
            t = td.create_task(t_["content"], labels=t_["labels"], due_string="today", priority=1)
            out["todoist"].append({"content": t_["content"], "id": t.get("id"), "plain_todo": True})
        except Exception as e:  # noqa: BLE001
            out["warnings"].append(f"todoist todo failed for {t_['content']!r}: {e}")

    def _finish(block_at_request: str) -> None:
        # Cache refresh so the new #-1g goal reaches dtd, then close the -1g
        # ritual card + stamp 🎯 through the exact same path a watch swipe
        # uses. Skipped if the block rolled over meanwhile (--ritual always
        # acts on the CURRENT block). Runs AFTER the response and outside
        # any build-order lock: did-fast flocks the same .lock itself.
        try:
            subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"],
                           capture_output=True, text=True, timeout=60)
        except Exception:
            pass
        if goal_contents and current_block() == block_at_request:
            try:
                subprocess.run(["/usr/bin/python3", str(DID_FAST), "--ritual", "-1g"],
                               capture_output=True, text=True, timeout=60)
            except Exception:
                pass

    if goal_contents:
        import threading
        threading.Thread(target=_finish, args=(block,), daemon=True).start()
    out["status"] = compute_status()
    return jsonify(out)


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
