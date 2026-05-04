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
import threading
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

import openpyxl
from flask import Flask, render_template_string, jsonify

# GA4 imports (optional — dashboard works without analytics)
try:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric, Dimension, OrderBy,
    )
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    GA4_AVAILABLE = True
except ImportError:
    GA4_AVAILABLE = False

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

EXPECTED_HOST = "ix"
_host_marker = Path.home() / ".claude" / ".host-name"
_current_host = _host_marker.read_text().strip() if _host_marker.exists() else "unknown"


@app.before_request
def _enforce_canonical_host():
    if _current_host == EXPECTED_HOST:
        return None
    return (
        f"""<!doctype html><html><head><title>Wrong Host</title>
<style>
  html,body{{margin:0;height:100%;background:#b00020;color:#fff;
    font:600 28px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;
    display:flex;align-items:center;justify-content:center;text-align:center}}
  .box{{padding:2em;max-width:640px}}
  code{{background:rgba(0,0,0,.25);padding:.1em .4em;border-radius:4px;font-size:.9em}}
</style></head><body><div class="box">
<div style="font-size:64px">⚠</div>
<div>This dashboard only serves canonical data from <code>{EXPECTED_HOST}</code>.</div>
<div style="margin-top:.6em;font-weight:400;font-size:.7em;opacity:.85">
You're on <code>{_current_host}</code> — bookmark <code>http://ix.local:5558</code> instead.
</div></div></body></html>""",
        503,
        {"Content-Type": "text/html; charset=utf-8"},
    )


LOCAL_TZ = ZoneInfo("America/Los_Angeles")
NEON_PATH = Path.home() / "OneDrive" / "vault-excel" / "Neon-current.xlsx"
EMAIL_GIST_ID = "7c08fd1a83c8f3bbab3917bdb3d33df1"

