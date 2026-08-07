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
from flask import Flask, render_template_string, jsonify, request

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
    "m828":  "#9b0023",  # Velvet
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

# 地支 2-hour blocks (卯..亥, 04:00-22:00) — skips the 22:00-04:00 sleep
# window. Mirrors scripts/build-order-daemon.py's BRANCH_HOURS; G:O are the
# per-block points columns in the 0分 sheet (see g245/CLAUDE.md's column map).
BRANCH_BLOCKS = [
    ("卯", 4, "G"), ("辰", 6, "H"), ("巳", 8, "I"), ("午", 10, "J"),
    ("未", 12, "K"), ("申", 14, "L"), ("酉", 16, "M"), ("戌", 18, "N"),
    ("亥", 20, "O"),
]
N_BLOCKS_PER_DAY = len(BRANCH_BLOCKS)
GRANULAR_BLOCK_DAYS = 3  # trailing days shown in the per-block chart view (27 bars)


def _block_index_for_hour(hour):
    """0-8 index into BRANCH_BLOCKS for a wall-clock hour, or None if asleep (22-04)."""
    for i, (_, start_hour, _) in enumerate(BRANCH_BLOCKS):
        if start_hour <= hour < start_hour + 2:
            return i
    return None


# GA4 config
GA4_PROPERTY_ID = os.environ.get("GA4_PROPERTY_ID", "")
GA4_OAUTH_KEYS = Path(__file__).parent / "ga4-oauth.keys.json"
GA4_TOKENS = Path(__file__).parent / "ga4-tokens.json"
GA4_SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


# ── Data loaders ───────────────────────────────────────────────────────────────

# A full B2:end(down) scan of 0分 reaches ~365 rows (the sheet is pre-templated
# with calendar rows into next January). Reading the whole G:Y block range
# (359 rows x 19 cols) in one xlwings/AppleEvent call reliably crashes with
# `appscript.reference.CommandError: OSERROR -609 (Connection is invalid)`
# (confirmed live 2026-07-21). Older rows never change once the build-order
# daemon locks them at fire time, so only a bounded window around "today"
# ever needs a live re-read — this both avoids the crash and is what made
# Points/Block silently go empty (see _get_points_cache below).
_BLOCK_REFRESH_WINDOW_DAYS = 45

_POINTS_CACHE_PATH = Path(__file__).parent / ".points-cache.json"


def _points_col_letter(idx):
    return chr(64 + idx) if idx <= 26 else "A" + chr(64 + idx - 26)


_XLWINGS_BLOCK_RETRIES = 3


def _xlwings_block_window(window_days=_BLOCK_REFRESH_WINDOW_DAYS):
    """Bounded live re-read of the last `window_days` rows of 0分's G:Y
    columns via xlwings. Returns {date_str: day_data} for just that window
    (empty dict if every retry fails) — never the full sheet, see the crash
    note above.

    Retries with a fresh xw.Book() connection each time: the same bounded
    read was observed (live, 2026-07-21) to fail with
    `CommandError: OSERROR -609/-1728` on one attempt and succeed
    immediately on the next with identical arguments — the AppleEvent
    bridge to Excel is just intermittently flaky (same family as the
    save-hang issue found the same day), not deterministically broken by
    range size or row position. Bounding the range lowers how often this
    hits; it doesn't eliminate it, hence the retry.
    """
    for attempt in range(_XLWINGS_BLOCK_RETRIES):
        try:
            import xlwings as xw
            wb = xw.Book(str(NEON_PATH))
            ws = wb.sheets["0分"]
            # 0分 is pre-templated with calendar rows reaching months into the
            # future (empty, no real data) — `end("down")` on B2 walks all the
            # way to that far frontier (row ~365, into next January), NOT to
            # today's row. Bounding the window relative to THAT end row (as an
            # earlier version of this function did) targeted blank future rows
            # instead of the recent past, which is the whole point of this
            # function — self-caught live, 2026-07-21: the window computed
            # this way silently read zero real data every time. Locate today's
            # actual row via the single-column B scan (bulk single-column
            # reads have proven reliable even across the full sheet in
            # testing) and bound the window around THAT instead.
            last_row = ws.range("B2").end("down").row
            b_scan = ws.range(f"B3:B{last_row}").value
            if not isinstance(b_scan, list):
                b_scan = [b_scan]
            today = date.today()
            anchor_row = None
            for i, b in enumerate(b_scan):
                d = b.date() if isinstance(b, datetime) else (b if isinstance(b, date) else None)
                if d is not None and d <= today:
                    anchor_row = i + 3  # most recent row seen so far that isn't in the future
                elif d is not None and d > today:
                    break
            if anchor_row is None:
                anchor_row = last_row
            window_start = max(3, anchor_row - window_days + 1)
            window_end = anchor_row
            min_idx = 7  # G — first per-block points column, precedes POINTS_COLS' P:Y range
            max_idx = max(POINTS_COLS)
            first_col = _points_col_letter(min_idx)
            last_col = _points_col_letter(max_idx)
            b_vals = b_scan[window_start - 3:window_end - 3 + 1]
            block = ws.range(f"{first_col}{window_start}:{last_col}{window_end}").value
            if not isinstance(block, list):
                block = [[block]]
            elif block and not isinstance(block[0], list):
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
                day_str = d.isoformat()
                day_data = {}
                row_vals = block[i] if i < len(block) else []
                for col_idx, meta in POINTS_COLS.items():
                    offset = col_idx - min_idx
                    val = row_vals[offset] if offset < len(row_vals) else None
                    if val is not None and isinstance(val, (int, float)) and val > 0:
                        day_data[meta["label"]] = int(round(float(val)))
                block_data = {}
                for j, (branch, _, _) in enumerate(BRANCH_BLOCKS):
                    val = row_vals[j] if j < len(row_vals) else None
                    if val is not None and isinstance(val, (int, float)) and val > 0:
                        block_data[branch] = int(round(float(val)))
                if block_data:
                    day_data["__block__"] = block_data
                if day_data:
                    result[day_str] = day_data
            return result
        except Exception:
            if attempt == _XLWINGS_BLOCK_RETRIES - 1:
                return {}
            continue
    return {}


def _get_points_cache():
    """Full, unfiltered {date_str: day_data} points cache, self-healing on
    staleness.

    Bug (2026-07-21): the block-granularity chart called load_points_all(),
    which just trusted whatever was on disk forever — no staleness check at
    all. The ONLY writer of this cache is /0t's refresh_points_cache(), which
    runs on whichever machine invokes /0t (usually the laptop, not ix where
    the dashboard server actually lives); i446-monorepo isn't synced between
    hosts, so that write never reached ix's copy and Points/Block silently
    went stale indefinitely. This function is now the single source both
    load_points_data() and load_points_all() call: it compares the cache's
    mtime against the live Neon file's mtime and, if stale, does a bounded
    live xlwings re-read (see _xlwings_block_window) and MERGES it into the
    on-disk cache — merged, not overwritten, so this never destroys the
    older history the weekly/monthly views depend on (the old xlwings path
    here used to rebuild+overwrite the whole file with only its own 30-day
    window, silently truncating everything older every time it ran)."""
    cached = {}
    if _POINTS_CACHE_PATH.exists():
        try:
            cached = json.loads(_POINTS_CACHE_PATH.read_text())
        except Exception:
            cached = {}

    try:
        cache_mtime = _POINTS_CACHE_PATH.stat().st_mtime if _POINTS_CACHE_PATH.exists() else 0
        excel_mtime = NEON_PATH.stat().st_mtime if NEON_PATH.exists() else 0
    except Exception:
        cache_mtime = excel_mtime = 0

    if cached and cache_mtime >= excel_mtime:
        return cached

    fresh = _xlwings_block_window()
    if not fresh:
        return cached  # live read failed; stale-but-present beats nothing

    merged = dict(cached)
    merged.update(fresh)
    try:
        _POINTS_CACHE_PATH.write_text(json.dumps(merged))
    except Exception:
        pass
    return merged


def load_points_data():
    """Read 0分 sheet, return {date_str: {label: value}} for last DAYS days.

    Each day's dict also carries a "__block__": {branch: points} entry read
    from the G:O per-block columns (卯..亥) in the same pass — independent
    numbers from the P:Y domain totals (each block column is locked to a
    literal value as the daemon fires; see build-order-daemon.py's
    LOCK_AT_FIRE_HOUR), not derived from POINTS_COLS.

    Backed by _get_points_cache()'s self-healing full cache, filtered here
    to the trailing DAYS-day window this function has always returned.
    """
    today = date.today()
    cutoff = today - timedelta(days=DAYS)
    full = _get_points_cache()
    return {d: v for d, v in full.items()
            if cutoff < date.fromisoformat(d) <= today}


