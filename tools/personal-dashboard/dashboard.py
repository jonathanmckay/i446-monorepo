#!/usr/bin/env python3
"""
Personal Dashboard — jm
Points/day and time/day stacked bar charts + AI turns/day
Run: python3 dashboard.py
Then open: http://localhost:5558
"""

import base64
import json
import os
import subprocess
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path

import openpyxl
from flask import Flask, render_template_string, jsonify

# Load .env file if present (for local dev without exporting vars)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

app = Flask(__name__)

LOCAL_TZ = ZoneInfo("America/Los_Angeles")
NEON_PATH = Path.home() / "OneDrive" / "vault-excel" / "Neon-current.xlsx"
EMAIL_GIST_ID = "7c08fd1a83c8f3bbab3917bdb3d33df1"

# ── Column mappings ────────────────────────────────────────────────────────────
# 0分 sheet: column index (1-based) → label + domain
POINTS_COLS = {
    25: {"label": "-1₦", "domain": None,    "color": "#9e9e9e"},  # Y
    26: {"label": "0₲",  "domain": None,    "color": "#0a0a0a"},  # Z — Abyss
    27: {"label": "i9",  "domain": "i9",    "color": "#2979ff"},  # AA
    28: {"label": "m5",  "domain": "m5x2",  "color": "#d50032"},  # AB
    29: {"label": "个",  "domain": "g245",  "color": "#00e676"},  # AC
    30: {"label": "媒",  "domain": "hcmc",  "color": "#0d3b66"},  # AD
    31: {"label": "思",  "domain": None,    "color": "#7c4dff"},  # AE
    32: {"label": "hcb", "domain": "hcb",   "color": "#f81d78"},  # AF
    33: {"label": "xk",  "domain": "xk87",  "color": "#fd6c1d"},  # AG
    34: {"label": "社",  "domain": "s897",  "color": "#1b5e20"},  # AH
}

# Toggl project code → color (neon palette)
PROJECT_COLORS = {
    "g245":  "#00e676",  # Matrix
    "h335":  "#00bfa5",  # Miami Vice
    "hcb":   "#f81d78",  # Bubblegum Shock
    "hcbp":  "#ff4081",  # Flamingo
    "hcm":   "#470bf6",  # Ultraviolet
    "hcmc":  "#0d3b66",  # Deep Sea
    "hcmc2": "#ffd600",  # Lightning
    "hci":   "#63ede0",  # Vaporwave
    "i444":  "#616161",  # Graphite
    "i447":  "#303030",  # Shadow
    "i9":    "#2979ff",  # Electric Blue
    "infra": "#9e9e9e",  # Concrete
    "m5x2":  "#d50032",  # Crimson
    "m828":  "#1249b4",  # Sapphire
    "n156":  "#1249b4",  # Sapphire
    "q5n7":  "#c3fc0d",  # Radioactive
    "qz12":  "#aa00ff",  # Purple Haze
    "s897":  "#1b5e20",  # Emerald Shadow
    "xk87":  "#fd6c1d",  # Tangerine Dream
    "xk88":  "#e65100",  # Molten
    "epcn":  "#00bfa5",  # Miami Vice
    "家":    "#fd6c1d",  # same as xk87
    "睡觉":  "#303030",  # Shadow
    "no project": "#424242",
}

PROJECT_ID_TO_CODE = {
    108537163: "g245", 153212856: "h335", 154064792: "hcb", 108359995: "hcm",
    109932707: "hcmc", 109216950: "hci", 158134455: "i447", 209635316: "i9",
    108359987: "m5x2", 112310620: "m828", 152057340: "qz12", 109719141: "s897",
    163129781: "xk87", 108433670: "xk88", 108547409: "家", 108358083: "睡觉",
    150114323: "epcn", 120844877: "infra", 108357451: "n156", 174372636: "q5n7",
    185952786: "i444", 160959920: "h5c7", 45122191: "f8", 108360024: "hcbp",
    108359992: "hcmc2",
}