# ── Column mappings ────────────────────────────────────────────────────────────
# 0分 sheet: column index (1-based) → label + domain
POINTS_COLS = {
    16: {"label": "-1₦", "domain": None,    "color": "#9e9e9e"},  # P
    17: {"label": "0₲",  "domain": None,    "color": "#0a0a0a"},  # Q — Abyss
    18: {"label": "i9",  "domain": "i9",    "color": "#2979ff"},  # R
    19: {"label": "m5",  "domain": "m5x2",  "color": "#d50032"},  # S
    20: {"label": "个",  "domain": "g245",  "color": "#00e676"},  # T
    21: {"label": "媒",  "domain": "hcmc",  "color": "#0d3b66"},  # U
    22: {"label": "思",  "domain": None,    "color": "#7c4dff"},  # V
    23: {"label": "hcb", "domain": "hcb",   "color": "#f81d78"},  # W
    24: {"label": "xk",  "domain": "xk87",  "color": "#fd6c1d"},  # X
    25: {"label": "社",  "domain": "s897",  "color": "#1b5e20"},  # Y
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
    "家":    "#00bfa5",  # Miami Vice (teal)
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

# GA4 config
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
GA4_OAUTH_KEYS = Path(__file__).parent / "ga4-oauth.keys.json"
GA4_TOKENS = Path(__file__).parent / "ga4-tokens.json"
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_points_data():
    """Read 0分 sheet, return {date_str: {label: value}} for last DAYS days.

    Uses xlwings to read from the running Excel instance so cross-sheet
    formulas are fully evaluated (openpyxl data_only reads stale caches).
    Falls back to openpyxl if xlwings/Excel is unavailable.
    """
    today = date.today()
    cutoff = today - timedelta(days=DAYS)

    # Strategy: read from JSON cache if it's newer than the Excel file.
    # Fall back to xlwings if no cache or cache is stale.
    _pts_cache = Path(__file__).parent / ".points-cache.json"
    if _pts_cache.exists():
        try:
            cache_mtime = _pts_cache.stat().st_mtime
            excel_mtime = NEON_PATH.stat().st_mtime if NEON_PATH.exists() else 0
            if cache_mtime >= excel_mtime:
                cached = json.loads(_pts_cache.read_text())
                result = {d: v for d, v in cached.items()
                          if cutoff < date.fromisoformat(d) <= today}
                if result:
                    return result
        except Exception:
            pass

    # Fallback: xlwings (works when run interactively, may timeout from launchd)
    try:
        import xlwings as xw
        wb = xw.Book(str(NEON_PATH))
        ws = wb.sheets["0分"]
        last_row = ws.range("B2").end("down").row
        min_idx = min(POINTS_COLS)
        max_idx = max(POINTS_COLS)
        def _col_letter(idx):
            return chr(64 + idx) if idx <= 26 else "A" + chr(64 + idx - 26)
        first_col = _col_letter(min_idx)
        last_col = _col_letter(max_idx)
        b_vals = ws.range(f"B3:B{last_row}").value
        if not isinstance(b_vals, list):
            b_vals = [b_vals]
        block = ws.range(f"{first_col}3:{last_col}{last_row}").value
        if not isinstance(block, list):
            block = [[block]]
        elif block and not isinstance(block[0], list):
            if min_idx == max_idx:
                block = [[v] for v in block]
            else:
                block = [block]

        result = {}
        for i, b in enumerate(b_vals):
            if b is None:
                continue
            if isinstance(b, datetime):
                d = b.date()
            elif isinstance(b, date):
                d = b
            else:
                continue
            if d <= cutoff or d > today:
                continue
            day_str = d.isoformat()
            day_data = {}
            row_vals = block[i] if i < len(block) else []
            for col_idx, meta in POINTS_COLS.items():
                offset = col_idx - min_idx
                val = row_vals[offset] if offset < len(row_vals) else None
                if val is not None and isinstance(val, (int, float)) and val > 0:
                    day_data[meta["label"]] = int(round(float(val)))
            if day_data:
                result[day_str] = day_data
        if result:
            # Write cache for launchd fallback
            _pts_cache = Path(__file__).parent / ".points-cache.json"
            _pts_cache.write_text(json.dumps(result))
        return result
    except Exception:
        pass

    return {}


# Each card pulls a single cell from 0n. Headers live on row 369 (and row 1
# for xk88). We hardcode (col, row) per card since headers span two rows.
CACHE_CARDS = [
    {"label": "hcbp",   "col": "AB", "row": 371, "period": "Q2",   "color": "#f81d78"},
    {"label": "hcbc",   "col": "AF", "row": 371, "period": "Q2",   "color": "#ff4081"},
    {"label": "xk88",   "col": "AN", "row": 371, "period": "Q2",   "color": "#e65100"},
    {"label": "ص",      "col": "AP", "row": 375, "period": "2026", "color": "#9c27b0"},
    {"label": "o314",   "col": "AQ", "row": 375, "period": "2026", "color": "#7c4dff"},
    {"label": "冥想",   "col": "AR", "row": 375, "period": "2026", "color": "#aa00ff"},
    {"label": "其他人", "col": "AS", "row": 375, "period": "2026", "color": "#fd6c1d"},
]


def load_cache_data():
    """Read each configured cell from 0n.

    Uses xlwings (live Excel) so the values are current; openpyxl can't read
    OneDrive paths from launchd-spawned processes (no Full Disk Access).
    Returns {label: value or None}.
    """
    result = {card["label"]: None for card in CACHE_CARDS}
    try:
        import xlwings as xw
        wb = xw.Book(str(NEON_PATH))
        ws = wb.sheets["0n"]
        for card in CACHE_CARDS:
            v = ws.range(f"{card['col']}{card['row']}").value
            if isinstance(v, (int, float)):
                result[card["label"]] = round(float(v), 1)
    except Exception:
        pass
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


_TASKS_CACHE_PATH = Path(__file__).parent / ".tasks-cache.json"
# Always refetch today + yesterday (late-evening completions can shift buckets).
# Days older than this are immutable — read from disk cache only.
_TASKS_REFETCH_TAIL = 2


def _fetch_tasks_for_day(day, token):
    """Fetch one day's completed-task counts from Todoist. Returns (date_str, counts) or (date_str, None)."""
    since_dt = datetime.combine(day, datetime.min.time(), tzinfo=PACIFIC).astimezone(timezone.utc)
    until_dt = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=PACIFIC).astimezone(timezone.utc)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    until = until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"https://api.todoist.com/api/v1/tasks/completed?since={since}&until={until}&limit=200"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    counts = {"neon": 0, "posthoc": 0, "one_n": 0, "other": 0}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return day.isoformat(), None
    for item in data.get("items", []):
        content = item.get("content", "")
        if "@0neon" in content:
            counts["neon"] += 1
        elif "@posthoc" in content:
            counts["posthoc"] += 1
        elif "@1neon" in content:
            counts["one_n"] += 1
        else:
            counts["other"] += 1
    counts["total"] = counts["neon"] + counts["posthoc"] + counts["one_n"] + counts["other"]
    return day.isoformat(), counts