# Each card pulls a single cell from 0n. Headers live on row 369 (and row 1
# for xk88). We hardcode (col, row) per card since headers span two rows.
CACHE_CARDS = [
    {"label": "hcbp",   "col": "AB", "row": 372, "period": "Q3",   "color": "#f81d78"},
    {"label": "hcbc",   "col": "AF", "row": 371, "period": "Q2",   "color": "#ff4081"},
    {"label": "xk88",   "col": "AN", "row": 375, "period": "2026", "color": "#e65100"},
    {"label": "ص",      "col": "AP", "row": 375, "period": "2026", "color": "#9c27b0"},
    {"label": "o314",   "col": "AQ", "row": 375, "period": "2026", "color": "#7c4dff"},
    {"label": "冥想",   "col": "AR", "row": 375, "period": "2026", "color": "#aa00ff"},
    {"label": "其他人", "col": "AS", "row": 375, "period": "2026", "color": "#673ab7"},
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
    entry_counts = defaultdict(lambda: defaultdict(int))
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
        entry_counts[d.isoformat()][code] += 1

    return {k: dict(v) for k, v in result.items()}, {k: dict(v) for k, v in entry_counts.items()}


# ── Weekly (1s) aggregation ─────────────────────────────────────────────────────

def _week_start(d):
    """Sunday on/before date d (Su-Sa weeks). weekday(): Mon=0..Sun=6."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _month_start(d):
    """First of the month containing date d."""
    return d.replace(day=1)


def _add_month(d, n=1):
    """First-of-month date n months after d (d must be day=1)."""
    m0 = d.month - 1 + n
    y, m = d.year + m0 // 12, m0 % 12 + 1
    return date(y, m, 1)


def load_points_all():
    """Full points cache {date_str: {label: value}} with no day clipping.

    Backed by _get_points_cache(), same self-healing staleness check
    load_points_data() uses — previously this just read disk unconditionally
    forever, which is why Points/Block went stale (see _get_points_cache).
    """
    try:
        return _get_points_cache()
    except Exception:
        return {}


_TOGGL_DAILY_CACHE_PATH = Path(__file__).parent / ".toggl-daily-cache.json"


def load_toggl_daily_cache():
    """Persistent per-day Toggl minutes + entry-counts, populated by
    backfill_toggl_cache.py via the Reports v3 API (reaches back further than
    the live v9 endpoint's ~90-day floor — see that script's docstring for why
    the live endpoint can't just be asked for a longer range).

    Returns ({date_str: {code: minutes}}, {date_str: {code: entry_count}}), a
    point-in-time snapshot — callers needing today/yesterday to be current
    should overlay a live load_toggl_range() tail on top.
    """
    if not _TOGGL_DAILY_CACHE_PATH.exists():
        return {}, {}
    try:
        raw = json.loads(_TOGGL_DAILY_CACHE_PATH.read_text())
    except Exception:
        return {}, {}
    minutes = {d: v.get("minutes", {}) for d, v in raw.items()}
    entries = {d: v.get("entries", {}) for d, v in raw.items()}
    return minutes, entries


def _fetch_toggl_entries(days):
    """Raw Toggl time entries for the last `days` days (empty list on any
    failure). Shared by load_toggl_range (day-bucketed) and the per-block
    chart builder (2-hour-bucketed) so both consume the same API call shape
    without duplicating the request/auth boilerplate."""
    api_key = os.environ.get("TOGGL_API_KEY", "")
    if not api_key:
        return []
    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    url = f"https://api.track.toggl.com/api/v9/me/time_entries?start_date={start}&end_date={end}"
    creds = base64.b64encode(f"{api_key}:api_token".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


def load_toggl_range(days, return_counts=False):
    """Toggl entries for the last `days` days → {date_str: {project: minutes}}.

    With return_counts=True, also returns {date_str: {project: entry_count}}
    as a second tuple element (mirrors load_toggl_data's shape for arbitrary
    day ranges, so granular chart views can compute the Entries chart too).
    """
    if not os.environ.get("TOGGL_API_KEY", ""):
        return ({}, {}) if return_counts else {}
    today = date.today()
    floor = today - timedelta(days=days)
    entries = _fetch_toggl_entries(days)
    result = defaultdict(lambda: defaultdict(int))
    entry_counts = defaultdict(lambda: defaultdict(int))
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
        if d < floor or d > today:
            continue
        proj_id = e.get("project_id")
        code = PROJECT_ID_TO_CODE.get(proj_id, "no project") if proj_id else "no project"
        result[d.isoformat()][code] += dur // 60
        entry_counts[d.isoformat()][code] += 1
    minutes = {k: dict(v) for k, v in result.items()}
    if return_counts:
        return minutes, {k: dict(v) for k, v in entry_counts.items()}
    return minutes


def _build_weekly_data(n_weeks=8):
    """Weekly time/points by category + cumulative this-week vs last-week shadow."""
    today = date.today()
    this_week_start = _week_start(today)
    first_week_start = this_week_start - timedelta(weeks=n_weeks - 1)

    points_all = load_points_all()
    days_span = (today - first_week_start).days + 2
    toggl_all = load_toggl_range(days_span)

    week_starts = [first_week_start + timedelta(weeks=i) for i in range(n_weeks)]
    week_labels = [ws.strftime("%-m/%-d") for ws in week_starts]

    def week_index(d):
        delta = (d - first_week_start).days
        if delta < 0:
            return None
        wi = delta // 7
        return wi if wi < n_weeks else None

    pts_cats = [m["label"] for m in POINTS_COLS.values()]
    pts_colors = {m["label"]: m["color"] for m in POINTS_COLS.values()}
    pts_week = {cat: [0] * n_weeks for cat in pts_cats}
    for dstr, day in points_all.items():
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        wi = week_index(d)
        if wi is None:
            continue
        for cat, val in day.items():
            if cat in pts_week and isinstance(val, (int, float)):
                pts_week[cat][wi] += val

    time_week = defaultdict(lambda: [0] * n_weeks)
    for dstr, day in toggl_all.items():
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        wi = week_index(d)
        if wi is None:
            continue
        for code, mins in day.items():
            time_week[code][wi] += mins

    POINTS_PRIORITY = ["i9", "xk", "m5"]
    def pts_sort(label):
        if label in POINTS_PRIORITY:
            return (0, POINTS_PRIORITY.index(label))
        return (1, pts_cats.index(label))
    ordered_pts = sorted([c for c in pts_cats if any(pts_week[c])], key=pts_sort)
    points_datasets = [{
        "label": c, "data": pts_week[c],
        "backgroundColor": pts_colors.get(c, "#9e9e9e"),
    } for c in ordered_pts]

    TIME_PRIORITY = ["i9", "xk87", "m5x2"]
    def time_sort(code):
        if code in TIME_PRIORITY:
            return (0, TIME_PRIORITY.index(code))
        if code == "睡觉":
            return (2, 0)
        return (1, -sum(time_week[code]))
    time_codes = sorted([c for c in time_week if any(time_week[c])], key=time_sort)
    time_datasets = [{
        "label": c,
        "data": [round(m / 60, 1) for m in time_week[c]],
        "backgroundColor": PROJECT_COLORS.get(c, "#424242"),
    } for c in time_codes]

    # cumulative this week vs last week, by category, Su..Sa
    last_week_start = this_week_start - timedelta(weeks=1)
    dow_labels = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
    today_dow = (today - this_week_start).days  # 0..6

    def cumulative_by_cat(week_start, clip_to=None):
        per = {cat: [0] * 7 for cat in pts_cats}
        for off in range(7):
            d = (week_start + timedelta(days=off)).isoformat()
            day = points_all.get(d, {})
            for cat in pts_cats:
                prev = per[cat][off - 1] if off > 0 else 0
                add = day.get(cat, 0)
                add = add if isinstance(add, (int, float)) else 0
                per[cat][off] = prev + add
        if clip_to is not None:
            for cat in pts_cats:
                for off in range(7):
                    if off > clip_to:
                        per[cat][off] = None
        return per

    this_cum = cumulative_by_cat(this_week_start, clip_to=today_dow)
    last_cum = cumulative_by_cat(last_week_start, clip_to=None)

    cum_cats = [c for c in pts_cats
                if any(v for v in (this_cum[c] + last_cum[c]) if v)]
    cum_cats = sorted(cum_cats, key=pts_sort)

    cum_datasets = []
    for c in cum_cats:
        cum_datasets.append({
            "label": c, "stack": "this", "data": this_cum[c],
            "backgroundColor": pts_colors.get(c, "#9e9e9e"),
        })
    for c in cum_cats:
        cum_datasets.append({
            "label": c + " ·last", "stack": "last", "data": last_cum[c],
            "backgroundColor": pts_colors.get(c, "#9e9e9e") + "40",
        })

    this_total = sum((this_cum[c][today_dow] or 0) for c in cum_cats)
    last_to_date = sum((last_cum[c][today_dow] or 0) for c in cum_cats)
    last_full = sum((last_cum[c][6] or 0) for c in cum_cats)

    return {
        "weeks": week_labels,
        "points_week": {"datasets": points_datasets},
        "time_week": {"datasets": time_datasets},
        "cumulative": {
            "labels": dow_labels,
            "today_index": today_dow,
            "datasets": cum_datasets,
            "this_total": int(this_total),
            "last_to_date": int(last_to_date),
            "last_full": int(last_full),
        },
        "this_week_start": this_week_start.strftime("%-m/%-d"),
    }


# ── Granular chart data (Task/Entries/Time/Points dropdown: weekly/monthly) ────
# Daily granularity reuses the existing /api/data payload client-side (no
# server code path here) — this only builds the weekly/monthly bucketed views,
# kept deliberately separate from _build_api_data so the daily payload (and
# everything else it feeds — email/ga4/imessage/cache cards) is untouched.

GRANULAR_WEEKS = 26   # ~6 months of Su-Sa weeks
GRANULAR_MONTHS = 12  # 1 year of calendar months


def _build_block_chart_data(n_days=GRANULAR_BLOCK_DAYS):
    """Points/Time/Tasks/Entries chart data bucketed into trailing 2-hour 地支
    blocks (last n_days days x 9 blocks/day, skipping the 22:00-04:00 sleep
    window). Each bar's label is a [branch, "M/D"] pair — Chart.js renders a
    2-element label array as two tick lines, so every bar shows both its
    block (卯..亥) and the date it belongs to, making day boundaries in the
    trailing window legible without a separate grouped-axis renderer.

    Unlike the weekly/monthly buckets in _build_granular_chart_data, Points
    here is a single "分" series (the sheet's G:O columns are per-block
    totals, not broken out by domain like P:Y), not a stacked-by-domain set.
    """
    today = date.today()
    day_list = [today - timedelta(days=n) for n in range(n_days - 1, -1, -1)]
    bucket_labels = [[branch, d.strftime("%-m/%-d")]
                     for d in day_list for branch, _, _ in BRANCH_BLOCKS]
    n_buckets = len(bucket_labels)
    day_pos = {d: i for i, d in enumerate(day_list)}
    branch_pos = {branch: i for i, (branch, _, _) in enumerate(BRANCH_BLOCKS)}

    def bucket_index(d, branch):
        di = day_pos.get(d)
        bi = branch_pos.get(branch)
        if di is None or bi is None:
            return None
        return di * N_BLOCKS_PER_DAY + bi

    # Points — single series, summed from the "__block__" per-day breakdown
    points_all = load_points_all()
    pts_bucketed = [0] * n_buckets
    for dstr, day in points_all.items():
        block_data = day.get("__block__") if isinstance(day, dict) else None
        if not block_data:
            continue
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        for branch, val in block_data.items():
            idx = bucket_index(d, branch)
            if idx is not None and isinstance(val, (int, float)):
                pts_bucketed[idx] += val
    points_datasets = [{"label": "分", "data": pts_bucketed, "backgroundColor": "#7c4dff"}]

    # Time (minutes) + entry counts — bucketed from raw Toggl entries via
    # their real start timestamps (unlike weekly/monthly, no daily-cache
    # overlay needed: n_days is small enough for a single live fetch).
    #
    # Minutes are CLIPPED to each block's own 120-min window: an entry that
    # runs past its start block's boundary (or spans several blocks) used to
    # dump its FULL duration into the block containing its start, so a single
    # block's Time bar could exceed the 120 real minutes physically available
    # in a 2h block. Each entry's overlap with every block is computed via
    # simple interval intersection and only the overlapping minutes land in
    # that block, so per-block Time now naturally caps at 120.
    entries = _fetch_toggl_entries(n_days)
    time_bucketed = defaultdict(lambda: [0] * n_buckets)
    entries_bucketed = defaultdict(lambda: [0] * n_buckets)
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
        end_dt = start_dt + timedelta(seconds=dur)
        proj_id = e.get("project_id")
        code = PROJECT_ID_TO_CODE.get(proj_id, "no project") if proj_id else "no project"

        # Entry count stays attributed to the block the entry STARTED in
        # (unchanged) — only the minutes get split/clipped across blocks.
        start_bi = _block_index_for_hour(start_dt.hour)
        if start_bi is not None:
            start_idx = bucket_index(start_dt.date(), BRANCH_BLOCKS[start_bi][0])
            if start_idx is not None:
                entries_bucketed[code][start_idx] += 1

        for d in day_list:
            for branch, block_start_hour, _ in BRANCH_BLOCKS:
                idx = bucket_index(d, branch)
                if idx is None:
                    continue
                block_start_dt = datetime(d.year, d.month, d.day, block_start_hour, 0, tzinfo=LOCAL_TZ)
                block_end_dt = block_start_dt + timedelta(hours=2)
                overlap_start = max(start_dt, block_start_dt)
                overlap_end = min(end_dt, block_end_dt)
                overlap_min = (overlap_end - overlap_start).total_seconds() / 60
                if overlap_min > 0:
                    time_bucketed[code][idx] += round(overlap_min)

    TIME_PRIORITY = ["i9", "xk87", "m5x2"]
    def time_sort(code):
        if code in TIME_PRIORITY:
            return (0, TIME_PRIORITY.index(code))
        if code == "睡觉":
            return (2, 0)
        return (1, -sum(time_bucketed[code]))
    time_codes = sorted([c for c in time_bucketed if any(time_bucketed[c])], key=time_sort)
    time_datasets = [{
        "label": c, "data": time_bucketed[c],
        "backgroundColor": PROJECT_COLORS.get(c, "#424242"),
    } for c in time_codes]

    entry_codes_sorted = [c for c in time_codes if c in entries_bucketed] + \
        [c for c in entries_bucketed if c not in time_codes]
    entries_datasets = [{
        "label": c, "data": entries_bucketed[c],
        "backgroundColor": PROJECT_COLORS.get(c, "#424242"),
    } for c in entry_codes_sorted if any(entries_bucketed[c])]
    time_entries_values = [sum(entries_bucketed[c][i] for c in entries_bucketed) for i in range(n_buckets)]

    # Tasks — bucketed from each completion's completed_at via load_tasks_by_block
    tasks_all_blocked = load_tasks_by_block(n_days)
    tasks_neon = [0] * n_buckets
    tasks_posthoc = [0] * n_buckets
    tasks_1n = [0] * n_buckets
    tasks_neg1n = [0] * n_buckets
    tasks_other = [0] * n_buckets
    for dstr, counts in tasks_all_blocked.items():
        by_block = (counts or {}).get("by_block") or {}
        if not by_block:
            continue
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        for branch, cat_counts in by_block.items():
            idx = bucket_index(d, branch)
            if idx is None:
                continue
            tasks_neon[idx] += cat_counts.get("neon", 0)
            tasks_posthoc[idx] += cat_counts.get("posthoc", 0)
            tasks_1n[idx] += cat_counts.get("one_n", 0)
            tasks_neg1n[idx] += cat_counts.get("neg1n", 0)
            tasks_other[idx] += cat_counts.get("other", 0)

    # Project Bocking — real per-block comms data (see
    # _build_email_by_account_blocked for what makes this possible). Re-key
    # each account's {(day,branch): entry} onto this chart's own bucket_index
    # so _build_email_datasets (shared with the daily/weekly/monthly views)
    # can build the same Chart.js dataset shape unchanged.
    email_raw = load_email_data()
    email_by_account_blocked = _build_email_by_account_blocked(email_raw, day_list)
    email_data = None
    if email_by_account_blocked:
        block_keys = [str(i) for i in range(n_buckets)]
        keyed = defaultdict(dict)
        for acct, day_branch_map in email_by_account_blocked.items():
            for (dstr, branch), entry in day_branch_map.items():
                try:
                    d = date.fromisoformat(dstr)
                except Exception:
                    continue
                idx = bucket_index(d, branch)
                if idx is not None:
                    keyed[acct][str(idx)] = entry
        if keyed:
            email_data = {"datasets": _build_email_datasets(keyed, block_keys)}

    result = {
        "dates": bucket_labels,
        "points": {"datasets": points_datasets},
        "time": {"datasets": time_datasets},
        "tasks_neon": tasks_neon,
        "tasks_posthoc": tasks_posthoc,
        "tasks_1n": tasks_1n,
        "tasks_neg1n": tasks_neg1n,
        "tasks_other": tasks_other,
        "time_entries": time_entries_values,
        "entries": {"datasets": entries_datasets},
    }
    if email_data:
        result["email"] = email_data
    return result


def _build_granular_chart_data(granularity):
    """Points/Time/Tasks/Entries chart data bucketed weekly or monthly.

    Returns the same shape as the relevant subset of _build_api_data()'s
    payload (dates/points/time/tasks_*/entries/time_entries), so the
    frontend's chart-building code can treat daily/weekly/monthly uniformly.
    """
    if granularity == "block":
        return _build_block_chart_data()

    today = date.today()

    if granularity == "monthly":
        this_month = _month_start(today)
        first_month = this_month
        for _ in range(GRANULAR_MONTHS - 1):
            first_month = date(first_month.year - (1 if first_month.month == 1 else 0),
                                12 if first_month.month == 1 else first_month.month - 1, 1)
        bucket_starts = []
        cur = first_month
        for _ in range(GRANULAR_MONTHS):
            bucket_starts.append(cur)
            cur = _add_month(cur)
        bucket_labels = [bs.strftime("%b '%y") for bs in bucket_starts]
        days_span = (today - first_month).days + 2

        def bucket_index(d):
            if d < first_month:
                return None
            for i in range(len(bucket_starts) - 1, -1, -1):
                if d >= bucket_starts[i]:
                    return i
            return None
    else:  # "weekly" (default fallback for anything unrecognized)
        this_week_start = _week_start(today)
        first_week_start = this_week_start - timedelta(weeks=GRANULAR_WEEKS - 1)
        bucket_starts = [first_week_start + timedelta(weeks=i) for i in range(GRANULAR_WEEKS)]
        bucket_labels = [ws.strftime("%-m/%-d") for ws in bucket_starts]
        days_span = (today - first_week_start).days + 2

        def bucket_index(d):
            delta = (d - first_week_start).days
            if delta < 0:
                return None
            wi = delta // 7
            return wi if wi < GRANULAR_WEEKS else None

    n_buckets = len(bucket_starts)

    points_all = load_points_all()
    # Time/Entries: base data comes from the persistent .toggl-daily-cache.json
    # (built by backfill_toggl_cache.py via the Reports v3 API, chunked into
    # ~60-day windows to stay fast — see that script's docstring for why a
    # single large Reports v3 request is too slow, and why the live v9
    # endpoint can't reach back this far at all). That cache is a
    # point-in-time snapshot, so the last few days are overlaid with a live v9
    # fetch (fast, and within v9's ~90-day floor) for freshness — same
    # refetch-tail pattern as load_tasks_data's _TASKS_REFETCH_TAIL.
    TOGGL_CACHE_TAIL_DAYS = 3
    toggl_all, toggl_counts = load_toggl_daily_cache()
    live_minutes, live_counts = load_toggl_range(TOGGL_CACHE_TAIL_DAYS, return_counts=True)
    toggl_all = {**toggl_all, **live_minutes}
    toggl_counts = {**toggl_counts, **live_counts}
    tasks_all = load_tasks_data(n_days=days_span)

    # Points, bucketed and summed
    pts_cats = [m["label"] for m in POINTS_COLS.values()]
    pts_colors = {m["label"]: m["color"] for m in POINTS_COLS.values()}
    pts_bucketed = {cat: [0] * n_buckets for cat in pts_cats}
    for dstr, day in points_all.items():
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        bi = bucket_index(d)
        if bi is None:
            continue
        for cat, val in day.items():
            if cat in pts_bucketed and isinstance(val, (int, float)):
                pts_bucketed[cat][bi] += val

    POINTS_PRIORITY = ["i9", "xk", "m5"]
    def pts_sort(label):
        if label in POINTS_PRIORITY:
            return (0, POINTS_PRIORITY.index(label))
        return (1, pts_cats.index(label))
    ordered_pts = sorted([c for c in pts_cats if any(pts_bucketed[c])], key=pts_sort)
    points_datasets = [{
        "label": c, "data": pts_bucketed[c],
        "backgroundColor": pts_colors.get(c, "#9e9e9e"),
    } for c in ordered_pts]

    # Time (minutes) + entry counts, bucketed and summed, same project order/colors as daily
    TIME_PRIORITY = ["i9", "xk87", "m5x2"]
    time_bucketed = defaultdict(lambda: [0] * n_buckets)
    for dstr, day in toggl_all.items():
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        bi = bucket_index(d)
        if bi is None:
            continue
        for code, mins in day.items():
            time_bucketed[code][bi] += mins

    def time_sort(code):
        if code in TIME_PRIORITY:
            return (0, TIME_PRIORITY.index(code))
        if code == "睡觉":
            return (2, 0)
        return (1, -sum(time_bucketed[code]))
    time_codes = sorted([c for c in time_bucketed if any(time_bucketed[c])], key=time_sort)
    time_datasets = [{
        "label": c, "data": time_bucketed[c],
        "backgroundColor": PROJECT_COLORS.get(c, "#424242"),
    } for c in time_codes]

    entries_bucketed = defaultdict(lambda: [0] * n_buckets)
    for dstr, day in toggl_counts.items():
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        bi = bucket_index(d)
        if bi is None:
            continue
        for code, cnt in day.items():
            entries_bucketed[code][bi] += cnt
    entry_codes_sorted = [c for c in time_codes if c in entries_bucketed] + \
        [c for c in entries_bucketed if c not in time_codes]
    entries_datasets = [{
        "label": c, "data": entries_bucketed[c],
        "backgroundColor": PROJECT_COLORS.get(c, "#424242"),
    } for c in entry_codes_sorted if any(entries_bucketed[c])]
    time_entries_values = [sum(entries_bucketed[c][i] for c in entries_bucketed) for i in range(n_buckets)]

    # Tasks, bucketed and summed
    tasks_neon = [0] * n_buckets
    tasks_posthoc = [0] * n_buckets
    tasks_1n = [0] * n_buckets
    tasks_neg1n = [0] * n_buckets
    tasks_other = [0] * n_buckets
    for dstr, counts in tasks_all.items():
        try:
            d = date.fromisoformat(dstr)
        except Exception:
            continue
        bi = bucket_index(d)
        if bi is None or counts is None:
            continue
        tasks_neon[bi] += counts.get("neon", 0)
        tasks_posthoc[bi] += counts.get("posthoc", 0)
        tasks_1n[bi] += counts.get("one_n", 0)
        tasks_neg1n[bi] += counts.get("neg1n", 0)
        tasks_other[bi] += counts.get("other", 0)

    # Email (Project Bocking) — bucket the same per-day merge used by the
    # daily builder (_build_email_by_account) into weekly/monthly buckets via
    # _bucket_email_by_account, then build Chart.js datasets with the SAME
    # function the daily builder uses (it only cares that labels/keys line up).
    daily_dates = [(bucket_starts[0] + timedelta(days=i)).isoformat() for i in range(days_span)]
    email_raw = load_email_data()
    email_by_account_daily = _build_email_by_account(email_raw, daily_dates)
    email_by_account_bucketed = _bucket_email_by_account(
        email_by_account_daily, lambda dstr: bucket_index(date.fromisoformat(dstr)), bucket_labels)
    email_datasets = _build_email_datasets(email_by_account_bucketed, bucket_labels)

    return {
        "dates": bucket_labels,
        "points": {"datasets": points_datasets},
        "time": {"datasets": time_datasets},
        "tasks_neon": tasks_neon,
        "tasks_posthoc": tasks_posthoc,
        "tasks_1n": tasks_1n,
        "tasks_neg1n": tasks_neg1n,
        "tasks_other": tasks_other,
        "time_entries": time_entries_values,
        "entries": {"datasets": entries_datasets},
        "email": {"datasets": email_datasets},
    }


# 5-min TTL cache for /api/weekly
_WEEKLY_CACHE: dict = {"payload": None, "ts": 0.0}
_WEEKLY_LOCK = threading.Lock()


def _weekly_cached():
    now = time.time()
    with _WEEKLY_LOCK:
        if _WEEKLY_CACHE["payload"] is not None and (now - _WEEKLY_CACHE["ts"]) < 300.0:
            return _WEEKLY_CACHE["payload"]
    payload = _build_weekly_data()
    with _WEEKLY_LOCK:
        _WEEKLY_CACHE["payload"] = payload
        _WEEKLY_CACHE["ts"] = time.time()
    return payload


_TASKS_CACHE_PATH = Path(__file__).parent / ".tasks-cache.json"
# Always refetch today + yesterday (late-evening completions can shift buckets).
# Days older than this are immutable — read from disk cache only.
_TASKS_REFETCH_TAIL = 2


def _fetch_tasks_for_day(day, token):
    """Fetch one day's completed-task counts from Todoist. Returns (date_str, counts) or (date_str, None).

    counts also carries counts["by_block"]: {branch: {"neon":n, "posthoc":n,
    "one_n":n, "neg1n":n, "other":n}}, bucketed from each completion's
    completed_at timestamp into the BRANCH_BLOCKS 2-hour window (skipped if
    it falls in the 22:00-04:00 sleep gap) — read alongside the day-level
    counts above since both come from the same API response.
    """
    since_dt = datetime.combine(day, datetime.min.time(), tzinfo=PACIFIC).astimezone(timezone.utc)
    until_dt = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=PACIFIC).astimezone(timezone.utc)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    until = until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"https://api.todoist.com/api/v1/tasks/completed?since={since}&until={until}&limit=200"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    counts = {"neon": 0, "posthoc": 0, "one_n": 0, "neg1n": 0, "other": 0}
    by_block = {branch: {"neon": 0, "posthoc": 0, "one_n": 0, "neg1n": 0, "other": 0}
                for branch, _, _ in BRANCH_BLOCKS}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return day.isoformat(), None
    for item in data.get("items", []):
        content = item.get("content", "")
        if "@0neon" in content:
            cat = "neon"
        elif "@posthoc" in content:
            cat = "posthoc"
        elif "@-1neon" in content:
            cat = "neg1n"
        elif "@1neon" in content:
            cat = "one_n"
        else:
            cat = "other"
        counts[cat] += 1
        completed_str = item.get("completed_at", "")
        if completed_str:
            try:
                completed_dt = datetime.fromisoformat(
                    completed_str.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
                bi = _block_index_for_hour(completed_dt.hour)
                if bi is not None:
                    by_block[BRANCH_BLOCKS[bi][0]][cat] += 1
            except (ValueError, TypeError):
                pass
    counts["total"] = (counts["neon"] + counts["posthoc"] + counts["one_n"]
                        + counts["neg1n"] + counts["other"])
    counts["by_block"] = by_block
    return day.isoformat(), counts


def load_tasks_data(n_days=DAYS):
    """Fetch completed tasks from Todoist, split by category tag in content.
    Returns {date_str: {"neon", "posthoc", "one_n", "neg1n", "other", "total"}}.

    Performance:
    - Historical days (older than today/yesterday) are read from a disk cache
      and never refetched (they're immutable).
    - Today + yesterday are always refetched in parallel.
    - Cold cache: parallel fetch of all n_days+1 days. The disk cache is
      shared across all callers regardless of n_days, so widening the window
      (e.g. for a monthly chart view) only pays the network cost once.
    """
    token = "7eb82f47aba8b334769351368e4e3e3284f980e5"
    today = date.today()
    all_days = [today - timedelta(days=n) for n in range(n_days, -1, -1)]
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


def load_tasks_by_block(n_days=GRANULAR_BLOCK_DAYS):
    """Completed-task counts for the trailing n_days, bucketed into
    BRANCH_BLOCKS via _fetch_tasks_for_day's "by_block" breakdown.

    Shares load_tasks_data's disk cache (.tasks-cache.json), but a cached day
    written before "by_block" existed won't have it — so unlike
    load_tasks_data's 2-day refetch tail, this refetches ANY day in the
    window missing the key, not just today/yesterday. Bounded cost since the
    per-block view only spans GRANULAR_BLOCK_DAYS days.
    """
    token = "7eb82f47aba8b334769351368e4e3e3284f980e5"
    today = date.today()
    day_list = [today - timedelta(days=n) for n in range(n_days - 1, -1, -1)]

    cache = {}
    if _TASKS_CACHE_PATH.exists():
        try:
            cache = json.loads(_TASKS_CACHE_PATH.read_text())
        except Exception:
            cache = {}

    to_fetch = [d for d in day_list
                if d.isoformat() not in cache or "by_block" not in cache[d.isoformat()]]
    if to_fetch:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(_fetch_tasks_for_day, d, token) for d in to_fetch]
            for fut in futures:
                day_str, counts = fut.result()
                if counts is not None:
                    cache[day_str] = counts
        try:
            _TASKS_CACHE_PATH.write_text(json.dumps(cache))
        except Exception:
            pass

    return {d.isoformat(): cache[d.isoformat()] for d in day_list if d.isoformat() in cache}


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


def _build_email_by_account(email_raw, dates):
    """Build {account: {date: {avg_hours, avg_hours_daytime, count,
    count_daytime, sent_count}}} for the given list of YYYY-MM-DD date
    strings, merging the response-time gist, imsg-responses.db, and
    archive_log.db (unified source of truth, overrides when it has more
    replies). Shared by the daily builder and the weekly/monthly granular
    builder (via _bucket_email_by_account) so the three-source merge logic
    lives in exactly one place."""
    email_daily = email_raw.get("daily", [])
    date_set = set(dates)
    email_by_account = defaultdict(dict)
    for entry in email_daily:
        acct = entry.get("account", "unknown")
        d = entry.get("date", "")
        if d and d in date_set:
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
                if day in date_set:
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
    # For each day in `dates`, replace per-type counts with archive_log data
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
    return email_by_account


def _build_email_by_account_blocked(email_raw, day_list):
    """Project Bocking at 2-hour 地支 block granularity.

    Feature (2026-07-21): previously the block-granularity chart had NO
    email/comms signal at all — every source _build_email_by_account reads
    (the gist's "daily" list, imsg-responses.db's daily_stats table) was
    pre-aggregated to daily granularity before being persisted, so there was
    no real per-block number to show. Two of those sources turned out to
    still have (or cheaply expose) real per-event timestamps:

    - gen_email_stats.py now also pushes a "by_block" key to the gist,
      bucketed from the same per-event recv_hour every producer already
      attaches to each reply (see build_block_stats() there) — covers
      gmail/outlook/teams/slack.
    - imsg-responses.db's response_pairs table already stores each pair's
      real recv_time (it's just daily_stats, read elsewhere, that discards
      the hour) — bucket that directly instead of adding a new table.
    - archive_log.db already has a raw per-message timestamp column; the
      day-level overlay in _build_email_by_account becomes a block-level
      overlay here using the same (day, branch) 2-hour window instead of a
      full day.

    Returns {account: {(date_iso, branch): {avg_hours, count,
    avg_hours_daytime, count_daytime, sent_count}}}. No per-block sent_count
    exists anywhere upstream (only day-level sent totals are tracked), so
    sent_count == count here — the "sent without reply" dark bars the daily
    view shows are intentionally absent at block granularity rather than
    faked.
    """
    day_set = {d.isoformat() for d in day_list}
    by_acct_bucket = defaultdict(dict)

    # gist by_block (gmail/outlook/teams/slack, iMessage overlaid below)
    for acct, day_map in (email_raw.get("by_block") or {}).items():
        for dstr, branch_map in day_map.items():
            if dstr not in day_set:
                continue
            for branch, entry in branch_map.items():
                count = entry.get("count", 0)
                by_acct_bucket[acct][(dstr, branch)] = {
                    "avg_hours": entry.get("avg_hours"),
                    "avg_hours_daytime": entry.get("avg_hours_daytime"),
                    "count": count,
                    "count_daytime": entry.get("count_daytime", 0),
                    "sent_count": count,
                }

    # iMessage — bucket response_pairs' real recv_time directly (mirrors the
    # daily view's choice to prefer the local db over the gist for imessage).
    import sqlite3 as _sq3
    _imsg_db = Path.home() / "vault" / "i447" / "i446" / "imsg-responses.db"
    if _imsg_db.exists() and day_list:
        try:
            _conn = _sq3.connect(f"file:{_imsg_db}?mode=ro", uri=True)
            lo, hi = min(day_list).isoformat(), max(day_list).isoformat()
            _rows = _conn.execute(
                "SELECT recv_time, response_hours FROM response_pairs "
                "WHERE day >= ? AND day <= ?", (lo, hi),
            ).fetchall()
            _conn.close()
            slots = defaultdict(list)
            for recv_time, hours in _rows:
                try:
                    recv_dt = datetime.fromisoformat(recv_time)
                except (ValueError, TypeError):
                    continue
                day_iso = recv_dt.date().isoformat()
                if day_iso not in day_set:
                    continue
                bi = _block_index_for_hour(recv_dt.hour)
                if bi is None:
                    continue
                branch = BRANCH_BLOCKS[bi][0]
                slots[(day_iso, branch)].append(hours)
            for key, hours_list in slots.items():
                count = len(hours_list)
                by_acct_bucket["imessage"][key] = {
                    "avg_hours": round(sum(hours_list) / count, 2) if count else None,
                    "avg_hours_daytime": None,
                    "count": count,
                    "count_daytime": 0,
                    "sent_count": count,
                }
        except Exception:
            pass

    # archive_log.db overlay — same source _build_email_by_account uses for
    # the daily view, same "override only if it has more replies" rule, just
    # windowed to each 2-hour block instead of the full day.
    _archive_db = Path.home() / ".config" / "ibx" / "archive_log.db"
    if _archive_db.exists() and day_list:
        try:
            _aconn = _sq3.connect(f"file:{_archive_db}?mode=ro", uri=True)
            _TYPE_TO_ACCT = {
                "email": "m5x2 gmail", "outlook": "outlook",
                "teams": "teams", "slack": "slack", "imsg": "imessage",
            }
            for d in day_list:
                for branch, start_hour, _ in BRANCH_BLOCKS:
                    block_start = datetime(d.year, d.month, d.day, start_hour, 0, tzinfo=LOCAL_TZ)
                    block_end = block_start + timedelta(hours=2)
                    rows = _aconn.execute(
                        "SELECT message_type, COUNT(DISTINCT item_uid), AVG(response_min) "
                        "FROM archive_log WHERE timestamp >= ? AND timestamp < ? "
                        "AND action = 'reply' AND response_min IS NOT NULL "
                        "GROUP BY message_type",
                        (block_start.timestamp(), block_end.timestamp()),
                    ).fetchall()
                    for msg_type, count, avg_min in rows:
                        acct = _TYPE_TO_ACCT.get(msg_type)
                        if not acct or count == 0:
                            continue
                        key = (d.isoformat(), branch)
                        existing = by_acct_bucket[acct].get(key, {})
                        if count >= existing.get("count", 0):
                            avg_h = avg_min / 60.0 if avg_min else None
                            by_acct_bucket[acct][key] = {
                                "avg_hours": avg_h, "avg_hours_daytime": avg_h,
                                "count": count, "count_daytime": count, "sent_count": count,
                            }
            _aconn.close()
        except Exception:
            pass

    return dict(by_acct_bucket)


def _bucket_email_by_account(email_by_account, bucket_index_fn, bucket_labels):
    """Fold a per-day email_by_account (see _build_email_by_account) into
    per-bucket totals, keyed by bucket LABEL — the result has the exact same
    {account: {key: {...}}} shape as the daily version, so
    _build_email_datasets builds Chart.js datasets from either unchanged.
    Response-time hours are count-weighted averages per bucket; counts and
    sent_counts are summed."""
    n = len(bucket_labels)
    acc = defaultdict(lambda: [{"h": 0.0, "c": 0, "h_dt": 0.0, "c_dt": 0, "sent": 0}
                               for _ in range(n)])
    for acct, day_map in email_by_account.items():
        for dstr, entry in day_map.items():
            bi = bucket_index_fn(dstr)
            if bi is None:
                continue
            slot = acc[acct][bi]
            h, c = entry.get("avg_hours"), entry.get("count", 0)
            if h is not None and c > 0:
                slot["h"] += h * c
                slot["c"] += c
            h_dt, c_dt = entry.get("avg_hours_daytime"), entry.get("count_daytime", 0)
            if h_dt is not None and c_dt > 0:
                slot["h_dt"] += h_dt * c_dt
                slot["c_dt"] += c_dt
            slot["sent"] += entry.get("sent_count", 0) or 0

    bucketed = defaultdict(dict)
    for acct, slots in acc.items():
        for bi, slot in enumerate(slots):
            if slot["c"] == 0 and slot["c_dt"] == 0 and slot["sent"] == 0:
                continue
            bucketed[acct][bucket_labels[bi]] = {
                "avg_hours": round(slot["h"] / slot["c"], 3) if slot["c"] > 0 else None,
                "avg_hours_daytime": round(slot["h_dt"] / slot["c_dt"], 3) if slot["c_dt"] > 0 else None,
                "count": slot["c"],
                "count_daytime": slot["c_dt"],
                "sent_count": slot["sent"],
            }
    return bucketed


def _build_email_datasets(email_by_account, labels):
    """Chart.js datasets (blended response-time lines + per-account stacked
    inbound/outbound count bars) from an email_by_account dict keyed by
    `labels` — raw daily YYYY-MM-DD dates OR weekly/monthly bucket labels,
    same shape either way (see _bucket_email_by_account)."""
    EMAIL_BAR_COLORS = {
        "m5x2 gmail": "#d5003266", "m5x2": "#d5003266",
        "s897 gmail": "#1b5e2066", "personal": "#1b5e2066", "gmail": "#1b5e2066",
        "imessage": "#34c75966",
        "slack": "#9b002366",
        "outlook": "#00b8d466",
        "teams": "#1249b466",
    }
    # Compute blended avg response time (minutes), weighted by reply pair count
    blended_response = []
    blended_daytime = []
    for label in labels:
        total_hours = 0
        total_count = 0
        total_hours_dt = 0
        total_count_dt = 0
        for acct, day_map in email_by_account.items():
            entry = day_map.get(label, {})
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
            "data": [day_map.get(label, {}).get("count", 0) for label in labels],
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
                "data": [day_map.get(label, {}).get("count", 0) for label in labels],
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
            "data": [max(0, day_map.get(label, {}).get("sent_count", 0) - day_map.get(label, {}).get("count", 0)) for label in labels],
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
                "data": [max(0, day_map.get(label, {}).get("sent_count", 0) - day_map.get(label, {}).get("count", 0)) for label in labels],
                "backgroundColor": EMAIL_BAR_COLORS_DARK.get(acct, "#aaaaaaaa"),
                "borderWidth": 0,
                "yAxisID": "y2",
                "stack": "outbound",
            })
    return email_datasets


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


@app.route("/api/points-today")
def api_points_today():
    """Live read of 0分 column D for today's row (today's #points total)."""
    today = date.today()
    try:
        import xlwings as xw
        wb = xw.Book(str(NEON_PATH))
        ws = wb.sheets["0分"]
        last_row = ws.range("B2").end("down").row
        # Scan from bottom up — today's row is near the end.
        b_vals = ws.range(f"B3:B{last_row}").value
        if not isinstance(b_vals, list):
            b_vals = [b_vals]
        target_row = None
        for i in range(len(b_vals) - 1, -1, -1):
            b = b_vals[i]
            d = None
            if isinstance(b, datetime):
                d = b.date()
            elif isinstance(b, date):
                d = b
            if d == today:
                target_row = i + 3
                break
        if target_row is None:
            return jsonify({"value": None})
        v = ws.range(f"D{target_row}").value
        if isinstance(v, (int, float)):
            return jsonify({"value": round(float(v), 1)})
        return jsonify({"value": None})
    except Exception as e:
        return jsonify({"value": None, "error": str(e)})


@app.route("/api/chart-granular")
def api_chart_granular():
    granularity = request.args.get("granularity", "weekly")
    if granularity not in ("weekly", "monthly", "block"):
        granularity = "weekly"
    return jsonify(_granular_cached(granularity))


@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    """Invalidate the data cache so the next /api/data fetch is fresh."""
    with _API_DATA_LOCK:
        _API_DATA_CACHE["payload"] = None
        _API_DATA_CACHE["ts"] = 0.0
    with _WEEKLY_LOCK:
        _WEEKLY_CACHE["payload"] = None
        _WEEKLY_CACHE["ts"] = 0.0
    with _GRANULAR_LOCK:
        _GRANULAR_CACHE.clear()
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


# 5-min TTL cache for /api/chart-granular, keyed by granularity
_GRANULAR_CACHE: dict = {}
_GRANULAR_LOCK = threading.Lock()
_GRANULAR_TTL = 300.0


def _granular_cached(granularity):
    now = time.time()
    with _GRANULAR_LOCK:
        entry = _GRANULAR_CACHE.get(granularity)
        if entry is not None and (now - entry["ts"]) < _GRANULAR_TTL:
            return entry["payload"]
    payload = _build_granular_chart_data(granularity)
    with _GRANULAR_LOCK:
        _GRANULAR_CACHE[granularity] = {"payload": payload, "ts": time.time()}
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
    toggl_result = raw["toggl"]
    if isinstance(toggl_result, tuple):
        toggl_raw, toggl_entry_counts = toggl_result
    else:
        toggl_raw, toggl_entry_counts = toggl_result, {}
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
    tasks_neg1n   = [tasks_raw.get(d, {}).get("neg1n", 0)   for d in dates]
    tasks_other   = [tasks_raw.get(d, {}).get("other", 0)   for d in dates]
    tasks_values  = [tasks_raw.get(d, {}).get("total", 0)   for d in dates]
    time_entries_values = [sum(toggl_entry_counts.get(d, {}).values()) for d in dates]

    # Build stacked entry-count datasets (same project order as time chart)
    entry_projects = {code for day_data in toggl_entry_counts.values() for code in day_data}
    entry_projects_sorted = [p for p in all_projects if p in entry_projects]
    entries_datasets = []
    for code in entry_projects_sorted:
        values = [toggl_entry_counts.get(d, {}).get(code, 0) for d in dates]
        if any(v > 0 for v in values):
            entries_datasets.append({
                "label": code,
                "data": values,
                "backgroundColor": PROJECT_COLORS.get(code, "#424242"),
            })

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
    # one bar per account (email count, secondary y-axis). Merge logic lives
    # in _build_email_by_account (shared with the weekly/monthly granular
    # builder via _bucket_email_by_account) so it's written exactly once.
    email_summary = email_raw.get("summary", {})
    email_by_account = _build_email_by_account(email_raw, dates)
    email_datasets = _build_email_datasets(email_by_account, dates)

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
        "tasks_neg1n": tasks_neg1n,
        "tasks_other": tasks_other,
        "time_entries": time_entries_values,
        "entries": {"datasets": entries_datasets},
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
  <div style="display:flex;align-items:baseline;gap:12px;">
    <h1>JM DASH</h1>
    <select id="granularitySelect" style="background:var(--card);color:var(--h1);border:1px solid var(--nav);border-radius:4px;padding:3px 8px;font-size:12px;letter-spacing:1px;font-family:inherit;">
      <option value="daily" selected>DAILY</option>
      <option value="weekly">WEEKLY</option>
      <option value="monthly">MONTHLY</option>
      <option value="block">BLOCK</option>
    </select>
  </div>
  <div style="display:flex;align-items:baseline;gap:16px;">
    <div id="ptsToday" style="font-size:14px;color:var(--h1);letter-spacing:1px;font-variant-numeric:tabular-nums;">分 <span id="ptsTodayVal" style="color:var(--text);font-weight:600;">…</span></div>
    <div style="font-size:14px;color:var(--h1);letter-spacing:1px;font-variant-numeric:tabular-nums;">Oct 2 '27: <span id="daysLeftVal" style="color:var(--text);font-weight:600;"></span><script>document.getElementById('daysLeftVal').textContent=Math.ceil((new Date('2027-10-02')-new Date())/864e5)+'d';</script></div>
    <a class="nav-link" href="/more">MORE →</a>
  </div>
</div>
<div class="grid">
  <div class="card">
    <h2>Cache · Q2</h2>
    <div id="cacheBars" class="cache-bars"></div>
  </div>
  <div class="card">
    <h2>Project Bocking — Comms Response Time</h2>
    <div class="chart-wrap sm"><canvas id="emailChart"></canvas></div>
    <div class="summary" id="emailSummary"></div>
  </div>
  <div class="card">
    <h2 id="tasksEntriesLabel">Tasks &amp; Entries / Day</h2>
    <div class="chart-wrap xs"><canvas id="tasksChart"></canvas></div>
    <div class="summary" id="tasksSummary"></div>
    <div class="chart-wrap xs"><canvas id="entriesChart"></canvas></div>
    <div class="summary" id="entriesSummary"></div>
  </div>
  <div class="card">
    <h2 id="timeLabel">Time / Day</h2>
    <div class="chart-wrap"><canvas id="timeChart"></canvas></div>
    <div class="summary" id="timeSummary"></div>
  </div>
  <div class="card">
    <h2 id="pointsLabel">Points / Day</h2>
    <div class="chart-wrap"><canvas id="pointsChart"></canvas></div>
    <div class="summary" id="pointsSummary"></div>
  </div>
</div>

<script>
""" + _SHARED_JS_HEAD + """
// Live #points-today poller (0分 col D)
function pollPointsToday() {
  fetch('/api/points-today').then(r => r.json()).then(d => {
    const el = document.getElementById('ptsTodayVal');
    if (!el) return;
    el.textContent = (d && d.value != null) ? Math.round(d.value) : '—';
  }).catch(() => {});
}
pollPointsToday();
setInterval(pollPointsToday, 30000);

// Task/Entries/Time/Points charts — shared renderer for daily/weekly/monthly.
// Daily granularity reuses the /api/data payload already on the page (cached
// in dailyPayload); weekly/monthly fetch /api/chart-granular on demand.
let dailyPayload = null;
let _tasksChart = null, _entriesChart = null, _timeChart = null, _pointsChart = null, _emailChart = null;
const GRANULARITY_NOUN = { daily: 'Day', weekly: 'Week', monthly: 'Month', block: 'Block' };

function renderFourCharts(data, granularity) {
  const labels = data.dates;
  const isDaily = granularity === 'daily';
  const noun = GRANULARITY_NOUN[granularity] || 'Day';
  document.getElementById('tasksEntriesLabel').textContent = `Tasks & Entries / ${noun}`;
  document.getElementById('timeLabel').textContent = `Time / ${noun}`;
  document.getElementById('pointsLabel').textContent = `Points / ${noun}`;

  if (_pointsChart) _pointsChart.destroy();
  _pointsChart = new Chart(document.getElementById('pointsChart'), {
    type: 'bar',
    data: { labels, datasets: data.points.datasets },
    options: isDaily ? {
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
    } : CHART_DEFAULTS
  });

  if (_timeChart) _timeChart.destroy();
  const isBlock = granularity === 'block';
  _timeChart = new Chart(document.getElementById('timeChart'), {
    type: 'bar',
    data: { labels, datasets: data.time.datasets },
    options: isDaily
      ? { ...CHART_DEFAULTS, scales: { ...CHART_DEFAULTS.scales, y: { ...CHART_DEFAULTS.scales.y, max: 1450 } } }
      : isBlock
      ? { ...CHART_DEFAULTS, scales: { ...CHART_DEFAULTS.scales, y: {
          ...CHART_DEFAULTS.scales.y, max: 120,
          ticks: { ...CHART_DEFAULTS.scales.y.ticks, stepSize: 30, callback: v => [0,30,60,90,120].includes(v) ? v : '' }
        } } }
      : CHART_DEFAULTS
  });

  const stackedOpts = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
      y: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } }
    }
  };

  if (_tasksChart) _tasksChart.destroy();
  const taskSeries = [
    { label: '0₦', data: data.tasks_neon,    bg: '#0a0a0a' },
    { label: 'posthoc', data: data.tasks_posthoc, bg: '#7c4dff' },
    { label: '1₦', data: data.tasks_1n,      bg: '#00e676' },
    { label: '-1₦', data: data.tasks_neg1n,  bg: '#e53935' },
    { label: 't779', data: data.tasks_other, bg: '#2979ff' },
  ];
  _tasksChart = new Chart(document.getElementById('tasksChart'), {
    type: 'bar',
    data: { labels, datasets: taskSeries.map(s => ({
      label: s.label, data: s.data,
      backgroundColor: s.bg, borderColor: s.bg, borderWidth: 0,
    }))},
    options: stackedOpts
  });
  const tkEl = document.getElementById('tasksSummary');
  tkEl.innerHTML = '';
  taskSeries.forEach(s => {
    const b = document.createElement('div');
    b.className = 'badge';
    b.innerHTML = `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${s.bg};margin-right:4px;vertical-align:middle;"></span>${s.label}`;
    tkEl.appendChild(b);
  });

  if (_entriesChart) _entriesChart.destroy();
  _entriesChart = new Chart(document.getElementById('entriesChart'), {
    type: 'bar',
    data: { labels, datasets: data.entries.datasets.map(ds => ({
      label: ds.label, data: ds.data,
      backgroundColor: ds.backgroundColor, borderColor: ds.backgroundColor, borderWidth: 0,
    }))},
    options: stackedOpts
  });
  const enEl = document.getElementById('entriesSummary');
  enEl.innerHTML = '';
  const enTotal = (data.time_entries || []).reduce((a,b) => a+b, 0);
  const enBuckets = (data.time_entries || []).filter(v => v > 0).length;
  enEl.innerHTML = `<span class="badge">${enTotal} entries / ${enBuckets > 0 ? Math.round(enTotal/enBuckets) : 0} avg</span>`;

  // Points/time summary badges — summed from datasets directly, so this works
  // identically regardless of granularity (daily payload's separate
  // `summary.total_points/total_mins` field is daily-only and not reused here).
  const ptEl = document.getElementById('pointsSummary');
  ptEl.innerHTML = '';
  (data.points.datasets || [])
    .map(ds => [ds.label, (ds.data || []).reduce((a,b) => a+(b||0), 0), ds.backgroundColor])
    .sort((a,b) => b[1]-a[1])
    .forEach(([k,v,color]) => {
      const b = document.createElement('div');
      b.className = 'badge';
      const dot = color ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:4px;vertical-align:middle;"></span>` : '';
      b.innerHTML = `${dot}${k} <span>${Math.round(v)}</span>`;
      ptEl.appendChild(b);
    });
  const tmEl = document.getElementById('timeSummary');
  tmEl.innerHTML = '';
  (data.time.datasets || [])
    .map(ds => [ds.label, (ds.data || []).reduce((a,b) => a+(b||0), 0), ds.backgroundColor])
    .sort((a,b) => b[1]-a[1])
    .forEach(([k,v,color]) => {
      const b = document.createElement('div');
      b.className = 'badge';
      const dot = color ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:4px;vertical-align:middle;"></span>` : '';
      b.innerHTML = `${dot}${k} <span>${Math.round(v)}m</span>`;
      tmEl.appendChild(b);
    });
}