DAYS = 30


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_points_data():
    """Read 0分 sheet, return {date_str: {label: value}} for last DAYS days."""
    today = date.today()
    cutoff = today - timedelta(days=DAYS)

    wb = openpyxl.load_workbook(str(NEON_PATH), data_only=True, read_only=True)
    ws = wb["0分"]

    result = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        b = row[1]  # column B (index 1)
        if b is None:
            continue
        if isinstance(b, datetime):
            d = b.date()
        else:
            continue
        if d <= cutoff or d > today:
            continue

        day_str = d.isoformat()
        day_data = {}
        for col_idx, meta in POINTS_COLS.items():
            val = row[col_idx - 1]  # 0-based
            if val is not None and isinstance(val, (int, float)) and val > 0:
                day_data[meta["label"]] = int(round(float(val)))
        if day_data:
            result[day_str] = day_data

    wb.close()
    return result


def load_toggl_data():
    """Fetch Toggl entries for last DAYS days, return {date_str: {project: minutes}}."""
    api_key = os.environ.get("TOGGL_API_KEY", "")
    if not api_key:
        return {}

    today = date.today()
    start = (today - timedelta(days=DAYS)).isoformat()
    end = (today + timedelta(days=1)).isoformat()

    url = f"https://api.track.toggl.com/api/v9/me/time_entries?start_date={start}&end_date={end}"
    creds = base64.b64encode(f"{api_key}:api_token".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            entries = json.loads(resp.read())
    except Exception:
        return {}

    result = defaultdict(lambda: defaultdict(int))
    for e in entries:
        dur = e.get("duration", 0)
        if dur <= 0:
            continue
        start_str = e.get("start", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.fromisoformat(start_str).astimezone(LOCAL_TZ)
        except (ValueError, TypeError):
            continue
        d = start_dt.date()
        if d <= (today - timedelta(days=DAYS)) or d > today:
            continue

        proj_id = e.get("project_id")
        code = PROJECT_ID_TO_CODE.get(proj_id, "no project") if proj_id else "no project"
        result[d.isoformat()][code] += dur // 60

    return {k: dict(v) for k, v in result.items()}


def load_tasks_data():
    """Fetch completed tasks from Todoist, split by category tag in content.
    Returns {date_str: {"neon": count, "posthoc": count, "one_n": count, "other": count, "total": count}}.
    Categories (detected via @tag in content):
      @0neon  → neon habits (black)
      @posthoc → retroactive log (purple)
      @1neon  → 1n weekly tasks (green)
      other   → regular tasks (blue)
    """
    token = "7eb82f47aba8b334769351368e4e3e3284f980e5"
    today = date.today()
    since = (today - timedelta(days=DAYS)).strftime("%Y-%m-%dT00:00:00Z")

    neon = defaultdict(int)
    posthoc = defaultdict(int)
    one_n = defaultdict(int)
    other = defaultdict(int)
    cursor = None

    while True:
        url = f"https://api.todoist.com/api/v1/tasks/completed?since={since}&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception:
            break

        for item in data.get("items", []):
            completed_at = item.get("completed_at", "")
            if not completed_at:
                continue
            try:
                ts = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                d = ts.astimezone(LOCAL_TZ).date()
                if d <= (today - timedelta(days=DAYS)) or d > today:
                    continue
                day_str = d.isoformat()
                content = item.get("content", "")
                if "@0neon" in content:
                    neon[day_str] += 1
                elif "@posthoc" in content:
                    posthoc[day_str] += 1
                elif "@1neon" in content:
                    one_n[day_str] += 1
                else:
                    other[day_str] += 1
            except (ValueError, TypeError):
                continue

        cursor = data.get("next_cursor")
        if not cursor:
            break

    all_days = set(neon) | set(posthoc) | set(one_n) | set(other)
    return {d: {
        "neon": neon[d], "posthoc": posthoc[d], "one_n": one_n[d], "other": other[d],
        "total": neon[d] + posthoc[d] + one_n[d] + other[d],
    } for d in all_days}


def load_turns_data():
    """Fetch pre-computed daily turns from ai-dashboard API (localhost:5555/api/turns).
    Falls back to empty if ai-dashboard is not running."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:5555/api/turns", timeout=5) as resp:
            entries = json.loads(resp.read())
        return {e["date"]: e["total"] for e in entries if e.get("date")}
    except Exception:
        return {}


def load_imessage_stats():
    """Load iMessage stats from response DB + live chat.db for today/yesterday."""
    import sqlite3

    stats = {}
    response_db = Path.home() / "vault" / "i447" / "i446" / "imsg-responses.db"
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    # Live counts from chat.db
    chatdb = Path.home() / "Library" / "Messages" / "chat.db"
    if chatdb.exists():
        try:
            conn = sqlite3.connect(f"file:{chatdb}?mode=ro", uri=True)
            apple_epoch = 978307200
            rows = conn.execute("""
                SELECT
                    date(m.date / 1000000000 + ?, 'unixepoch', 'localtime') as day,
                    m.is_from_me,
                    COUNT(*) as cnt
                FROM message m
                WHERE m.date / 1000000000 + ? >= strftime('%s', ?, 'utc') - 86400*2
                  AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL)
                  AND m.associated_message_type = 0
                GROUP BY day, m.is_from_me
            """, (apple_epoch, apple_epoch, yesterday_str)).fetchall()
            conn.close()

            for day, is_from_me, cnt in rows:
                if day == today_str:
                    key = "today"
                elif day == yesterday_str:
                    key = "yesterday"
                else:
                    continue
                if key not in stats:
                    stats[key] = {"sent": 0, "received": 0, "responses": 0, "avg_hours": None}
                if is_from_me:
                    stats[key]["sent"] = cnt
                else:
                    stats[key]["received"] = cnt
        except Exception:
            pass

    # Response pair stats from imsg-responses.db
    if response_db.exists():
        try:
            conn = sqlite3.connect(f"file:{response_db}?mode=ro", uri=True)
            for day_str, key in [(today_str, "today"), (yesterday_str, "yesterday")]:
                row = conn.execute(
                    "SELECT response_count, avg_response_hours FROM daily_stats WHERE day = ?",
                    (day_str,)
                ).fetchone()
                if row and key in stats:
                    stats[key]["responses"] = row[0] or 0
                    stats[key]["avg_hours"] = round(row[1], 1) if row[1] else None
            conn.close()
        except Exception:
            pass

    return stats


def load_email_data():
    """Fetch email response time stats from GitHub Gist.
    Returns {"daily": [{date, account, avg_hours, count}], "summary": {...}}.
    """
    try:
        token = subprocess.check_output(
            ["gh", "auth", "token"], text=True, timeout=5
        ).strip()
    except Exception:
        token = ""

    url = f"https://api.github.com/gists/{EMAIL_GIST_ID}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            gist = json.loads(resp.read())
        # Gist has one file; grab its content
        file_content = next(iter(gist["files"].values()))["content"]
        return json.loads(file_content)
    except Exception:
        return {"daily": [], "summary": {}}


# ── Date range helper ──────────────────────────────────────────────────────────

def last_n_days(n=DAYS):
    today = date.today()
    return [(today - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/favicon.png")
def favicon():
    from PIL import Image, ImageDraw
    import io
    from flask import send_file
    img = Image.new("RGB", (32, 32), (17, 17, 17))
    d = ImageDraw.Draw(img)
    d.rectangle([2, 16, 9, 31],  fill=(41, 121, 255))
    d.rectangle([2,  8, 9, 15],  fill=(253, 108, 29))
    d.rectangle([12, 22, 19, 31], fill=(41, 121, 255))
    d.rectangle([12, 12, 19, 21], fill=(253, 108, 29))
    d.rectangle([12,  4, 19, 11], fill=(213, 0, 50))
    d.rectangle([22, 22, 29, 31], fill=(41, 121, 255))
    d.rectangle([22, 14, 29, 21], fill=(253, 108, 29))
    d.rectangle([22, 10, 29, 13], fill=(213, 0, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/api/data")
def api_data():
    dates = last_n_days()

    points_raw = load_points_data()
    toggl_raw = load_toggl_data()
    turns_raw = load_turns_data()
    tasks_raw = load_tasks_data()
    email_raw = load_email_data()
    imsg_stats = load_imessage_stats()

    # Build sorted label lists
    all_point_labels = [m["label"] for m in POINTS_COLS.values()]
    point_colors = {m["label"]: m["color"] for m in POINTS_COLS.values()}

    # Points order: i9 bottom, xk, m5, then others
    POINTS_PRIORITY = ["i9", "xk", "m5"]
    def points_sort_key(label):
        if label in POINTS_PRIORITY:
            return (0, POINTS_PRIORITY.index(label))
        return (1, all_point_labels.index(label))
    ordered_point_labels = sorted(all_point_labels, key=points_sort_key)

    # Collect all project codes that appear in toggl data
    # Time order: i9 bottom, xk87, m5x2, others by volume, 睡觉 top
    TIME_PRIORITY = ["i9", "xk87", "m5x2"]
    all_project_codes = {code for day_data in toggl_raw.values() for code in day_data}
    def time_sort_key(code):
        if code in TIME_PRIORITY:
            return (0, TIME_PRIORITY.index(code))
        if code == "睡觉":
            return (2, 0)
        return (1, -sum(v.get(code, 0) for v in toggl_raw.values()))
    all_projects = sorted(all_project_codes, key=time_sort_key)

    # Build chart datasets
    points_datasets = []
    for label in ordered_point_labels:
        values = [points_raw.get(d, {}).get(label, 0) for d in dates]
        if any(v > 0 for v in values):
            points_datasets.append({
                "label": label,
                "data": values,
                "backgroundColor": point_colors.get(label, "#9e9e9e"),
            })

    time_datasets = []
    for code in all_projects:
        values = [int(toggl_raw.get(d, {}).get(code, 0)) for d in dates]
        if any(v > 0 for v in values):
            time_datasets.append({
                "label": code,
                "data": values,
                "backgroundColor": PROJECT_COLORS.get(code, "#424242"),
            })

    turns_values = [turns_raw.get(d, 0) for d in dates]
    tasks_neon    = [tasks_raw.get(d, {}).get("neon", 0)    for d in dates]
    tasks_posthoc = [tasks_raw.get(d, {}).get("posthoc", 0) for d in dates]
    tasks_1n      = [tasks_raw.get(d, {}).get("one_n", 0)   for d in dates]
    tasks_other   = [tasks_raw.get(d, {}).get("other", 0)   for d in dates]
    tasks_values  = [tasks_raw.get(d, {}).get("total", 0)   for d in dates]

    # shots/task = tasks completed / turns (None when either is 0)
    shots_per_task = []
    for t, tr in zip(tasks_values, turns_values):
        if t > 0 and tr > 0:
            shots_per_task.append(round(tr / t, 1))
        else:
            shots_per_task.append(None)

    # 分/min ratio datasets (7-day rolling, exclude days with <30 min tracked)
    RATIO_DOMAINS = [
        {"label": "xk",  "pts_col": "xk",  "time_codes": ["xk87", "xk88"], "color": "#fd6c1d"},
        {"label": "i9",  "pts_col": "i9",  "time_codes": ["i9"],            "color": "#2979ff"},
        {"label": "m5",  "pts_col": "m5",  "time_codes": ["m5x2"],          "color": "#d50032"},
    ]
    ratio_datasets = []
    for dom in RATIO_DOMAINS:
        raw = []
        for d in dates:
            pts = points_raw.get(d, {}).get(dom["pts_col"], 0)
            mins = sum(toggl_raw.get(d, {}).get(c, 0) for c in dom["time_codes"])
            raw.append((pts, mins))
        # 7-day rolling average ratio
        rolling = []
        for i in range(len(dates)):
            window = raw[max(0, i - 6): i + 1]
            total_pts = sum(p for p, _ in window)
            total_mins = sum(m for _, m in window)
            if total_mins >= 30:
                rolling.append(round(total_pts / total_mins, 2))
            else:
                rolling.append(None)
        ratio_datasets.append({
            "label": dom["label"],
            "data": rolling,
            "borderColor": dom["color"],
            "backgroundColor": "transparent",
            "borderWidth": 2,
            "pointRadius": 2,
            "tension": 0.3,
            "spanGaps": True,
        })

    # Email response time datasets — one line per account (response time) +
    # one bar per account (email count, secondary y-axis)
    email_daily = email_raw.get("daily", [])
    email_summary = email_raw.get("summary", {})
    # Build {account: {date: {avg_hours, count}}}
    email_by_account = defaultdict(dict)
    for entry in email_daily:
        acct = entry.get("account", "unknown")
        d = entry.get("date", "")
        if d:
            email_by_account[acct][d] = {
                "avg_hours": entry.get("avg_hours"),
                "count": entry.get("count", 0),
            }
    # Add iMessage daily stats from response DB
    import sqlite3 as _sq3
    _imsg_db = Path.home() / "vault" / "i447" / "i446" / "imsg-responses.db"
    if _imsg_db.exists():
        try:
            _conn = _sq3.connect(f"file:{_imsg_db}?mode=ro", uri=True)
            _rows = _conn.execute(
                "SELECT day, avg_response_hours, sent_count FROM daily_stats"
            ).fetchall()
            _conn.close()
            for day, avg_h, sent in _rows:
                if day in set(dates):
                    email_by_account["imessage"][day] = {
                        "avg_hours": avg_h,
                        "count": sent,
                    }
        except Exception:
            pass

    # Blended average response time (purple line) + per-account count bars
    EMAIL_BAR_COLORS = {
        "m5x2 gmail": "#d5003266", "m5x2": "#d5003266",
        "s897 gmail": "#1b5e2066", "personal": "#1b5e2066", "gmail": "#1b5e2066",
        "imessage": "#34c75966",
        "slack": "#9b002366",
        "outlook": "#00b8d466",
    }
    # Compute blended daily avg response time (minutes) across all accounts
    blended_response = []
    for d in dates:
        hours_list = []
        for acct, day_map in email_by_account.items():
            h = day_map.get(d, {}).get("avg_hours")
            if h is not None:
                hours_list.append(h)
        if hours_list:
            blended_response.append(round(sum(hours_list) / len(hours_list) * 60, 1))
        else:
            blended_response.append(None)

    email_datasets = []
    # Purple blended line
    email_datasets.append({
        "type": "line",
        "label": "avg response",
        "data": blended_response,
        "borderColor": "#aa00ff",
        "backgroundColor": "transparent",
        "borderWidth": 2,
        "pointRadius": 3,
        "tension": 0,
        "spanGaps": True,
        "yAxisID": "y",
    })
    # Per-account count bars (stacked) — ordered so m5x2+slack are adjacent
    EMAIL_BAR_ORDER = ["m5x2 gmail", "outlook", "slack", "imessage", "s897 gmail"]
    for acct in EMAIL_BAR_ORDER:
        day_map = email_by_account.get(acct, {})
        if not day_map:
            continue
        email_datasets.append({
            "type": "bar",
            "label": acct,
            "data": [day_map.get(d, {}).get("count", 0) for d in dates],
            "backgroundColor": EMAIL_BAR_COLORS.get(acct, "#aaaaaa44"),
            "borderWidth": 0,
            "yAxisID": "y2",
        })
    # Any accounts not in the explicit order
    for acct, day_map in sorted(email_by_account.items()):
        if acct not in EMAIL_BAR_ORDER:
            email_datasets.append({
                "type": "bar",
                "label": acct,
                "data": [day_map.get(d, {}).get("count", 0) for d in dates],
                "backgroundColor": EMAIL_BAR_COLORS.get(acct, "#aaaaaa44"),
                "borderWidth": 0,
                "yAxisID": "y2",
            })

    # Summary stats
    total_points = {label: sum(points_raw.get(d, {}).get(label, 0) for d in dates)
                    for label in all_point_labels}
    total_time = {code: sum(toggl_raw.get(d, {}).get(code, 0) for d in dates)
                  for code in all_projects}

    return jsonify({
        "dates": [d[5:] for d in dates],  # MM-DD for display
        "points": {"datasets": points_datasets},
        "time": {"datasets": time_datasets},
        "turns": turns_values,
        "tasks": tasks_values,
        "tasks_neon": tasks_neon,
        "tasks_posthoc": tasks_posthoc,
        "tasks_1n": tasks_1n,
        "tasks_other": tasks_other,
        "shots_per_task": shots_per_task,
        "ratio": {"datasets": ratio_datasets},
        "email": {"datasets": email_datasets, "summary": email_summary, "imessage": imsg_stats},
        "summary": {
            "total_points": {k: int(v) for k, v in total_points.items() if v > 0},
            "total_mins": {k: int(v) for k, v in total_time.items() if v > 0},
            "total_turns": sum(turns_values),
            "total_tasks": sum(tasks_values),
        }
    })


# ── HTML templates ─────────────────────────────────────────────────────────────

_SHARED_STYLE = """
<meta charset="utf-8">
<link rel="icon" type="image/png" href="/favicon.png">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {
  --bg: #111; --card: #1a1a1a; --text: #eee;
  --h1: #aaa; --h2: #666;
  --badge-bg: #222; --badge-text: #aaa;
  --tick: #555; --grid: #222;
  --nav: #333; --nav-text: #aaa;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f4f4f4; --card: #fff; --text: #111;
    --h1: #444; --h2: #888;
    --badge-bg: #eee; --badge-text: #555;
    --tick: #aaa; --grid: #e0e0e0;
    --nav: #ddd; --nav-text: #555;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'SF Mono', monospace; padding: 24px; }
.topbar { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 24px; }
h1 { font-size: 18px; color: var(--h1); letter-spacing: 2px; }
h2 { font-size: 13px; color: var(--h2); margin-bottom: 12px; letter-spacing: 1px; text-transform: uppercase; }
.nav-link { font-size: 12px; color: var(--nav-text); background: var(--nav); border-radius: 4px; padding: 4px 12px; text-decoration: none; letter-spacing: 1px; }
.nav-link:hover { opacity: 0.8; }
.grid { display: grid; grid-template-columns: 1fr; gap: 32px; margin-bottom: 32px; }
.card { background: var(--card); border-radius: 8px; padding: 20px; }
.chart-wrap { height: 280px; position: relative; max-width: 100%; }
.chart-wrap.sm { height: 200px; }
.chart-wrap.xs { height: 180px; }
.card { overflow: hidden; }
.summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.badge { background: var(--badge-bg); border-radius: 4px; padding: 4px 10px; font-size: 12px; }
.badge span { color: var(--badge-text); }
</style>
"""

_SHARED_JS_HEAD = """
const DARK = window.matchMedia('(prefers-color-scheme: dark)').matches;
const TICK = DARK ? '#555' : '#aaa';
const GRID = DARK ? '#222' : '#e0e0e0';

const CHART_DEFAULTS = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    x: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
    y: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } }
  }
};
"""

HTML = """<!DOCTYPE html>
<html>
<head>
<title>jm dashboard</title>
""" + _SHARED_STYLE + """
</head>
<body>
<div class="topbar">
  <h1>JM · PERSONAL DASHBOARD</h1>
  <a class="nav-link" href="/more">MORE →</a>