def load_tasks_data():
    """Fetch completed tasks from Todoist, split by category tag in content.
    Returns {date_str: {"neon", "posthoc", "one_n", "other", "total"}}.

    Performance:
    - Historical days (older than today/yesterday) are read from a disk cache
      and never refetched (they're immutable).
    - Today + yesterday are always refetched in parallel.
    - Cold cache: parallel fetch of all DAYS+1 days.
    """
    token = "7eb82f47aba8b334769351368e4e3e3284f980e5"
    today = date.today()
    all_days = [today - timedelta(days=n) for n in range(DAYS, -1, -1)]
    refresh_cutoff = today - timedelta(days=_TASKS_REFETCH_TAIL - 1)

    # Load disk cache
    cache = {}
    if _TASKS_CACHE_PATH.exists():
        try:
            cache = json.loads(_TASKS_CACHE_PATH.read_text())
        except Exception:
            cache = {}

    # Decide which days need a network fetch
    to_fetch = [d for d in all_days
                if d >= refresh_cutoff or d.isoformat() not in cache]

    # Parallel fetch — Todoist has no published rate limit issue at this scale,
    # 10 workers gives ~4s for 31 cold days vs 41s serial.
    if to_fetch:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(_fetch_tasks_for_day, d, token) for d in to_fetch]
            for fut in futures:
                day_str, counts = fut.result()
                if counts is not None:
                    cache[day_str] = counts

        # Persist (best-effort; cache is just a perf win, not a correctness req)
        try:
            _TASKS_CACHE_PATH.write_text(json.dumps(cache))
        except Exception:
            pass

    # Build return dict from cache (only days in our window)
    wanted = {d.isoformat() for d in all_days}
    return {d: counts for d, counts in cache.items() if d in wanted}


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