const _granularCache = {};
document.getElementById('granularitySelect').addEventListener('change', (e) => {
  const g = e.target.value;
  if (g === 'daily') {
    if (dailyPayload) { renderFourCharts(dailyPayload, 'daily'); renderEmailChart(dailyPayload); }
    return;
  }
  if (_granularCache[g]) {
    renderFourCharts(_granularCache[g], g);
    renderEmailChart(_granularCache[g]);
    return;
  }
  fetch(`/api/chart-granular?granularity=${g}`).then(r => r.json()).then(d => {
    _granularCache[g] = d;
    renderFourCharts(d, g);
    renderEmailChart(d);
  });
});

fetch('/api/data').then(r => r.json()).then(data => {
  dailyPayload = data;
  const labels = data.dates;

  // Cache bars (Q2 cumulative + or - 分 by area)
  // Bar width is normalized to ±200; values beyond that pin to full width
  // while the numeric label still shows the true value.
  const cache = data.cache || [];
  const maxAbs = 200;
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

  renderFourCharts(data, 'daily');
  renderEmailChart(data);
});

// Email chart (Project Bocking): blended response-time line (purple) +
// per-account count bars (stacked). At block granularity data.email is
// built from real per-event recv_hour/recv_time timestamps (see
// _build_email_by_account_blocked in dashboard.py) rather than being
// daily-aggregate only — it's still absent if none of those sources had any
// reply in the trailing window, in which case fall back to a note instead
// of a misleading empty chart.
function renderEmailChart(data) {
  const canvas = document.getElementById('emailChart');
  const emEl = document.getElementById('emailSummary');
  if (_emailChart) { _emailChart.destroy(); _emailChart = null; }
  emEl.innerHTML = '';

  if (!data.email) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    emEl.innerHTML = '<div class="badge">no comms data for this window</div>';
    return;
  }

  _emailChart = new Chart(canvas, {
    type: 'bar',
    data: { labels: data.dates, datasets: data.email.datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID } },
        y:  { type: 'logarithmic', position: 'right', min: 10, max: 1440,
              ticks: { color: TICK, font: { size: 10 }, callback: v => [10,30,60,120,300,720,1440].includes(v) ? v+'m' : '' },
              afterBuildTicks: axis => { axis.ticks = [10,30,60,120,300,720,1440].map(v => ({value:v})); },
              grid: { color: GRID }, title: { display: true, text: 'min', color: TICK, font: { size: 10 } } },
        y2: { position: 'left', min: 0, stacked: true, ticks: { color: TICK, font: { size: 10 } }, grid: { display: false }, title: { display: true, text: 'msgs', color: TICK, font: { size: 10 } } },
      }
    }
  });

  // Email legend (same style as Points/Day)
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
}
</script>