</div>
<div class="grid">
  <div class="card">
    <h2>Points / Day</h2>
    <div class="chart-wrap"><canvas id="pointsChart"></canvas></div>
    <div class="summary" id="pointsSummary"></div>
  </div>
  <div class="card">
    <h2>Time / Day</h2>
    <div class="chart-wrap"><canvas id="timeChart"></canvas></div>
    <div class="summary" id="timeSummary"></div>
  </div>
  <div class="card">
    <h2>Tasks Complete / Day</h2>
    <div class="chart-wrap xs"><canvas id="tasksChart"></canvas></div>
    <div class="summary" id="tasksSummary"></div>
  </div>
  <div class="card">
    <h2>Project Bocking — Comms Response Time</h2>
    <div class="chart-wrap sm"><canvas id="emailChart"></canvas></div>
    <div class="summary" id="emailSummary"></div>
  </div>
</div>

<script>
""" + _SHARED_JS_HEAD + """
fetch('/api/data').then(r => r.json()).then(data => {
  const labels = data.dates;

  // Points chart
  new Chart(document.getElementById('pointsChart'), {
    type: 'bar',
    data: { labels, datasets: data.points.datasets },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        ...CHART_DEFAULTS.scales,
        y: {
          ...CHART_DEFAULTS.scales.y,
          max: 2160,
          ticks: {
            ...CHART_DEFAULTS.scales.y.ticks,
            stepSize: 360,
            callback: v => [0,360,720,1080,1440,2160].includes(v) ? v : ''
          }
        }
      }
    }
  });

  // Time chart
  new Chart(document.getElementById('timeChart'), {
    type: 'bar',
    data: { labels, datasets: data.time.datasets },
    options: { ...CHART_DEFAULTS, scales: { ...CHART_DEFAULTS.scales, y: { ...CHART_DEFAULTS.scales.y, max: 1450 } } }
  });

  // Tasks chart (stacked: 0n, posthoc, 1n, other — neon palette)
  const taskSeries = [
    { label: '0₦', data: data.tasks_neon,    bg: '#0a0a0a' },
    { label: 'posthoc', data: data.tasks_posthoc, bg: '#7c4dff' },
    { label: '1₦', data: data.tasks_1n,      bg: '#00e676' },
    { label: 't779', data: data.tasks_other, bg: '#2979ff' },
  ];
  new Chart(document.getElementById('tasksChart'), {
    type: 'bar',
    data: { labels, datasets: taskSeries.map(s => ({
      label: s.label, data: s.data,
      backgroundColor: s.bg, borderColor: s.bg, borderWidth: 0,
    }))},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } }
      }
    }
  });
  // Tasks legend badges
  const tkEl = document.getElementById('tasksSummary');
  taskSeries.forEach(s => {
    const b = document.createElement('div');
    b.className = 'badge';
    b.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${s.bg};margin-right:4px;vertical-align:middle;"></span>${s.label}`;
    tkEl.appendChild(b);
  });

  // Email chart: blended response time line (purple) + per-account count bars (stacked)
  new Chart(document.getElementById('emailChart'), {
    type: 'bar',
    data: { labels, datasets: data.email.datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y:  { type: 'logarithmic', position: 'left', min: 10, max: 1440,
              ticks: { color: TICK, font: { size: 10 }, callback: v => [10,30,60,120,300,720,1440].includes(v) ? v+'m' : '' },
              afterBuildTicks: axis => { axis.ticks = [10,30,60,120,300,720,1440].map(v => ({value:v})); },
              grid: { color: GRID }, title: { display: true, text: 'min', color: TICK, font: { size: 10 } } },
        y2: { position: 'right', min: 0, stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { display: false }, title: { display: true, text: 'msgs', color: TICK, font: { size: 10 } } },
      }
    }
  });

  // Summaries
  const s = data.summary;
  // Build color maps from datasets
  const ptColors = {};
  (data.points.datasets || []).forEach(ds => { ptColors[ds.label] = ds.backgroundColor; });
  const tmColors = {};
  (data.time.datasets || []).forEach(ds => { tmColors[ds.label] = ds.backgroundColor; });

  const ptEl = document.getElementById('pointsSummary');
  Object.entries(s.total_points).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => {
    const b = document.createElement('div');
    b.className = 'badge';
    const dot = ptColors[k] ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${ptColors[k]};margin-right:4px;vertical-align:middle;"></span>` : '';
    b.innerHTML = `${dot}${k} <span>${v}</span>`;
    ptEl.appendChild(b);
  });
  const tmEl = document.getElementById('timeSummary');
  Object.entries(s.total_mins).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => {
    const b = document.createElement('div');
    b.className = 'badge';
    const dot = tmColors[k] ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${tmColors[k]};margin-right:4px;vertical-align:middle;"></span>` : '';
    b.innerHTML = `${dot}${k} <span>${v}m</span>`;
    tmEl.appendChild(b);
  });

  // Email legend (same style as Points/Day)
  const emEl = document.getElementById('emailSummary');
  const emLegend = [
    ['avg response', '#aa00ff', 'line'],
    ['m5x2 gmail', '#d50032', 'bar'],
    ['outlook', '#00b8d4', 'bar'],
    ['slack', '#9b0023', 'bar'],
    ['imessage', '#34c759', 'bar'],
    ['jbm gmail', '#1b5e20', 'bar'],
  ];
  emLegend.forEach(([label, color, type]) => {
    const b = document.createElement('div');
    b.className = 'badge';
    const shape = type === 'line'
      ? `<span style="display:inline-block;width:12px;height:2px;background:${color};margin-right:4px;vertical-align:middle;"></span>`
      : `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:4px;vertical-align:middle;"></span>`;
    b.innerHTML = `${shape}${label}`;
    emEl.appendChild(b);
  });
});
</script>
</body>
</html>"""


MORE_HTML = """<!DOCTYPE html>
<html>
<head>
<title>jm dashboard · more</title>
""" + _SHARED_STYLE + """
</head>
<body>
<div class="topbar">
  <h1>JM · MORE</h1>
  <a class="nav-link" href="/">← MAIN</a>