def _ga4_credentials():
    """Load or refresh GA4 OAuth credentials."""
    if not GA4_AVAILABLE or not GA4_OAUTH_KEYS.exists():
        return None
    creds = None
    if GA4_TOKENS.exists():
        info = json.loads(GA4_TOKENS.read_text())
        creds = Credentials.from_authorized_user_info(info, GA4_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
        GA4_TOKENS.write_text(creds.to_json())
    if not creds or not creds.valid:
        return None
    return creds


def load_ga4_data():
    """Fetch GA4 pageviews/day and top pages for last DAYS days.
    Returns {"daily": {date_str: pageviews}, "top_pages": [{path, views}]}.
    """
    creds = _ga4_credentials()
    if not creds or not GA4_PROPERTY_ID:
        return {"daily": {}, "top_pages": []}

    try:
        client = BetaAnalyticsDataClient(credentials=creds)

        # Daily pageviews
        today = date.today()
        start = (today - timedelta(days=DAYS)).isoformat()
        end = today.isoformat()

        resp = client.run_report(RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date=start, end_date=end)],
            metrics=[Metric(name="screenPageViews")],
            dimensions=[Dimension(name="date")],
        ))
        daily = {}
        for row in resp.rows:
            d = row.dimension_values[0].value  # YYYYMMDD
            d_iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            daily[d_iso] = int(row.metric_values[0].value)

        # Top pages
        resp2 = client.run_report(RunReportRequest(
            property=f"properties/{GA4_PROPERTY_ID}",
            date_ranges=[DateRange(start_date=start, end_date=end)],
            metrics=[Metric(name="screenPageViews")],
            dimensions=[Dimension(name="pagePath")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
            limit=10,
        ))
        top_pages = []
        for row in resp2.rows:
            top_pages.append({
                "path": row.dimension_values[0].value,
                "views": int(row.metric_values[0].value),
            })

        return {"daily": daily, "top_pages": top_pages}
    except Exception as e:
        print(f"GA4 error: {e}")
        return {"daily": {}, "top_pages": []}


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
    cached = _api_data_cached()
    return jsonify(cached)


@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    """Invalidate the data cache so the next /api/data fetch is fresh."""
    with _API_DATA_LOCK:
        _API_DATA_CACHE["payload"] = None
        _API_DATA_CACHE["ts"] = 0.0
    return jsonify({"status": "ok"})


# 60s TTL cache for /api/data — avoids re-fetching everything on refresh-spam.
_API_DATA_CACHE: dict = {"payload": None, "ts": 0.0}
_API_DATA_LOCK = threading.Lock()
_API_DATA_TTL = 300.0


def _api_data_cached():
    now = time.time()
    with _API_DATA_LOCK:
        if _API_DATA_CACHE["payload"] is not None and (now - _API_DATA_CACHE["ts"]) < _API_DATA_TTL:
            return _API_DATA_CACHE["payload"]
    payload = _build_api_data()
    with _API_DATA_LOCK:
        _API_DATA_CACHE["payload"] = payload
        _API_DATA_CACHE["ts"] = time.time()
    return payload


def _build_api_data():
    dates = last_n_days()

    # xlwings uses AppleScript and cannot run in a background thread.
    # Run it in the main thread first, then parallelize the rest.
    try:
        points_raw = load_points_data()
    except Exception:
        points_raw = {}

    try:
        cache_raw = load_cache_data()
    except Exception:
        cache_raw = {card["label"]: None for card in CACHE_CARDS}

    loaders = {
        "toggl": load_toggl_data,
        "turns": load_turns_data,
        "tasks": load_tasks_data,
        "email": load_email_data,
        "imsg": load_imessage_stats,
        "ga4": load_ga4_data,
    }
    raw = {}
    with ThreadPoolExecutor(max_workers=len(loaders)) as ex:
        futures = {name: ex.submit(fn) for name, fn in loaders.items()}
        for name, fut in futures.items():
            try:
                raw[name] = fut.result()
            except Exception:
                raw[name] = {} if name != "imsg" else None
    toggl_raw = raw["toggl"]
    turns_raw = raw["turns"]
    tasks_raw = raw["tasks"]
    email_raw = raw["email"]
    imsg_stats = raw["imsg"]
    ga4_raw = raw["ga4"]

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
    # Build {account: {date: {avg_hours, count, sent_count}}}
    email_by_account = defaultdict(dict)
    for entry in email_daily:
        acct = entry.get("account", "unknown")
        d = entry.get("date", "")
        if d:
            count = entry.get("count", 0)
            email_by_account[acct][d] = {
                "avg_hours": entry.get("avg_hours"),
                "avg_hours_daytime": entry.get("avg_hours_daytime"),
                "count": count,
                "count_daytime": entry.get("count_daytime", count),
                "sent_count": entry.get("sent_count", count),
            }
    # Add iMessage daily stats from response DB
    import sqlite3 as _sq3
    _imsg_db = Path.home() / "vault" / "i447" / "i446" / "imsg-responses.db"
    if _imsg_db.exists():
        try:
            _conn = _sq3.connect(f"file:{_imsg_db}?mode=ro", uri=True)
            _rows = _conn.execute(
                "SELECT day, median_response_hours, response_count, sent_count, "
                "median_response_hours_daytime, response_count_daytime "
                "FROM daily_stats"
            ).fetchall()
            _conn.close()
            for day, median_h, resp_count, sent, median_h_dt, resp_count_dt in _rows:
                if day in set(dates):
                    email_by_account["imessage"][day] = {
                        "avg_hours": median_h,
                        "avg_hours_daytime": median_h_dt,
                        "count": resp_count or 0,
                        "count_daytime": resp_count_dt or 0,
                        "sent_count": sent,
                    }
        except Exception:
            pass

    # Overlay archive_log.db (unified source of truth, shared with /inbound idle screen).
    # For each day in the chart window, replace per-type counts with archive_log data
    # so the dashboard matches what /inbound shows.
    _archive_db = Path.home() / ".config" / "ibx" / "archive_log.db"
    if _archive_db.exists():
        try:
            _aconn = _sq3.connect(f"file:{_archive_db}?mode=ro", uri=True)
            _TYPE_TO_ACCT = {
                "email": "m5x2 gmail",
                "outlook": "outlook",
                "teams": "teams",
                "slack": "slack",
                "imsg": "imessage",
            }
            for d in dates:
                day_start = datetime.strptime(d, "%Y-%m-%d").timestamp()
                day_end = day_start + 86400
                rows = _aconn.execute(
                    "SELECT message_type, COUNT(DISTINCT item_uid), AVG(response_min) "
                    "FROM archive_log WHERE timestamp >= ? AND timestamp < ? "
                    "AND action = 'reply' AND response_min IS NOT NULL "
                    "GROUP BY message_type",
                    (day_start, day_end),
                ).fetchall()
                for msg_type, count, avg_min in rows:
                    acct = _TYPE_TO_ACCT.get(msg_type)
                    if not acct or count == 0:
                        continue
                    avg_h = avg_min / 60.0 if avg_min else None
                    existing = email_by_account[acct].get(d, {})
                    # Only override if archive_log has MORE replies (it's more complete)
                    if count >= existing.get("count", 0):
                        email_by_account[acct][d] = {
                            "avg_hours": avg_h,
                            "avg_hours_daytime": avg_h,
                            "count": count,
                            "count_daytime": count,
                            "sent_count": count,
                        }
            _aconn.close()
        except Exception:
            pass

    # Blended average response time (purple line) + per-account count bars
    EMAIL_BAR_COLORS = {
        "m5x2 gmail": "#d5003266", "m5x2": "#d5003266",
        "s897 gmail": "#1b5e2066", "personal": "#1b5e2066", "gmail": "#1b5e2066",
        "imessage": "#34c75966",
        "slack": "#9b002366",
        "outlook": "#00b8d466",
        "teams": "#1249b466",
    }
    # Compute blended daily avg response time (minutes), weighted by reply pair count
    blended_response = []
    blended_daytime = []
    for d in dates:
        total_hours = 0
        total_count = 0
        total_hours_dt = 0
        total_count_dt = 0
        for acct, day_map in email_by_account.items():
            entry = day_map.get(d, {})
            h = entry.get("avg_hours")
            c = entry.get("count", 0)
            h_dt = entry.get("avg_hours_daytime")
            c_dt = entry.get("count_daytime", 0)
            if h is not None and c > 0:
                total_hours += h * c
                total_count += c
            if h_dt is not None and c_dt > 0:
                total_hours_dt += h_dt * c_dt
                total_count_dt += c_dt
        blended_response.append(round(total_hours / total_count * 60, 1) if total_count > 0 else None)
        blended_daytime.append(round(total_hours_dt / total_count_dt * 60, 1) if total_count_dt > 0 else None)

    email_datasets = []
    # Purple blended line — overall (no exclusions)
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
    # Pink line — daytime only (excludes inbound 9pm–6am)
    email_datasets.append({
        "type": "line",
        "label": "avg response - daytime",
        "data": blended_daytime,
        "borderColor": "#ff4081",
        "backgroundColor": "transparent",
        "borderWidth": 2,
        "pointRadius": 3,
        "tension": 0,
        "spanGaps": True,
        "yAxisID": "y",
    })
    # Per-account inbound/reply count bars (stacked) — ordered so m5x2+slack are adjacent
    EMAIL_BAR_ORDER = ["outlook", "teams", "m5x2 gmail", "slack", "imessage", "s897 gmail"]
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
            "stack": "inbound",
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
                "stack": "inbound",
            })
    # Outbound/sent bars per channel (stacked, darker versions of inbound colors)
    EMAIL_BAR_COLORS_DARK = {
        "m5x2 gmail": "#d50032aa", "m5x2": "#d50032aa",
        "s897 gmail": "#1b5e20aa", "personal": "#1b5e20aa", "gmail": "#1b5e20aa",
        "imessage": "#34c759aa",
        "slack": "#9b0023aa",
        "outlook": "#00b8d4aa",
        "teams": "#1249b4aa",
    }
    for acct in EMAIL_BAR_ORDER:
        day_map = email_by_account.get(acct, {})
        if not day_map:
            continue
        email_datasets.append({
            "type": "bar",
            "label": acct + " sent",
            "data": [max(0, day_map.get(d, {}).get("sent_count", 0) - day_map.get(d, {}).get("count", 0)) for d in dates],
            "backgroundColor": EMAIL_BAR_COLORS_DARK.get(acct, "#aaaaaaaa"),
            "borderWidth": 0,
            "yAxisID": "y2",
            "stack": "outbound",
        })
    for acct, day_map in sorted(email_by_account.items()):
        if acct not in EMAIL_BAR_ORDER:
            email_datasets.append({
                "type": "bar",
                "label": acct + " sent",
                "data": [max(0, day_map.get(d, {}).get("sent_count", 0) - day_map.get(d, {}).get("count", 0)) for d in dates],
                "backgroundColor": EMAIL_BAR_COLORS_DARK.get(acct, "#aaaaaaaa"),
                "borderWidth": 0,
                "yAxisID": "y2",
                "stack": "outbound",
            })

    # Summary stats
    total_points = {label: sum(points_raw.get(d, {}).get(label, 0) for d in dates)
                    for label in all_point_labels}
    total_time = {code: sum(toggl_raw.get(d, {}).get(code, 0) for d in dates)
                  for code in all_projects}

    # GA4 pageviews per day
    ga4_daily = ga4_raw.get("daily", {})
    ga4_views = [ga4_daily.get(d, 0) for d in dates]

    cache_payload = [
        {
            "label":  card["label"],
            "value":  cache_raw.get(card["label"]),
            "color":  card["color"],
            "period": card["period"],
        }
        for card in CACHE_CARDS
    ]

    return {
        "dates": [d[5:] for d in dates],  # MM-DD for display
        "cache": cache_payload,
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
        "ga4": {"views": ga4_views, "top_pages": ga4_raw.get("top_pages", [])},
        "summary": {
            "total_points": {k: int(v) for k, v in total_points.items() if v > 0},
            "total_mins": {k: int(v) for k, v in total_time.items() if v > 0},
            "total_turns": sum(turns_values),
            "total_tasks": sum(tasks_values),
            "total_views": sum(ga4_views),
        }
    }


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
.cache-bars { display: flex; flex-direction: column; gap: 8px; }
.cache-row { display: grid; grid-template-columns: 80px 1fr 1fr 70px; align-items: center; gap: 8px; font-size: 12px; }
.cache-label { color: var(--badge-text); text-align: right; padding-right: 8px; line-height: 1.1; }
.cache-label .period { display: block; font-size: 9px; opacity: 0.6; letter-spacing: 0.5px; }
.cache-track-l, .cache-track-r { height: 14px; position: relative; }
.cache-track-l { background: linear-gradient(to left, var(--grid), transparent); }
.cache-track-r { background: linear-gradient(to right, var(--grid), transparent); }
.cache-bar { position: absolute; top: 0; bottom: 0; border-radius: 2px; }
.cache-bar.neg { right: 0; }
.cache-bar.pos { left: 0; }
.cache-value { font-variant-numeric: tabular-nums; }
.cache-value.neg { color: #ff5252; }
.cache-value.pos { color: #00e676; }
.cache-value.zero { color: var(--badge-text); }
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
    <h2>Cache · Q2</h2>
    <div id="cacheBars" class="cache-bars"></div>
  </div>
  <div class="card">
    <h2>Project Blocking — Comms Response Time</h2>
    <div class="chart-wrap sm"><canvas id="emailChart"></canvas></div>
    <div class="summary" id="emailSummary"></div>
  </div>
  <div class="card">
    <h2>Tasks Complete / Day</h2>
    <div class="chart-wrap xs"><canvas id="tasksChart"></canvas></div>
    <div class="summary" id="tasksSummary"></div>
  </div>
  <div class="card">
    <h2>Time / Day</h2>
    <div class="chart-wrap"><canvas id="timeChart"></canvas></div>
    <div class="summary" id="timeSummary"></div>
  </div>
  <div class="card">
    <h2>Points / Day</h2>
    <div class="chart-wrap"><canvas id="pointsChart"></canvas></div>
    <div class="summary" id="pointsSummary"></div>
  </div>
</div>

<script>
""" + _SHARED_JS_HEAD + """
fetch('/api/data').then(r => r.json()).then(data => {
  const labels = data.dates;

  // Cache bars (Q2 cumulative + or - 分 by area)
  const cache = data.cache || [];
  const maxAbs = Math.max(1, ...cache.map(c => Math.abs(c.value || 0)));
  const cbEl = document.getElementById('cacheBars');
  cache.forEach(c => {
    const row = document.createElement('div');
    row.className = 'cache-row';
    const v = c.value;
    const cls = v == null ? 'zero' : (v < 0 ? 'neg' : (v > 0 ? 'pos' : 'zero'));
    const display = v == null ? '—' : (v > 0 ? '+' : '') + Math.round(v);
    const pct = v == null ? 0 : Math.min(100, Math.abs(v) / maxAbs * 100);
    const negBar = (v != null && v < 0)
      ? `<div class="cache-bar neg" style="width:${pct}%;background:${c.color}"></div>` : '';
    const posBar = (v != null && v > 0)
      ? `<div class="cache-bar pos" style="width:${pct}%;background:${c.color}"></div>` : '';
    row.innerHTML = `
      <div class="cache-label">${c.label}<span class="period">${c.period||''}</span></div>
      <div class="cache-track-l">${negBar}</div>
      <div class="cache-track-r">${posBar}</div>
      <div class="cache-value ${cls}">${display}</div>
    `;
    cbEl.appendChild(row);
  });

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
    ['avg response - daytime', '#ff4081', 'line'],
    ['outlook', '#00b8d4', 'bar'],
    ['teams', '#1249b4', 'bar'],
    ['m5x2 gmail', '#d50032', 'bar'],
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
  <div class="card">
    <h2>o315 Blog — Pageviews / Day</h2>
    <div class="chart-wrap xs"><canvas id="ga4Chart"></canvas></div>
    <div class="summary" id="ga4Summary"></div>
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

  // GA4 pageviews chart
  const ga4 = data.ga4 || { views: [], top_pages: [] };
  new Chart(document.getElementById('ga4Chart'), {
    type: 'bar',
    data: { labels, datasets: [{
      label: 'pageviews',
      data: ga4.views,
      backgroundColor: '#00e67644',
      borderColor: '#00e676',
      borderWidth: 1,
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID }, beginAtZero: true }
      }
    }
  });
  // Top pages badges
  const ga4El = document.getElementById('ga4Summary');
  const totalViews = data.summary.total_views || 0;
  if (totalViews > 0) {
    const tb = document.createElement('div');
    tb.className = 'badge';
    tb.innerHTML = `total <span>${totalViews}</span>`;
    ga4El.appendChild(tb);
  }
  (ga4.top_pages || []).slice(0, 5).forEach(p => {
    const b = document.createElement('div');
    b.className = 'badge';
    const short = p.path.length > 30 ? p.path.slice(0, 27) + '...' : p.path;
    b.innerHTML = `${short} <span>${p.views}</span>`;
    ga4El.appendChild(b);
  });
});
</script>
</body>
</html>"""


@app.route("/auth/ga4")
def auth_ga4():
    """Run GA4 OAuth flow. Visit this URL in a browser to authenticate."""
    if not GA4_AVAILABLE:
        return "GA4 libraries not installed. Run: pip install google-analytics-data google-auth-oauthlib", 500
    if not GA4_OAUTH_KEYS.exists():
        return f"OAuth client keys not found at {GA4_OAUTH_KEYS}", 500
    flow = InstalledAppFlow.from_client_secrets_file(str(GA4_OAUTH_KEYS), GA4_SCOPES)
    creds = flow.run_local_server(port=0)
    GA4_TOKENS.write_text(creds.to_json())
    return "GA4 OAuth complete. Tokens saved. Pageviews should now load on the dashboard."


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/more")
def more():
    return render_template_string(MORE_HTML)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5558, debug=False)