<div style="text-align:center; margin:24px 0 12px; font-size:13px; color:var(--h2);">
  <a href="/1s" style="color:var(--h2); text-decoration:none; border-bottom:1px dotted var(--h2);">→ 1s weekly</a>
  &nbsp;·&nbsp;
  <a href="http://ix:5555" style="color:var(--h2); text-decoration:none; border-bottom:1px dotted var(--h2);">→ jm-ai-dash</a>
  &nbsp;·&nbsp;
  <a href="http://ix:5556" style="color:var(--h2); text-decoration:none; border-bottom:1px dotted var(--h2);">→ AI Dashboard (m5x2)</a>
</div>

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

<div style="text-align:center; margin:24px 0 12px; font-size:13px; color:var(--muted);">
  <a href="/1s" style="color:var(--muted); text-decoration:none; border-bottom:1px dotted var(--muted);">→ 1s weekly</a>
  &nbsp;·&nbsp;
  <a href="http://ix:5555" style="color:var(--muted); text-decoration:none; border-bottom:1px dotted var(--muted);">→ jm-ai-dash</a>
  &nbsp;·&nbsp;
  <a href="http://ix:5556" style="color:var(--muted); text-decoration:none; border-bottom:1px dotted var(--muted);">→ AI Dashboard (m5x2)</a>
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