</div>
<div class="grid">
  <div class="card">
    <h2>分 / min (7-day rolling) — xk · i9 · m5</h2>
    <div class="chart-wrap sm"><canvas id="ratioChart"></canvas></div>
  </div>
  <div class="card">
    <h2>AI Turns / Day</h2>
    <div class="chart-wrap xs"><canvas id="turnsChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Shots / Task (turns ÷ tasks, 7-day rolling)</h2>
    <div class="chart-wrap xs"><canvas id="shotsChart"></canvas></div>
  </div>
</div>

<script>
""" + _SHARED_JS_HEAD + """
fetch('/api/data').then(r => r.json()).then(data => {
  const labels = data.dates;

  // Ratio chart (line)
  new Chart(document.getElementById('ratioChart'), {
    type: 'line',
    data: { labels, datasets: data.ratio.datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, labels: { color: TICK, font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } }
      }
    }
  });

  // Turns chart (bar)
  new Chart(document.getElementById('turnsChart'), {
    type: 'bar',
    data: { labels, datasets: [{
      label: 'turns',
      data: data.turns,
      backgroundColor: '#2979ff44',
      borderColor: '#2979ff',
      borderWidth: 1,
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } }
      }
    }
  });

  // Shots/task chart (7-day rolling)
  const rolling7 = data.turns.map((_, i) => {
    const t7 = data.tasks.slice(Math.max(0, i - 6), i + 1).reduce((a, b) => a + b, 0);
    const tr7 = data.turns.slice(Math.max(0, i - 6), i + 1).reduce((a, b) => a + b, 0);
    return t7 > 0 ? Math.round(tr7 / t7 * 10) / 10 : null;
  });
  new Chart(document.getElementById('shotsChart'), {
    type: 'line',
    data: { labels, datasets: [{
      label: 'turns/task',
      data: rolling7,
      borderColor: '#aa00ff',
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 2,
      tension: 0.3,
      spanGaps: true,
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } }
      }
    }
  });
});
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/more")
def more():
    return render_template_string(MORE_HTML)


if __name__ == "__main__":
    app.run(port=5558, debug=False)
