#!/usr/bin/env python3
"""
dtd — Do The Damn thing. iPhone-first mirror of the `dtd` fzf TUI.

Same data source (task-queue.json), same domain colors, same short names,
same right-justified (time)[value]{bonus} estimates — but as a swipeable
web list. Swipe a card right → runs the real /did (did-fast.py): closes the
Todoist task AND writes its points to Neon.

Run:   python3 dtd.py            (binds 0.0.0.0:5560)
Open:  http://ix:5560            (from the phone, same as the dashboard)
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

# ---------------------------------------------------------------------------
# Config / paths
# ---------------------------------------------------------------------------
PORT = 5560
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"
STATE_DIR = Path.home() / ".local/state/jm"
CACHE = STATE_DIR / "task-queue.json"
DONE_FILE = STATE_DIR / "completed-today.json"
SNOOZE_FILE = STATE_DIR / "dtd-block-snooze.json"
DEFERRED_DIR = Path.home() / ".cache/jm"
CACHE_MAX_AGE = 180  # seconds; refresh from Todoist if staler
SUMMARY_MAX_AGE = 45  # seconds; day-total (points + done) cache

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
TODOIST_TOKEN_FILE = Path.home() / ".config/todoist/token"

# Neon domain palette — mirrors tools/did/dtd.sh COLORS (RGB → hex).
COLORS = {
    "g245": "#00e676", "epcn": "#00bfa5", "s897": "#1b5e20", "hcmc2": "#ffd600",
    "xk87": "#fd6c1d", "xk88": "#e65100", "hci": "#63ede0", "i9": "#2979ff",
    "n156": "#1249b4", "hcmc": "#0d3b66", "m5x2": "#d50032", "m828": "#9b0023",
    "hcb": "#f81d78",
    "hcbp": "#ff4081", "infra": "#9e9e9e", "i444": "#616161", "i447": "#a89c8a",
    "hcm": "#aa00ff", "hcmp": "#7c4dff", "hcmr": "#bda6ff", "家": "#ff4136",
    "睡觉": "#666666",
}
DEFAULT_COLOR = "#bdbdbd"  # unlabelled / f693 / i446 → terminal default fg

# ---------------------------------------------------------------------------
# Parsing helpers (mirror dtd.sh)
# ---------------------------------------------------------------------------
_ANN = re.compile(r" *\(\d*\)| *\[\d*G?\]| *\{\d*\}")
_TIME = re.compile(r"\((\d+)\)")
_VAL = re.compile(r"\[(\d+)G?\]")
_BONUS = re.compile(r"\{(\d+)\}")

_MDLINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_TAG = re.compile(r"@(\S+)")


def strip_ann(s: str) -> str:
    # Markdown links (/todo stores URLs as "[(link)](https://…)") collapse to
    # their visible text so the card shows "(link)" instead of the raw URL.
    s = _MDLINK.sub(lambda m: m.group(1) or "(link)", s)
    return re.sub(r"  +", " ", _ANN.sub("", s)).strip()

def parse_est(content: str) -> tuple[str, int]:
    """Return ('(30) [20]' canonical string, points)."""
    tm, vm, bm = _TIME.search(content), _VAL.search(content), _BONUS.search(content)
    toks = []
    if tm:
        toks.append("(%s)" % tm.group(1))
    if vm:
        toks.append("[%s]" % vm.group(1))
    if bm:
        toks.append("{%s}" % bm.group(1))
    pts = int(vm.group(1)) if vm else (int(bm.group(1)) if bm else 0)
    return " ".join(toks), pts

# Ritual cards (label '-1neon') carry no domain label of their own — resolve
# their color from the ritual tag in the name instead. Mirrors dtd.sh's own
# RITUAL_DOMAIN (tools/did/dtd.sh:1120,1352-1355) exactly.
RITUAL_DOMAIN = {"-1ibx": "i9", "-1g": "g245", "-1l": "g245", "-1t": "n156", "سمش": "hcm"}

def color_of(labels: list[str], content: str = "") -> tuple[str, str]:
    if "-1neon" in labels:
        bare = strip_ann(content).lower().replace("😈", "").strip()
        for tag, dom in RITUAL_DOMAIN.items():
            if bare == tag or tag in bare.split():
                return COLORS[dom], dom
    for lbl in labels:
        if lbl in COLORS:
            return COLORS[lbl], lbl
    return DEFAULT_COLOR, ""

def _prank(p) -> int:
    try:
        return -(int(p) or 1)
    except (TypeError, ValueError):
        return -1

# ---------------------------------------------------------------------------
# Cache load + section ordering (mirror dtd.sh list build)
# ---------------------------------------------------------------------------
_refresh_lock = threading.Lock()

def _run_refresh_subprocess():
    try:
        subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"],
                       capture_output=True, text=True, timeout=25)
    except Exception as e:
        print("WARN refresh-cache:", e, file=sys.stderr)
    finally:
        _refresh_lock.release()

def _refresh_cache_if_stale(force: bool = False):
    """force=True (↻ button, post-add reload): the caller is explicitly
    waiting on fresh data, so refresh synchronously as before.

    force=False (every normal page load once the cache passes
    CACHE_MAX_AGE): NEVER block the request on this. It used to call the
    same synchronous subprocess.run here, so any request landing on a stale
    cache waited on a live Todoist refresh (with its own internal retries)
    before the page could render at all — "dtd web hangs on loading (>20s)"
    (2026-08-11). Reproduced live: consecutive calls measured 18.2s, 15.7s,
    then 66.8s — WORSE each time, because nothing stopped overlapping
    requests from each spawning their own refresh subprocess, piling
    concurrent Todoist calls into rate limiting and slowing every one of
    them down together (the plain script alone takes ~2s run directly).
    Now the stale case kicks the refresh off in a background thread (deduped
    by _refresh_lock — at most one in flight) and returns immediately; the
    request serves whatever's already cached (at most a few minutes old),
    and the next request picks up the freshened cache once it lands."""
    stale = force
    if not stale:
        try:
            d = json.loads(CACHE.read_text())
            upd = _dt.datetime.fromisoformat(d.get("updated", "1970-01-01"))
            stale = (_dt.datetime.now() - upd).total_seconds() > CACHE_MAX_AGE
        except Exception:
            stale = True
    if not stale:
        return
    if force:
        try:
            subprocess.run(["/usr/bin/python3", str(DID_FAST), "--refresh-cache"],
                           capture_output=True, text=True, timeout=25)
        except Exception as e:
            print("WARN refresh-cache:", e, file=sys.stderr)
        return
    if _refresh_lock.acquire(blocking=False):
        threading.Thread(target=_run_refresh_subprocess, daemon=True).start()
    # else: a refresh is already in flight — don't stack another, just serve
    # the current cache below.

def _completed_ids() -> set[str]:
    """Todoist ids completed today (from completed-today.json `ids` map).

    Hiding is by id ONLY, never by name. Names are unreliable: -1neon block
    rituals (سمش / -1g / -1ibx) are deleted+recreated with identical names at
    every 2h boundary, so a stale name-only completion (e.g. a goal-set that
    recorded '😈 -1g' with no id) would wrongly suppress the new block's card.
    Genuinely-closed tasks drop out of the cache on refresh regardless; this id
    set only guards the window between a completion and the next cache refresh.
    """
    try:
        d = json.loads(DONE_FILE.read_text())
    except Exception:
        return set()
    if d.get("date") != _dt.date.today().isoformat():
        return set()
    return {str(v) for v in (d.get("ids") or {}).values()}

def _snoozed_ids() -> set[str]:
    """Ids block-snoozed (ctrl-v) in the desktop dtd, hidden until their
    chosen 地支 block's hour arrives. Mirrors tools/did/dtd.sh's read of the
    same file — without this, a task delayed to later today in the terminal
    view reappeared immediately here since dtd web never read this file
    (2026-08-11 bug). File is {date, snoozes: {id: start_hour}}; a stale
    date (leftover from a previous day) voids it, same as dtd.sh."""
    try:
        sn = json.loads(SNOOZE_FILE.read_text())
    except Exception:
        return set()
    if sn.get("date") != _dt.date.today().isoformat():
        return set()
    now_hour = _dt.datetime.now().hour
    return {str(k) for k, v in (sn.get("snoozes") or {}).items()
            if now_hour < int(v)}

def _deferred_habit_ids() -> set[str]:
    """Recurring 0neon/夜neon habit-parent ids deferred (/defer) today,
    hidden for the rest of today. Mirrors tools/did/dtd.sh's read of the same
    per-day marker file (same gap as _snoozed_ids above)."""
    p = DEFERRED_DIR / f"habits-deferred-{_dt.date.today().isoformat()}.ids"
    try:
        return {l.strip() for l in p.read_text().splitlines() if l.strip()}
    except OSError:
        return set()

def build_tasks(force_refresh: bool = False) -> list[dict]:
    _refresh_cache_if_stale(force=force_refresh)
    try:
        d = json.loads(CACHE.read_text())
    except Exception as e:
        print("WARN read cache:", e, file=sys.stderr)
        return []

    today = _dt.date.today().isoformat()
    tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()

    def sec(key, bound):
        return [t for t in d.get(key, []) if isinstance(t, dict)
                and t.get("due") and t["due"] <= bound]

    today_tasks = [t for t in d.get("today", []) if isinstance(t, dict)
                   and t.get("due") and t["due"] <= today]
    has = lambda t, lab: lab in t.get("labels", [])

    rituals = [t for t in today_tasks if has(t, "-1neon")]
    neg1g = [t for t in today_tasks if has(t, "#-1g") and not has(t, "-1neon")]
    _deferred_ids = _deferred_habit_ids()
    zeroneon = [t for t in sec("0neon", tomorrow) + sec("夜neon", tomorrow)
                if t.get("id") not in _deferred_ids]
    oneneon = sec("1neon", today)
    zerog = [t for t in today_tasks
             if has(t, "#0g") and not has(t, "-1neon") and not has(t, "#-1g")]
    critical = sec("关键路径", today)
    placed = lambda t: has(t, "-1neon") or has(t, "#-1g") or has(t, "#0g")
    rest = sorted([t for t in today_tasks if not placed(t)],
                  key=lambda t: _prank(t.get("priority")))
    ordered = rituals + neg1g + zeroneon + oneneon + zerog + critical + rest

    completed_ids = _completed_ids()
    snoozed_ids = _snoozed_ids()
    # Block labels (地支 glyph from /todo, 2026-07-27): hidden until that
    # block's hour arrives — mirrors the desktop dtd list generator.
    block_hours = {"卯": 4, "辰": 6, "巳": 8, "午": 10, "未": 12,
                   "申": 14, "酉": 16, "戌": 18, "亥": 20}
    now_hour = _dt.datetime.now().hour
    seen = set()
    out = []
    for t in ordered:
        tid = t.get("id")
        if not t.get("content") or tid in seen:
            continue
        seen.add(tid)
        if tid is not None and str(tid) in completed_ids:
            continue
        # Block-snoozed (ctrl-v in desktop dtd): hidden until the chosen
        # block's hour arrives — same file/semantics as terminal dtd.
        if tid is not None and str(tid) in snoozed_ids:
            continue
        blk = next((block_hours[l] for l in t.get("labels", []) if l in block_hours), None)
        if blk is not None and now_hour < blk:
            continue
        # Cross-machine "done today": a recurring daily habit whose due date has
        # advanced past today was completed today (each /close bumps it +1 day).
        # The 0neon/夜neon sections are bounded to due<=tomorrow (to survive a
        # drift), so these completed-and-advanced habits would otherwise linger.
        # completed-today.json is machine-local and is stale on this host when the
        # completion happened on the desktop (Straylight), so it can't hide them;
        # the Todoist due date carried in the cache is the durable signal.
        if t.get("recurring") and t.get("due") and t["due"] > today:
            continue
        raw = t["content"]
        display = t.get("short") or raw
        title = strip_ann(display) or raw
        est, pts = parse_est(raw)
        color, dom = color_of(t.get("labels", []), raw)
        out.append({
            "id": tid,
            "raw": raw,
            "title": title,
            "est": est,
            "points": pts,
            "color": color,
            "domain": dom,
            "recurring": bool(t.get("recurring")),
        })
    return out

# ---------------------------------------------------------------------------
# Complete via did-fast (real /did: closes Todoist + writes Neon)
# ---------------------------------------------------------------------------
def complete(content: str) -> dict:
    try:
        proc = subprocess.run(["/usr/bin/python3", str(DID_FAST), content],
                              capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "did-fast timeout"}
    out = proc.stdout.strip()
    data, brace = None, out.find("{")
    if brace >= 0:
        try:
            data = json.loads(out[brace:])
        except Exception:
            data = None
    closed = False
    step = None
    if data and data.get("results"):
        r0 = data["results"][0]
        step = r0.get("step")
        closed = bool((r0.get("todoist") or {}).get("closed"))
    return {
        "ok": proc.returncode == 0,
        "closed": closed,
        "step": step,
        "future_skipped": bool(data and data.get("future_skipped")) if data else False,
        "stderr_tail": proc.stderr.strip()[-300:],
    }

# ---------------------------------------------------------------------------
# Quick-add (+ button): same (N)/[N]/@tag syntax as /todo, parsed with plain
# regex — this is a bare Flask process with no LLM access, so unlike /todo's
# own inference of missing time/value/domain, omitted modifiers here just
# stay omitted rather than being guessed. Explicit only.
# ---------------------------------------------------------------------------
def parse_add_input(raw: str) -> tuple[str, list[str]]:
    """('description (N) [N]', ['tag', ...]) — tags stripped from content,
    (N)/[N] left in place (dtd's own parse_est reads them back out for
    display, same as every other task in the list)."""
    labels = _TAG.findall(raw)
    content = re.sub(r"  +", " ", _TAG.sub("", raw)).strip()
    return content, labels

def create_todoist_task(content: str, labels: list[str]) -> dict:
    """POST a new task: Inbox (no project_id), due today, priority 1 (=p4,
    Todoist's default/lowest — confirmed against a live task's raw API
    value, not assumed)."""
    try:
        tok = TODOIST_TOKEN_FILE.read_text().strip()
    except Exception:
        return {"ok": False, "error": "no Todoist token"}
    body = json.dumps({"content": content, "labels": labels,
                       "due_string": "today", "priority": 1}).encode()
    req = urllib.request.Request(
        "https://api.todoist.com/api/v1/tasks", data=body, method="POST",
        headers={"Authorization": "Bearer " + tok,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            json.loads(resp.read())  # validate it's a real task response
        return {"ok": True, "content": content}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------------------------------------------------------------------------
# Day totals for the header: points so far today + tasks completed today.
# Both derived from cross-machine-durable sources (Excel Σ on Ix + Todoist +
# the Todoist-due-date cache) rather than the machine-local completed-today.json,
# so a completion made on the desktop still counts here. MUST run on Ix (the
# Excel daemon is local and the cache reflects advanced habit due dates).
# ---------------------------------------------------------------------------
_SUMMARY = {"at": 0.0, "val": None}

def _todoist_completed_today() -> int:
    """Count of Todoist completions today. NOTE: recurring habits are absent —
    completing a recurring task reschedules it, so it never lands in the
    completed ledger. Those are added back from the cache in day_summary()."""
    try:
        tok = TODOIST_TOKEN_FILE.read_text().strip()
    except Exception:
        return 0
    if not tok:
        return 0
    today = _dt.date.today().isoformat()
    url = ("https://api.todoist.com/api/v1/tasks/completed/by_completion_date"
           "?since=%sT00:00:00&until=%sT23:59:59&limit=200" % (today, today))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=10) as r:
        return len(json.loads(r.read()).get("items", []))

_summary_lock = threading.Lock()

def _compute_summary() -> dict:
    """The actual (slow, Excel/Todoist-bound) computation — always runs to
    completion and updates _SUMMARY. Split out of day_summary() so force=True
    can call it directly (blocking, as before) while the stale/background
    case below can run it off-thread."""
    today = _dt.date.today()
    iso = today.isoformat()

    # points so far today = 0分 Σ (col D), the grand total for today's row.
    points = 0
    try:
        from neon import excel
        r = excel.read("0分", "D", date="%d/%d" % (today.month, today.day))
        if r.get("ok"):
            points = int(float(r.get("value") or 0))
    except Exception as e:
        print("WARN day_summary points:", e, file=sys.stderr)

    # tasks completed today = Todoist-completed (non-recurring tasks/rituals/goals)
    # + daily habits whose due date advanced past today (recurring completions
    # Todoist hides). Matches the felt count; independent of which machine did it.
    done = 0
    try:
        done += _todoist_completed_today()
    except Exception as e:
        print("WARN day_summary todoist:", e, file=sys.stderr)
    try:
        d = json.loads(CACHE.read_text())
        done += sum(1 for key in ("0neon", "夜neon") for t in d.get(key, [])
                    if isinstance(t, dict) and t.get("recurring")
                    and t.get("due") and t["due"] > iso)
    except Exception as e:
        print("WARN day_summary advanced:", e, file=sys.stderr)

    val = {"points": points, "done": done}
    _SUMMARY.update(at=time.time(), val=val)
    return val

def _compute_summary_async():
    try:
        _compute_summary()
    finally:
        _summary_lock.release()

def day_summary(force: bool = False) -> dict:
    """force=True: compute synchronously, as before (the caller is
    deliberately waiting on fresh data).

    force=False once the SUMMARY_MAX_AGE cache expires: never block the
    request on this either (2026-08-11, same bug class/fix as
    _refresh_cache_if_stale above). Confirmed live: even after fixing the
    Todoist refresh path, /api/tasks was STILL taking 15s+, because this
    function's excel.read() call blocks on the excel-http daemon on ix,
    which itself was taking ~15s just to return its own osascript_timeout
    error. A slow/wedged Excel daemon must not translate into a hung page
    load — recompute in the background (deduped, at most one in flight) and
    serve the last known value, or a zeroed fallback on a cold start with
    nothing computed yet (matches the existing graceful-degrade contract)."""
    now = time.time()
    fresh = _SUMMARY["val"] is not None and now - _SUMMARY["at"] < SUMMARY_MAX_AGE
    if not force and fresh:
        return _SUMMARY["val"]
    if force:
        return _compute_summary()
    if _summary_lock.acquire(blocking=False):
        threading.Thread(target=_compute_summary_async, daemon=True).start()
    return _SUMMARY["val"] or {"points": 0, "done": 0}

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)

@app.route("/api/tasks")
def api_tasks():
    try:
        force = request.args.get("refresh") == "1"
        return jsonify({"ok": True, "tasks": build_tasks(force_refresh=force),
                        "summary": day_summary(force=force)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/summary")
def api_summary():
    try:
        return jsonify({"ok": True, **day_summary(force=request.args.get("refresh") == "1")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/done", methods=["POST"])
def api_done():
    body = request.get_json(force=True, silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    res = complete(content)
    res["points"] = parse_est(content)[1]
    return jsonify(res)

@app.route("/api/add", methods=["POST"])
def api_add():
    body = request.get_json(force=True, silent=True) or {}
    raw = (body.get("content") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "no content"}), 400
    content, labels = parse_add_input(raw)
    if not content:
        return jsonify({"ok": False, "error": "no content"}), 400
    return jsonify(create_todoist_task(content, labels))

@app.route("/")
def index():
    return render_template_string(PAGE)

# ---------------------------------------------------------------------------
# Frontend — terminal-styled, single file, no external deps
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>dtd</title>
<style>
  :root { --bg:#1b1b1b; --dim:#777; --go:#00e676; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:#cfcfcf;
    font:15px/1.2 ui-monospace,"SF Mono",Menlo,Monaco,"Cascadia Mono",monospace;
    -webkit-font-smoothing:antialiased; }
  header { position:sticky; top:0; z-index:5;
    padding:calc(env(safe-area-inset-top) + 9px) 14px 8px;
    background:#1b1b1bee; backdrop-filter:blur(6px);
    display:flex; align-items:center; justify-content:space-between;
    border-bottom:1px solid #2a2a2a; }
  header .brand { font-weight:700; letter-spacing:1px; color:#cfcfcf; }
  header .brand b { color:var(--go); }
  .tally { color:var(--dim); font-variant-numeric:tabular-nums; }
  .tally b { color:var(--go); }
  #reload { background:none; border:1px solid #333; color:var(--dim);
    border-radius:6px; padding:3px 9px; font-family:inherit; font-size:15px; }
  main { padding:2px 0 calc(env(safe-area-inset-bottom) + 60px); }
  .row { position:relative; overflow:hidden; }
  .row .track { position:absolute; inset:0; background:var(--go); color:#003; font-weight:800;
    display:flex; align-items:center; padding-left:16px; opacity:0; }
  .row .track::before { content:"✓ done  +" ; white-space:pre; }
  .row .track .p { font-weight:800; }
  .line { position:relative; display:flex; align-items:center; gap:10px;
    padding:10px 14px; background:var(--bg); min-height:40px;
    transform:translateX(0); transition:transform .05s linear; will-change:transform;
    touch-action:pan-y; }
  .line.snap { transition:transform .22s cubic-bezier(.2,.7,.2,1); }
  .ttl { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .est { white-space:nowrap; font-variant-numeric:tabular-nums; opacity:.92; }
  .empty,.loading { text-align:center; color:var(--dim); padding:60px 20px; }
  .toast { position:fixed; left:50%; bottom:calc(env(safe-area-inset-bottom) + 20px);
    transform:translateX(-50%) translateY(16px); background:var(--go); color:#003;
    font-weight:700; padding:9px 18px; border-radius:8px; opacity:0; transition:.22s;
    z-index:20; font-family:inherit; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.err { background:#ff4081; color:#2a0010; }
  #fab { position:fixed; right:calc(env(safe-area-inset-right) + 18px);
    bottom:calc(env(safe-area-inset-bottom) + 22px); width:52px; height:52px;
    border-radius:50%; background:var(--go); color:#003; border:none;
    font:400 26px/52px ui-monospace,monospace; text-align:center; padding:0;
    box-shadow:0 3px 10px #0007; z-index:15; }
  #fab:active { transform:scale(.94); }
  #addWrap { position:fixed; inset:0; background:#000a; z-index:25;
    display:flex; align-items:flex-end; opacity:0; pointer-events:none;
    transition:opacity .18s; }
  #addWrap.show { opacity:1; pointer-events:auto; }
  #addSheet { width:100%; background:#232323; border-top:1px solid #333;
    padding:16px 14px calc(env(safe-area-inset-bottom) + 16px);
    transform:translateY(12px); transition:transform .18s; }
  #addWrap.show #addSheet { transform:translateY(0); }
  #addInput { width:100%; background:#1b1b1b; color:#cfcfcf; border:1px solid #3a3a3a;
    border-radius:8px; padding:11px 12px; font:15px/1.3 inherit; }
  #addInput:focus { outline:none; border-color:var(--go); }
  #addRow { display:flex; gap:10px; margin-top:10px; }
  #addRow button { flex:1; padding:10px; border-radius:8px; border:1px solid #3a3a3a;
    background:#1b1b1b; color:#cfcfcf; font:700 15px inherit; }
  #addRow button.go { background:var(--go); color:#003; border-color:var(--go); }
</style>
</head>
<body>
<header>
  <div class="brand">d<b>t</b>d</div>
  <div class="tally"><b id="tot">0</b> 分 · <span id="cnt">0</span> done</div>
  <button id="reload">↻</button>
</header>
<main id="list"><div class="loading">loading…</div></main>
<button id="fab">+</button>
<div id="addWrap">
  <div id="addSheet">
    <input id="addInput" placeholder="task (10) [5] @tag" autocomplete="off" autocapitalize="off">
    <div id="addRow">
      <button id="addCancel">cancel</button>
      <button id="addGo" class="go">add</button>
    </div>
  </div>
</div>
<div class="toast" id="toast"></div>
<script>
const list = document.getElementById('list');
const toastEl = document.getElementById('toast');
let total = 0, count = 0;

function toast(msg, err){
  toastEl.textContent = msg;
  toastEl.classList.toggle('err', !!err);
  toastEl.classList.add('show');
  clearTimeout(toastEl._t);
  toastEl._t = setTimeout(()=>toastEl.classList.remove('show'), 1500);
}

function setTally(p, c){
  total = p||0; count = c||0;
  document.getElementById('tot').textContent = total;
  document.getElementById('cnt').textContent = count;
}

async function load(refresh){
  list.innerHTML = '<div class="loading">loading…</div>';
  try {
    const r = await fetch('/api/tasks' + (refresh?'?refresh=1':''));
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fetch failed');
    render(d.tasks);
    if(d.summary) setTally(d.summary.points, d.summary.done);
  } catch(e){ list.innerHTML = '<div class="empty">⚠ '+e.message+'</div>'; }
}

function render(tasks){
  if(!tasks.length){ list.innerHTML = '<div class="empty">🎉 nothing left for today</div>'; return; }
  list.innerHTML = '';
  for(const t of tasks) list.appendChild(makeRow(t));
}

function makeRow(t){
  const row = document.createElement('div');
  row.className = 'row';
  const track = document.createElement('div');
  track.className = 'track';
  track.innerHTML = '<span class="p">'+(t.points||0)+' 分</span>';
  row.appendChild(track);

  const line = document.createElement('div');
  line.className = 'line';
  line.style.color = t.color;
  const ttl = document.createElement('span');
  ttl.className = 'ttl';
  ttl.textContent = (t.recurring?'↻ ':'') + t.title;
  const est = document.createElement('span');
  est.className = 'est';
  est.textContent = t.est || '';
  line.appendChild(ttl); line.appendChild(est);
  row.appendChild(line);

  bindSwipe(row, line, track, t);
  return row;
}

function bindSwipe(row, line, track, t){
  let x0=null, dx=0, dragging=false;
  const W = () => row.offsetWidth;
  const start = x=>{ x0=x; dx=0; dragging=true; line.classList.remove('snap'); };
  const move = x=>{
    if(!dragging) return;
    dx = Math.max(0, x - x0);
    line.style.transform = 'translateX('+dx+'px)';
    track.style.opacity = Math.min(1, dx/(W()*0.4));
  };
  const end = ()=>{
    if(!dragging) return; dragging=false;
    line.classList.add('snap');
    if(dx > W()*0.42){ fly(row, line, t); }
    else { line.style.transform='translateX(0)'; track.style.opacity=0; }
  };
  line.addEventListener('touchstart', e=>start(e.touches[0].clientX), {passive:true});
  line.addEventListener('touchmove',  e=>move(e.touches[0].clientX),  {passive:true});
  line.addEventListener('touchend', end);
  line.addEventListener('mousedown', e=>{start(e.clientX);
    const mm=ev=>move(ev.clientX), mu=()=>{end();
      document.removeEventListener('mousemove',mm);document.removeEventListener('mouseup',mu);};
    document.addEventListener('mousemove',mm); document.addEventListener('mouseup',mu);});
}

function fly(row, line, t){
  line.style.transform = 'translateX('+(row.offsetWidth+40)+'px)';
  setTimeout(()=>{ row.style.height=row.offsetHeight+'px';
    requestAnimationFrame(()=>{ row.style.transition='.2s'; row.style.height='0'; row.style.opacity='0'; });
    setTimeout(()=>{ row.remove();
      if(!document.querySelector('.row')) list.innerHTML='<div class="empty">🎉 nothing left for today</div>';
    }, 210);
  }, 170);
  commit(t);
}

async function commit(t){
  total += (t.points||0); count += 1;
  document.getElementById('tot').textContent = total;
  document.getElementById('cnt').textContent = count;
  try {
    const r = await fetch('/api/done', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:t.id, content:t.raw})});
    const d = await r.json();
    if(!d.ok){ toast('saved · Neon write failed', true); return; }
    if(d.closed) toast('+'+(t.points||0)+' 分 ✓');
    else if(d.future_skipped) toast('+'+(t.points||0)+' 分 · recurring');
    else toast('+'+(t.points||0)+' 分');
  } catch(e){ toast('offline · not synced', true); }
}

document.getElementById('reload').onclick = ()=> load(true);

const addWrap = document.getElementById('addWrap');
const addInput = document.getElementById('addInput');

function openAdd(){
  addWrap.classList.add('show');
  addInput.value = '';
  setTimeout(()=>addInput.focus(), 50);
}
function closeAdd(){ addWrap.classList.remove('show'); addInput.blur(); }

document.getElementById('fab').onclick = openAdd;
document.getElementById('addCancel').onclick = closeAdd;
addWrap.addEventListener('click', e=>{ if(e.target===addWrap) closeAdd(); });
addInput.addEventListener('keydown', e=>{
  if(e.key==='Enter') submitAdd();
  if(e.key==='Escape') closeAdd();
});
document.getElementById('addGo').onclick = submitAdd;

async function submitAdd(){
  const content = addInput.value.trim();
  if(!content) return;
  closeAdd();
  try {
    const r = await fetch('/api/add', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({content})});
    const d = await r.json();
    if(!d.ok){ toast(d.error||'add failed', true); return; }
    toast('+ added');
    load(true);
  } catch(e){ toast('offline · not added', true); }
}

load(false);
</script>
</body>
</html>"""

if __name__ == "__main__":
    # threaded=True: Werkzeug's dev server is single-threaded by default, so
    # a single slow request (a force=True refresh, or /api/done's did-fast
    # subprocess) blocked every OTHER client's request behind it too —
    # contributing to "dtd web hangs on loading" across tabs/devices even
    # after the staleness-triggered refresh above stopped blocking on its own.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