WEEKLY_HTML = """<!DOCTYPE html>
<html>
<head>
<title>1s · weekly</title>
""" + _SHARED_STYLE + """
</head>
<body>
<div class="topbar">
  <h1>1s WEEKLY</h1>
  <div style="display:flex;align-items:baseline;gap:16px;">
    <div id="cumHead" style="font-size:14px;color:var(--h1);letter-spacing:1px;font-variant-numeric:tabular-nums;">…</div>
    <a class="nav-link" href="/">← DASH</a>
  </div>
</div>
<div class="grid">
  <div class="card">
    <h2>Points / Week</h2>
    <div class="chart-wrap"><canvas id="pointsWeekChart"></canvas></div>
    <div class="summary" id="pointsWeekSummary"></div>
  </div>
  <div class="card">
    <h2>Time / Week (h)</h2>
    <div class="chart-wrap"><canvas id="timeWeekChart"></canvas></div>
    <div class="summary" id="timeWeekSummary"></div>
  </div>
  <div class="card" style="grid-column:1/-1;">
    <h2>Cumulative Points · This Week vs Last (Su–Sa)</h2>
    <div class="chart-wrap"><canvas id="cumChart"></canvas></div>
    <div class="summary" id="cumSummary"></div>
  </div>
</div>

<div style="text-align:center; margin:24px 0 12px; font-size:13px; color:var(--h2);">
  <a href="/" style="color:var(--h2); text-decoration:none; border-bottom:1px dotted var(--h2);">← jm dash</a>
  &nbsp;·&nbsp;
  <a href="http://ix:5555" style="color:var(--h2); text-decoration:none; border-bottom:1px dotted var(--h2);">→ jm-ai-dash</a>
  &nbsp;·&nbsp;
  <a href="http://ix:5556" style="color:var(--h2); text-decoration:none; border-bottom:1px dotted var(--h2);">→ AI Dashboard (m5x2)</a>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
""" + _SHARED_JS_HEAD + """
function stackedOpts(unit) {
  const o = JSON.parse(JSON.stringify(CHART_DEFAULTS));
  o.plugins.tooltip = { mode: 'index', intersect: false };
  return o;
}

fetch('/api/weekly').then(r => r.json()).then(d => {
  // Points / Week
  new Chart(document.getElementById('pointsWeekChart'), {
    type: 'bar',
    data: { labels: d.weeks, datasets: d.points_week.datasets },
    options: stackedOpts('pts')
  });
  document.getElementById('pointsWeekSummary').textContent =
    d.points_week.datasets.map(s => s.label).join(' · ');

  // Time / Week
  new Chart(document.getElementById('timeWeekChart'), {
    type: 'bar',
    data: { labels: d.weeks, datasets: d.time_week.datasets },
    options: stackedOpts('h')
  });
  document.getElementById('timeWeekSummary').textContent =
    d.time_week.datasets.map(s => s.label).join(' · ');

  // Cumulative this vs last
  const c = d.cumulative;
  new Chart(document.getElementById('cumChart'), {
    type: 'bar',
    data: { labels: c.labels, datasets: c.datasets },
    options: stackedOpts('pts')
  });
  const delta = c.this_total - c.last_to_date;
  const sign = delta >= 0 ? '+' : '';
  const word = delta >= 0 ? 'ahead' : 'behind';
  document.getElementById('cumHead').innerHTML =
    `<span style="color:var(--text);font-weight:600;">${c.this_total}</span> pts ` +
    `<span style="color:${delta>=0?'#00e676':'#fd6c1d'};">${sign}${delta} ${word}</span>`;
  document.getElementById('cumSummary').textContent =
    `This week ${c.this_total} · last week to-date ${c.last_to_date} · last week full ${c.last_full}. ` +
    `Faded bars = last week's cumulative shadow.`;
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


@app.route("/api/weekly")
def api_weekly():
    return jsonify(_weekly_cached())


@app.route("/1s")
def weekly_page():
    return render_template_string(WEEKLY_HTML)


@app.route("/more")
def more():
    return render_template_string(MORE_HTML)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5558, debug=False)
