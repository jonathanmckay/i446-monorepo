#!/usr/bin/env python3
"""
janus mobile — iPhone-first mirror of the `janus` timeline TUI.

Same data source (Toggl), same domain colors, same 地支 block structure — but
as a swipeable web list of today's timeline. Two swipe actions:

  · swipe an UNTRACKED GAP right  → dialog with start/end prefilled; saving
    creates the Toggl entry (optional @code picks the project, like /tg).
  · swipe a TIME ENTRY right      → logs its minutes through the real /did
    (did-fast.py "<desc> <minutes> [@code]"), which routes exactly like the
    desktop: 0n habit → minutes to its 0n column; variable/1n+ → base+rate;
    Todoist word-overlap match → its [N]; otherwise the variable path writes
    minutes-as-points to the inferred domain column + a posthoc task. A
    per-day ledger prevents double-logging the same entry.

Run:   python3 mobile.py           (binds 0.0.0.0:5561)
Open:  http://ix:5561              (from the phone, same as dtd/dashboard)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

# Toggl API key: env first, else the MCP config (same fallback toggl_cli uses —
# a launchd agent has a bare environment).
def _load_api_key() -> str:
    try:
        d = json.loads((Path.home() / ".claude.json").read_text())
        return (d.get("mcpServers", {}).get("toggl_server", {})
                 .get("env", {}).get("TOGGL_API_KEY", ""))
    except Exception:
        return ""

if not os.environ.get("TOGGL_API_KEY"):
    os.environ["TOGGL_API_KEY"] = _load_api_key()
os.environ.setdefault("TOGGL_WORKSPACE_ID", "2092616")

sys.path.insert(0, str(Path.home() / "i446-monorepo"))
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))

from flask import Flask, jsonify, render_template_string, request  # noqa: E402

from mcp.toggl_server import toggl_api  # noqa: E402
from mcp.toggl_server.config import PROJECT_MAP, PROJECT_NAMES  # noqa: E402

PORT = 5561
TZ = ZoneInfo("America/Los_Angeles")
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
STATE_DIR = Path.home() / ".local/state/jm"
MIN_GAP_MIN = 5          # gaps shorter than this are not shown
DAY_START_HOUR = 0       # timeline from midnight (睡觉 entries live there)

# Neon domain palette — mirrors tools/dtd/dtd.py / tools/did/dtd.sh COLORS.
COLORS = {
    "g245": "#00e676", "epcn": "#00bfa5", "s897": "#1b5e20", "hcmc2": "#ffd600",
    "xk87": "#fd6c1d", "xk88": "#e65100", "hci": "#63ede0", "i9": "#2979ff",
    "n156": "#1249b4", "hcmc": "#0d3b66", "m5x2": "#d50032", "m828": "#9b0023",
    "hcb": "#f81d78",
    "hcbp": "#ff4081", "infra": "#9e9e9e", "i444": "#616161", "i447": "#a89c8a",
    "hcm": "#aa00ff", "hcmp": "#7c4dff", "hcmr": "#bda6ff", "家": "#ff4136",
    "睡觉": "#666666",
}
DEFAULT_COLOR = "#bdbdbd"

BLOCKS = [(4, "卯"), (6, "辰"), (8, "巳"), (10, "午"), (12, "未"),
          (14, "申"), (16, "酉"), (18, "戌"), (20, "亥"), (22, "子")]

_AT = re.compile(r"\s*@(\S+)")


def _ledger_path(day: _dt.date) -> Path:
    return STATE_DIR / f"janus-mobile-logged-{day.isoformat()}.json"


def _ledger(day: _dt.date) -> dict:
    try:
        return json.loads(_ledger_path(day).read_text())
    except Exception:
        return {}


def _ledger_add(day: _dt.date, entry_id: str, note: str) -> None:
    d = _ledger(day)
    d[str(entry_id)] = note
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _ledger_path(day).with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False))
    tmp.replace(_ledger_path(day))


def _parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)


def _fetch_today() -> list[dict]:
    today = _dt.datetime.now(TZ).date()
    raw = toggl_api.get_entries(
        start_date=(today - _dt.timedelta(days=1)).isoformat(),
        end_date=(today + _dt.timedelta(days=1)).isoformat()) or []
    now = _dt.datetime.now(TZ)
    out = []
    for e in raw:
        try:
            st = _parse_iso(e["start"])
        except Exception:
            continue
        running = (e.get("duration") or 0) < 0 or not e.get("stop")
        en = now if running else _parse_iso(e["stop"])
        # keep the part that falls inside today
        if en.date() < today or st.date() > today:
            continue
        day_start = _dt.datetime.combine(today, _dt.time(0, 0), TZ)
        st_c, en_c = max(st, day_start), min(en, now)
        if en_c <= st_c:
            continue
        code = PROJECT_NAMES.get(e.get("project_id") or 0, "")
        out.append({
            "id": str(e.get("id")),
            "desc": e.get("description") or "(no description)",
            "project": code,
            "color": COLORS.get(code, DEFAULT_COLOR),
            "tags": e.get("tags") or [],
            "start": st_c, "end": en_c, "running": running,
        })
    out.sort(key=lambda r: r["start"])
    return out


def build_timeline() -> dict:
    today = _dt.datetime.now(TZ).date()
    now = _dt.datetime.now(TZ)
    entries = _fetch_today()
    logged = _ledger(today)

    rows: list[dict] = []
    tracked_min = 0

    def hhmm(dt: _dt.datetime) -> str:
        return dt.strftime("%H:%M")

    def add_dividers(upto: _dt.datetime, cursor_holder: list):
        """Emit 地支 dividers for block starts crossed before `upto`."""
        while cursor_holder[0] < len(BLOCKS):
            h, name = BLOCKS[cursor_holder[0]]
            bdt = _dt.datetime.combine(today, _dt.time(0, 0), TZ) + _dt.timedelta(hours=h)
            if bdt > upto or bdt > now:
                break
            rows.append({"type": "divider", "label": f"{name} {h:02d}:00"})
            cursor_holder[0] += 1

    bidx = [0]
    day0 = _dt.datetime.combine(today, _dt.time(0, 0), TZ)

    def emit_gap(a: _dt.datetime, b: _dt.datetime):
        """Emit the untracked span (a, b), split at 地支 block boundaries so
        dividers land inside long gaps instead of stacking above them."""
        cur = a
        while bidx[0] < len(BLOCKS):
            h, name = BLOCKS[bidx[0]]
            bdt = day0 + _dt.timedelta(hours=h)
            if bdt >= b or bdt > now:
                break
            if (bdt - cur).total_seconds() >= MIN_GAP_MIN * 60:
                rows.append({"type": "gap", "start": hhmm(cur), "end": hhmm(bdt),
                             "minutes": int((bdt - cur).total_seconds() // 60)})
            rows.append({"type": "divider", "label": f"{name} {h:02d}:00"})
            bidx[0] += 1
            cur = max(cur, bdt)
        if (b - cur).total_seconds() >= MIN_GAP_MIN * 60:
            rows.append({"type": "gap", "start": hhmm(cur), "end": hhmm(b),
                         "minutes": int((b - cur).total_seconds() // 60)})

    cursor = _dt.datetime.combine(today, _dt.time(DAY_START_HOUR, 0), TZ)
    for e in entries:
        emit_gap(cursor, e["start"])
        add_dividers(e["start"], bidx)
        mins = int(round((e["end"] - e["start"]).total_seconds() / 60))
        tracked_min += mins
        rows.append({"type": "entry", "id": e["id"], "desc": e["desc"],
                     "project": e["project"], "color": e["color"],
                     "tags": e.get("tags") or [],
                     "start": hhmm(e["start"]), "end": ("now" if e["running"] else hhmm(e["end"])),
                     "minutes": mins, "running": e["running"],
                     "logged": e["id"] in logged})
        cursor = max(cursor, e["end"])
    emit_gap(cursor, now)
    add_dividers(now, bidx)

    # Σ points for the header (same source as mobile dtd: 0分 col D on Ix).
    points = None
    try:
        from neon import excel
        r = excel.read("0分", "D", date="%d/%d" % (today.month, today.day))
        if r.get("ok") and str(r.get("value") or "").strip():
            points = int(float(r["value"]))
    except Exception as e:
        print("WARN points:", e, file=sys.stderr)

    return {"rows": rows, "tracked_min": tracked_min, "points": points,
            "date": today.isoformat()}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def fill_gap(desc: str, start_hhmm: str, end_hhmm: str) -> dict:
    m = _AT.search(desc)
    code = m.group(1) if m else ""
    desc_clean = _AT.sub("", desc).strip()
    pid = PROJECT_MAP.get(code)
    today = _dt.datetime.now(TZ).date()
    try:
        st = _dt.datetime.combine(today, _dt.time(*map(int, start_hhmm.split(":"))), TZ)
        en = _dt.datetime.combine(today, _dt.time(*map(int, end_hhmm.split(":"))), TZ)
    except Exception:
        return {"ok": False, "error": "bad time format (HH:MM)"}
    if en <= st:
        return {"ok": False, "error": "end must be after start"}
    dur = int((en - st).total_seconds())
    fmt = "%Y-%m-%dT%H:%M:%S%z"
    try:
        r = toggl_api.create_entry(desc_clean, st.strftime(fmt), en.strftime(fmt), dur,
                                   project_id=pid)
        return {"ok": bool(r), "project": code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


_FMT = "%Y-%m-%dT%H:%M:%S%z"


def _get_entry(entry_id: str) -> dict | None:
    """True (un-clipped) entry from Toggl by id — the timeline's own rows are
    clipped to [midnight, now] for display (see _fetch_today), so any mutation
    must re-fetch the real record rather than trust a client-submitted HH:MM
    built from a clipped row (bug 2026-08-06: a 睡觉 entry starting 23:30
    yesterday displays as start=00:00 today; blindly resubmitting that would
    silently delete the real overnight minutes)."""
    today = _dt.datetime.now(TZ).date()
    entries = toggl_api.get_entries(
        start_date=(today - _dt.timedelta(days=1)).isoformat(),
        end_date=(today + _dt.timedelta(days=2)).isoformat()) or []
    for e in entries:
        if str(e.get("id")) == str(entry_id):
            return e
    return None


def _split_chunk_minutes(duration_min: float) -> int:
    """Fixed split-chunk size, scaled down for short entries so a 6-minute
    entry doesn't refuse to split just because 10 doesn't fit."""
    if duration_min < 5:
        return 1
    if duration_min < 10:
        return 5
    return 10


def _would_touch_logged(entry_id: str, start_dt: _dt.datetime, end_dt: _dt.datetime,
                        ledger: dict) -> bool:
    """True if retiming `entry_id` to [start_dt, end_dt) would make
    trim_range() delete/shrink another entry that's already been credited.
    trim_range replaces whatever it trims with brand-new ids (create_entry
    for the surviving remainder) that are absent from the ledger — silently
    re-swipeable/re-loggable minutes that were already counted once (bug
    2026-08-06, the same mechanism that tripled the quarterly-checkin task)."""
    day = start_dt.date()
    entries = toggl_api.get_entries(
        start_date=(day - _dt.timedelta(days=1)).isoformat(),
        end_date=(day + _dt.timedelta(days=2)).isoformat()) or []
    for e in entries:
        eid = str(e.get("id"))
        if eid == str(entry_id) or eid not in ledger:
            continue
        try:
            e_start = _parse_iso(e["start"])
        except Exception:
            continue
        running = (e.get("duration") or 0) < 0
        e_end = _dt.datetime.now(TZ) if running else (
            _parse_iso(e["stop"]) if e.get("stop") else None)
        if e_end is None or e_end <= start_dt or e_start >= end_dt:
            continue
        return True
    return False


def edit_entry(entry_id: str, desc: str, start_hhmm: str, end_hhmm: str,
               project_code: str) -> dict:
    """Edit description/project/time. Time changes go through the same
    trim_range() MECE-keeping path the desktop TUI uses. Guards (2026-08-06
    review): refuses to touch a cross-midnight entry's clipped time (can't
    safely reconstruct which calendar day a bare HH:MM belongs to), refuses
    to retime an already-logged entry, and refuses a retime that would bump
    trim_range into deleting/shrinking a DIFFERENT already-logged entry."""
    e = _get_entry(entry_id)
    if not e:
        return {"ok": False, "error": "entry not found (may have changed elsewhere)"}
    today = _dt.datetime.now(TZ).date()
    true_start = _parse_iso(e["start"])
    running = (e.get("duration") or 0) < 0 or not e.get("stop")
    true_end = None if running else _parse_iso(e["stop"])

    desc = (desc or "").strip()
    if not desc:
        return {"ok": False, "error": "description required"}
    fields: dict = {"description": desc}
    if project_code:
        pid = PROJECT_MAP.get(project_code)
        if pid is None:
            return {"ok": False, "error": f"unknown project @{project_code}"}
        fields["project_id"] = pid

    cur_start_hhmm = true_start.strftime("%H:%M")
    cur_end_hhmm = "now" if running else true_end.strftime("%H:%M")
    start_changed = bool(start_hhmm) and start_hhmm != cur_start_hhmm
    end_changed = bool(end_hhmm) and end_hhmm not in ("", "now") and end_hhmm != cur_end_hhmm

    if end_changed and running:
        return {"ok": False, "error": "still running — stop it first to set an end time"}

    if start_changed or end_changed:
        if true_start.date() != today or (true_end is not None and true_end.date() != today):
            return {"ok": False, "error": "cross-midnight entry — retime from desktop"}
        try:
            new_start = (_dt.datetime.combine(today, _dt.time(*map(int, start_hhmm.split(":"))), TZ)
                        if start_changed else true_start)
        except Exception:
            return {"ok": False, "error": "bad start time (HH:MM)"}

        if running:
            # No fixed end to reason about yet — just move the start.
            if new_start >= _dt.datetime.now(TZ):
                return {"ok": False, "error": "start must be in the past"}
            fields["start"] = new_start.strftime(_FMT)
        else:
            try:
                new_end = (_dt.datetime.combine(today, _dt.time(*map(int, end_hhmm.split(":"))), TZ)
                          if end_changed else true_end)
            except Exception:
                return {"ok": False, "error": "bad end time (HH:MM)"}
            if new_end <= new_start:
                return {"ok": False, "error": "end must be after start"}
            ledger = _ledger(today)
            if str(entry_id) in ledger:
                return {"ok": False, "error": "already logged — can't retime a logged entry"}
            if _would_touch_logged(entry_id, new_start, new_end, ledger):
                return {"ok": False, "error": "would overlap an already-logged entry — refusing"}
            try:
                toggl_api.trim_range(new_start, new_end, exclude_ids={e.get("id")})
            except Exception as ex:
                return {"ok": False, "error": f"trim failed (entry not yet moved): {ex}"[:200]}
            fields["start"] = new_start.strftime(_FMT)
            fields["stop"] = new_end.strftime(_FMT)
            fields["duration"] = int((new_end - new_start).total_seconds())

    try:
        toggl_api.update_entry(e.get("id"), **fields)
        return {"ok": True}
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}


def split_entry(entry_id: str, mode: str) -> dict:
    """Split into a fixed chunk (see _split_chunk_minutes) + remainder.
    `mode="top"` carves the chunk off the START; `mode="bottom"` carves it
    off the END. Id-ownership always follows the EARLIER piece (matches the
    desktop TUI's ^P split convention) — the original id shrinks to become
    whichever piece comes first chronologically, and a new entry is created
    for whichever piece comes second. The new (later) piece is created
    BEFORE the original is shrunk (2026-08-06 review): if the shrink call
    then fails, the worst case is a transient overlap, not a permanently
    lost chunk of time."""
    if mode not in ("top", "bottom"):
        return {"ok": False, "error": "bad mode"}
    e = _get_entry(entry_id)
    if not e:
        return {"ok": False, "error": "entry not found (may have changed elsewhere)"}
    running = (e.get("duration") or 0) < 0 or not e.get("stop")
    if running:
        return {"ok": False, "error": "still running — stop it first"}
    today = _dt.datetime.now(TZ).date()
    start = _parse_iso(e["start"])
    end = _parse_iso(e["stop"])
    if start.date() != today or end.date() != today:
        return {"ok": False, "error": "cross-midnight entry — split from desktop"}

    duration_min = (end - start).total_seconds() / 60
    chunk = _split_chunk_minutes(duration_min)
    if duration_min <= chunk:
        return {"ok": False, "error": f"too short to split (need > {chunk}m)"}

    if str(entry_id) in _ledger(today):
        return {"ok": False, "error": "already logged — can't split a logged entry"}

    cut = (start + _dt.timedelta(minutes=chunk) if mode == "top"
          else end - _dt.timedelta(minutes=chunk))
    desc = e.get("description") or ""
    proj_id = e.get("project_id")
    tags = e.get("tags") or None

    try:
        toggl_api.create_entry(desc, cut.strftime(_FMT), end.strftime(_FMT),
                               int((end - cut).total_seconds()), proj_id, tags)
        toggl_api.update_entry(e.get("id"), stop=cut.strftime(_FMT),
                               duration=int((cut - start).total_seconds()))
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}
    return {"ok": True, "chunk_minutes": chunk}


_DF_MOD = None  # lazy did-fast module (for habit-name lookups only)


def habit_tags(tags: list[str]) -> list[str]:
    """The subset of a Toggl entry's tags that name known habits (0n or 1n+
    headers/aliases) — those get a secondary minutes log on swipe (user
    request 2026-07-27: a run tagged 其他人 should credit both ledgers).
    Meta tags (-1/-2/-3/2, project codes, …) resolve to nothing and are
    ignored. Best-effort: an import failure just skips secondaries."""
    global _DF_MOD
    if not tags:
        return []
    try:
        if _DF_MOD is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("df_tags", DID_FAST)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["df_tags"] = mod
            spec.loader.exec_module(mod)
            _DF_MOD = mod
        df = _DF_MOD
        h = df.load_headers()
        known = {df.header_normalize(k)
                 for k in list(h.get("0n", {})) + list(h.get("1n", {}))}
        known |= {df.header_normalize(a) for a in df.ONENEON_ALIASES}
        return [t for t in tags if df.header_normalize(str(t)) in known]
    except Exception as e:
        print("WARN habit_tags:", e, file=sys.stderr)
        return []


def log_entry(entry_id: str, desc: str, minutes: int, project: str,
              tags: list[str] | None = None) -> dict:
    today = _dt.datetime.now(TZ).date()
    if str(entry_id) in _ledger(today):
        return {"ok": True, "already": True}
    text = f"{desc} {minutes}"
    if project:
        text += f" @{project}"
    # Habit tags ride along as extra comma-separated /did items — did-fast
    # processes each independently (其他人 61 → 其他人 0n column, etc.).
    extra = habit_tags(tags or [])
    for t in extra:
        text += f", {t} {minutes}"
    try:
        proc = subprocess.run(["/usr/bin/python3", str(DID_FAST), text],
                              capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did-fast timeout"}
    out = proc.stdout.strip()
    data = None
    brace = out.find("{")
    if brace >= 0:
        try:
            data = json.loads(out[brace:])
        except Exception:
            data = None
    step = None
    tag_steps = []
    if data and data.get("results"):
        step = data["results"][0].get("step")
        tag_steps = [f"{r.get('name')}→{r.get('step')}"
                     for r in data["results"][1:]]
    needs_agent = bool(data and data.get("agent_needed"))
    ok = proc.returncode == 0 and step is not None
    if ok:
        note = f"{desc} {minutes} → {step}"
        if tag_steps:
            note += " + " + ", ".join(tag_steps)
        _ledger_add(today, entry_id, note)
    return {"ok": ok, "step": step, "tag_steps": tag_steps,
            "needs_agent": needs_agent,
            "stderr_tail": proc.stderr.strip()[-200:]}


# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)


@app.route("/api/timeline")
def api_timeline():
    try:
        return jsonify({"ok": True, **build_timeline()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/fill", methods=["POST"])
def api_fill():
    b = request.get_json(force=True, silent=True) or {}
    desc = (b.get("desc") or "").strip()
    if not desc:
        return jsonify({"ok": False, "error": "no description"}), 400
    return jsonify(fill_gap(desc, b.get("start") or "", b.get("end") or ""))


@app.route("/api/log", methods=["POST"])
def api_log():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id") or not b.get("desc"):
        return jsonify({"ok": False, "error": "id+desc required"}), 400
    return jsonify(log_entry(str(b["id"]), b["desc"].strip(),
                             int(b.get("minutes") or 0), (b.get("project") or "").strip(),
                             tags=b.get("tags") or []))


@app.route("/api/edit", methods=["POST"])
def api_edit():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id"):
        return jsonify({"ok": False, "error": "id required"}), 400
    return jsonify(edit_entry(str(b["id"]), b.get("desc") or "",
                              (b.get("start") or "").strip(), (b.get("end") or "").strip(),
                              (b.get("project") or "").strip()))


@app.route("/api/split", methods=["POST"])
def api_split():
    b = request.get_json(force=True, silent=True) or {}
    if not b.get("id") or b.get("mode") not in ("top", "bottom"):
        return jsonify({"ok": False, "error": "id+mode(top|bottom) required"}), 400
    return jsonify(split_entry(str(b["id"]), b["mode"]))


@app.route("/api/projects")
def api_projects():
    return jsonify({"ok": True, "codes": sorted(PROJECT_MAP.keys())})


@app.route("/")
def index():
    return render_template_string(PAGE)


# ---------------------------------------------------------------------------
# Frontend — same terminal styling as mobile dtd, single file, no deps
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>janus</title>
<style>
  :root { --bg:#1b1b1b; --dim:#777; --go:#00e676; --gap:#555; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:#cfcfcf;
    font:15px/1.2 ui-monospace,"SF Mono",Menlo,Monaco,"Cascadia Mono",monospace;
    -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5;
    padding:calc(env(safe-area-inset-top) + 9px) 14px 8px;
    background:#1b1b1bee; backdrop-filter:blur(6px);
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid #2a2a2a; }
  header .brand { font-weight:700; letter-spacing:1px; }
  header .brand b { color:var(--go); }
  .tally { color:var(--dim); font-variant-numeric:tabular-nums; }
  .tally b { color:var(--go); }
  #reload { background:none; border:1px solid #333; color:var(--dim);
    border-radius:6px; padding:3px 9px; font-family:inherit; font-size:15px; }
  main { padding:2px 0 calc(env(safe-area-inset-bottom) + 60px); }
  .div { color:var(--dim); padding:8px 14px 2px; font-size:12px; letter-spacing:1px;
    border-top:1px solid #242424; }
  .row { position:relative; overflow:hidden; }
  .row .track { position:absolute; inset:0; background:var(--go); color:#003;
    font-weight:800; display:flex; align-items:center; padding-left:16px; opacity:0; }
  .row .track.edit { background:#2979ff; color:#001a3d;
    justify-content:flex-end; padding-left:0; padding-right:16px; }
  .line { position:relative; display:flex; align-items:center; gap:10px;
    padding:9px 14px; background:var(--bg); min-height:38px;
    transform:translateX(0); transition:transform .05s linear; will-change:transform;
    touch-action:pan-y; }
  .line.snap { transition:transform .22s cubic-bezier(.2,.7,.2,1); }
  .ttl { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .meta { white-space:nowrap; font-variant-numeric:tabular-nums; color:var(--dim); }
  .gaprow .ttl { color:var(--gap); font-style:italic; }
  .logged .ttl::after { content:" ✓"; color:var(--go); }
  .logged { opacity:.45; }
  .running .ttl::before { content:"▶ "; color:var(--go); }
  .empty,.loading { text-align:center; color:var(--dim); padding:60px 20px; }
  .toast { position:fixed; left:50%; bottom:calc(env(safe-area-inset-bottom) + 20px);
    transform:translateX(-50%) translateY(16px); background:var(--go); color:#003;
    font-weight:700; padding:9px 18px; border-radius:8px; opacity:0; transition:.22s;
    z-index:20; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.err { background:#ff4081; color:#2a0010; }
  #dlg, #editDlg { position:fixed; inset:0; background:#000a; z-index:10; display:none;
    align-items:flex-end; }
  #dlg.show, #editDlg.show { display:flex; }
  #dlg .card, #editDlg .card { background:#232323; width:100%; padding:16px 16px
    calc(env(safe-area-inset-bottom) + 16px); border-radius:14px 14px 0 0; }
  #dlg h3, #editDlg h3 { margin:0 0 12px; font-size:15px; color:#cfcfcf; font-weight:700; }
  #dlg input, #editDlg input, #editDlg select { width:100%; background:#1b1b1b; border:1px solid #333;
    color:#cfcfcf; font:15px ui-monospace,Menlo,monospace; border-radius:8px; padding:10px 12px;
    margin-bottom:10px; }
  #dlg .times, #editDlg .times { display:flex; gap:10px; }
  #dlg .times input, #editDlg .times input { flex:1; text-align:center; }
  #dlg .btns, #editDlg .btns { display:flex; gap:10px; margin-top:4px; }
  #dlg button, #editDlg button { flex:1; font:700 15px ui-monospace,Menlo,monospace; border:none;
    border-radius:8px; padding:12px; }
  #dlg .save, #editDlg .save { background:var(--go); color:#003; }
  #dlg .cancel, #editDlg .cancel { background:#333; color:#aaa; }
  #editDlg .split { background:#2979ff; color:#001a3d; }
  #editDlg input:disabled { opacity:.4; }
</style>
</head>
<body>
<header>
  <div class="brand">jan<b>u</b>s</div>
  <div class="tally"><b id="pts">–</b> 分 · <span id="trk">0:00</span></div>
  <button id="reload">↻</button>
</header>
<main id="list"><div class="loading">loading…</div></main>

<div id="dlg">
  <div class="card">
    <h3>fill gap</h3>
    <input id="d-desc" placeholder="description (@code for project)" autocomplete="off">
    <div class="times">
      <input id="d-start" inputmode="numeric" placeholder="HH:MM">
      <input id="d-end" inputmode="numeric" placeholder="HH:MM">
    </div>
    <div class="btns">
      <button class="cancel" onclick="closeDlg()">cancel</button>
      <button class="save" onclick="saveDlg()">save</button>
    </div>
  </div>
</div>

<div id="editDlg">
  <div class="card">
    <h3>edit entry</h3>
    <input id="e-desc" placeholder="description" autocomplete="off">
    <div class="times">
      <input id="e-start" inputmode="numeric" placeholder="HH:MM">
      <input id="e-end" inputmode="numeric" placeholder="HH:MM">
    </div>
    <select id="e-project"></select>
    <div class="btns">
      <button class="cancel" onclick="closeEditDlg()">cancel</button>
      <button class="save" onclick="saveEdit()">save</button>
    </div>
    <div class="btns" style="margin-top:8px">
      <button class="split" onclick="doSplit('top')">split top</button>
      <button class="split" onclick="doSplit('bottom')">split bottom</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>

<script>
const list = document.getElementById('list');
const toastEl = document.getElementById('toast');
const dlg = document.getElementById('dlg');

function toast(msg, err){
  toastEl.textContent = msg;
  toastEl.classList.toggle('err', !!err);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(()=>toastEl.classList.remove('show'), 1800);
}

async function load(){
  list.innerHTML = '<div class="loading">loading…</div>';
  try {
    const r = await fetch('/api/timeline');
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fetch failed');
    document.getElementById('pts').textContent = (d.points==null?'–':d.points);
    document.getElementById('trk').textContent =
      Math.floor(d.tracked_min/60)+':'+String(d.tracked_min%60).padStart(2,'0');
    render(d.rows);
  } catch(e){ list.innerHTML = '<div class="empty">⚠ '+e.message+'</div>'; }
}

function render(rows){
  if(!rows.length){ list.innerHTML = '<div class="empty">no entries yet</div>'; return; }
  list.innerHTML = '';
  for(const r of rows){
    if(r.type === 'divider'){
      const d = document.createElement('div');
      d.className = 'div'; d.textContent = r.label;
      list.appendChild(d);
    } else {
      list.appendChild(makeRow(r));
    }
  }
  list.scrollIntoView(false);
  window.scrollTo(0, document.body.scrollHeight);
}

function makeRow(r){
  const row = document.createElement('div');
  row.className = 'row';
  const track = document.createElement('div');
  track.className = 'track';
  track.textContent = r.type==='gap' ? '+ fill' : 'neon log 分 ('+r.minutes+'m)';
  row.appendChild(track);

  // Left-swipe reveal (edit) — entries only; a gap has nothing to edit.
  let trackEdit = null;
  if(r.type === 'entry'){
    trackEdit = document.createElement('div');
    trackEdit.className = 'track edit';
    trackEdit.textContent = 'edit';
    row.appendChild(trackEdit);
  }

  const line = document.createElement('div');
  line.className = 'line' + (r.type==='gap'?' gaprow':'') +
    (r.logged?' logged':'') + (r.running?' running':'');
  if(r.type==='entry') line.style.color = r.color;
  const ttl = document.createElement('span');
  ttl.className = 'ttl';
  ttl.textContent = r.type==='gap' ? '· empty ·' : r.desc;
  const meta = document.createElement('span');
  meta.className = 'meta';
  meta.textContent = r.start+'–'+r.end+' · '+r.minutes+'m';
  line.appendChild(ttl); line.appendChild(meta);
  row.appendChild(line);
  bindSwipe(row, line, track, trackEdit, r);
  return row;
}

function bindSwipe(row, line, track, trackEdit, r){
  let x0=null, dx=0, dragging=false;
  const W = () => row.offsetWidth;
  const start = x=>{ x0=x; dx=0; dragging=true; line.classList.remove('snap'); };
  const move = x=>{
    if(!dragging) return;
    dx = x - x0;
    if(!trackEdit) dx = Math.max(0, dx);  // gap rows: right-swipe only
    line.style.transform = 'translateX('+dx+'px)';
    if(dx >= 0){
      track.style.opacity = Math.min(1, dx/(W()*0.4));
      if(trackEdit) trackEdit.style.opacity = 0;
    } else {
      track.style.opacity = 0;
      trackEdit.style.opacity = Math.min(1, -dx/(W()*0.4));
    }
  };
  const end = ()=>{
    if(!dragging) return; dragging=false;
    line.classList.add('snap');
    line.style.transform='translateX(0)';
    track.style.opacity=0; if(trackEdit) trackEdit.style.opacity=0;
    if(dx > W()*0.42) act(row, line, r);
    else if(trackEdit && dx < -W()*0.42) openEdit(r);
  };
  line.addEventListener('touchstart', e=>start(e.touches[0].clientX), {passive:true});
  line.addEventListener('touchmove',  e=>move(e.touches[0].clientX),  {passive:true});
  line.addEventListener('touchend', end);
  line.addEventListener('mousedown', e=>{start(e.clientX);
    const mm=ev=>move(ev.clientX), mu=()=>{end();
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);};
    document.addEventListener('mousemove',mm); document.addEventListener('mouseup',mu);});
}

let gapCtx = null;
function act(row, line, r){
  if(r.type === 'gap'){
    gapCtx = r;
    document.getElementById('d-desc').value = '';
    document.getElementById('d-start').value = r.start;
    document.getElementById('d-end').value = r.end;
    dlg.classList.add('show');
    setTimeout(()=>document.getElementById('d-desc').focus(), 60);
    return;
  }
  if(r.running){ toast('still running', true); return; }
  if(r.logged){ toast('already logged', true); return; }
  commitLog(line, r);
}

async function commitLog(line, r){
  line.classList.add('logged');
  try {
    const resp = await fetch('/api/log', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:r.id, desc:r.desc, minutes:r.minutes, project:r.project, tags:r.tags||[]})});
    const d = await resp.json();
    if(d.already){ toast('already logged'); return; }
    if(!d.ok){
      line.classList.remove('logged');
      toast(d.needs_agent ? 'no route — use /did on desktop' : 'log failed', true);
      return;
    }
    let msg = '+'+r.minutes+'m → '+(d.step||'neon');
    if(d.tag_steps && d.tag_steps.length) msg += ' + '+d.tag_steps.join(', ');
    toast(msg+' ✓');
  } catch(e){ line.classList.remove('logged'); toast('offline', true); }
}

function closeDlg(){ dlg.classList.remove('show'); gapCtx=null; }
async function saveDlg(){
  const desc = document.getElementById('d-desc').value.trim();
  const start = document.getElementById('d-start').value.trim();
  const end = document.getElementById('d-end').value.trim();
  if(!desc){ toast('need a description', true); return; }
  closeDlg();
  try {
    const r = await fetch('/api/fill', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({desc, start, end})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'create failed', true); return; }
    toast('tracked ✓' + (d.project?' → '+d.project:''));
    load();
  } catch(e){ toast('offline', true); }
}

let projectCodes = [];
async function loadProjects(){
  try {
    const r = await fetch('/api/projects');
    const d = await r.json();
    if(d.ok) projectCodes = d.codes;
  } catch(e){ /* dropdown just stays empty — non-fatal */ }
}

const editDlg = document.getElementById('editDlg');
let editCtx = null;

function openEdit(r){
  editCtx = r;
  document.getElementById('e-desc').value = r.desc;
  document.getElementById('e-start').value = r.start;
  const endEl = document.getElementById('e-end');
  endEl.value = r.end;
  endEl.disabled = !!r.running;   // no fixed end yet — stop it first (desktop mirrors this)
  const sel = document.getElementById('e-project');
  sel.innerHTML = '<option value="">(none)</option>' +
    projectCodes.map(c=>'<option value="'+c+'"'+(c===r.project?' selected':'')+'>'+c+'</option>').join('');
  editDlg.classList.add('show');
}

function closeEditDlg(){ editDlg.classList.remove('show'); editCtx = null; }

async function saveEdit(){
  if(!editCtx) return;
  const desc = document.getElementById('e-desc').value.trim();
  const start = document.getElementById('e-start').value.trim();
  const endEl = document.getElementById('e-end');
  const end = endEl.disabled ? '' : endEl.value.trim();
  const project = document.getElementById('e-project').value;
  if(!desc){ toast('need a description', true); return; }
  const id = editCtx.id;
  closeEditDlg();
  try {
    const r = await fetch('/api/edit', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id, desc, start, end, project})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'edit failed', true); return; }
    toast('saved ✓');
    load();
  } catch(e){ toast('offline', true); }
}

async function doSplit(mode){
  if(!editCtx) return;
  const id = editCtx.id;
  closeEditDlg();
  try {
    const r = await fetch('/api/split', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id, mode})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'split failed', true); return; }
    toast('split ✓ ('+d.chunk_minutes+'m)');
    load();
  } catch(e){ toast('offline', true); }
}

document.getElementById('reload').onclick = load;
loadProjects();
load();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
