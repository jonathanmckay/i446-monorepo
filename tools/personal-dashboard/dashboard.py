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

# ── Column mappings ────────────────────────────────────────────────────────────
# 0分 sheet: column index (1-based) → label + domain
POINTS_COLS = {
    25: {"label": "-1₦", "domain": None,    "color": "#9e9e9e"},  # Y
    26: {"label": "0₲",  "domain": None,    "color": "#616161"},  # Z
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
    "睡觉":  "#0a0a0a",  # Abyss
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
        # Exclude sleep from time chart
        if code == "睡觉":
            continue
        result[d.isoformat()][code] += dur // 60

    return {k: dict(v) for k, v in result.items()}


def load_tasks_data():
    """Fetch completed tasks from Todoist, return {date_str: count} for last DAYS days."""
    token = "7eb82f47aba8b334769351368e4e3e3284f980e5"
    today = date.today()
    since = (today - timedelta(days=DAYS)).strftime("%Y-%m-%dT00:00:00Z")

    result = defaultdict(int)
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
                result[d.isoformat()] += 1
            except (ValueError, TypeError):
                continue

        cursor = data.get("next_cursor")
        if not cursor:
            break

    return dict(result)


def load_turns_data():
    """Fetch pre-computed daily turns from ai-dashboard API (localhost:5555/api/turns).
    Falls back to empty if ai-dashboard is not running."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:5555/api/turns", timeout=5) as resp:
            entries = json.loads(resp.read())
        return {e["date"]: e["claude"] for e in entries if e.get("date")}
    except Exception:
        return {}


# ── Date range helper ──────────────────────────────────────────────────────────

def last_n_days(n=DAYS):
    today = date.today()
    return [(today - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/api/data")
def api_data():
    dates = last_n_days()

    points_raw = load_points_data()
    toggl_raw = load_toggl_data()
    turns_raw = load_turns_data()
    tasks_raw = load_tasks_data()

    # Build sorted label lists
    all_point_labels = [m["label"] for m in POINTS_COLS.values()]
    point_colors = {m["label"]: m["color"] for m in POINTS_COLS.values()}

    # Collect all project codes that appear in toggl data
    all_projects = sorted(
        {code for day_data in toggl_raw.values() for code in day_data},
        key=lambda c: -sum(v.get(c, 0) for v in toggl_raw.values())
    )

    # Build chart datasets
    points_datasets = []
    for label in all_point_labels:
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
    tasks_values = [tasks_raw.get(d, 0) for d in dates]

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
        "shots_per_task": shots_per_task,
        "ratio": {"datasets": ratio_datasets},
        "summary": {
            "total_points": {k: int(v) for k, v in total_points.items() if v > 0},
            "total_mins": {k: int(v) for k, v in total_time.items() if v > 0},
            "total_turns": sum(turns_values),
            "total_tasks": sum(tasks_values),
        }
    })


HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>jm dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #111; color: #eee; font-family: 'SF Mono', monospace; padding: 24px; }
h1 { font-size: 18px; color: #aaa; margin-bottom: 24px; letter-spacing: 2px; }
h2 { font-size: 13px; color: #666; margin-bottom: 12px; letter-spacing: 1px; text-transform: uppercase; }
.grid { display: grid; grid-template-columns: 1fr; gap: 32px; margin-bottom: 32px; }
.card { background: #1a1a1a; border-radius: 8px; padding: 20px; }
.chart-wrap { height: 280px; position: relative; }
.summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.badge { background: #222; border-radius: 4px; padding: 4px 10px; font-size: 12px; }
.badge span { color: #aaa; }
</style>
</head>
<body>
<h1>JM · PERSONAL DASHBOARD</h1>
<div class="grid">
  <div class="card">
    <h2>Points / Day</h2>
    <div class="chart-wrap"><canvas id="pointsChart"></canvas></div>
    <div class="summary" id="pointsSummary"></div>
  </div>
  <div class="card">
    <h2>Time / Day (min, excl. sleep)</h2>
    <div class="chart-wrap"><canvas id="timeChart"></canvas></div>
    <div class="summary" id="timeSummary"></div>
  </div>
  <div class="card">
    <h2>分 / min (7-day rolling) — xk · i9 · m5</h2>
    <div class="chart-wrap" style="height:200px"><canvas id="ratioChart"></canvas></div>
  </div>
  <div class="card">
    <h2>AI Turns / Day</h2>
    <div class="chart-wrap" style="height:180px"><canvas id="turnsChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Tasks Complete / Day</h2>
    <div class="chart-wrap" style="height:180px"><canvas id="tasksChart"></canvas></div>
  </div>
  <div class="card">
    <h2>Shots / Task (turns ÷ tasks, 7-day rolling)</h2>
    <div class="chart-wrap" style="height:180px"><canvas id="shotsChart"></canvas></div>
  </div>
</div>

<script>
const CHART_DEFAULTS = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    x: { stacked: true, ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } },
    y: { stacked: true, ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } }
  }
};

fetch('/api/data').then(r => r.json()).then(data => {
  const labels = data.dates;

  // Points chart
  new Chart(document.getElementById('pointsChart'), {
    type: 'bar',
    data: { labels, datasets: data.points.datasets },
    options: { ...CHART_DEFAULTS }
  });

  // Time chart
  new Chart(document.getElementById('timeChart'), {
    type: 'bar',
    data: { labels, datasets: data.time.datasets },
    options: { ...CHART_DEFAULTS }
  });

  // Ratio chart (line)
  new Chart(document.getElementById('ratioChart'), {
    type: 'line',
    data: { labels, datasets: data.ratio.datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: true, labels: { color: '#666', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } },
        y: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } }
      }
    }
  });

  // Turns chart (line)
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
        x: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } },
        y: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } }
      }
    }
  });

  // Tasks chart
  new Chart(document.getElementById('tasksChart'), {
    type: 'bar',
    data: { labels, datasets: [{
      label: 'tasks',
      data: data.tasks,
      backgroundColor: '#00e67644',
      borderColor: '#00e676',
      borderWidth: 1,
    }]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } },
        y: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } }
      }
    }
  });

  // Shots/task chart (7-day rolling average)
  const shotsRaw = data.shots_per_task;
  const rolling7 = shotsRaw.map((_, i) => {
    const window = shotsRaw.slice(Math.max(0, i - 6), i + 1).filter(v => v !== null);
    return window.length > 0 ? Math.round(window.reduce((a, b) => a + b, 0) / window.length * 10) / 10 : null;
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
        x: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } },
        y: { ticks: { color: '#555', font: { size: 10 } }, grid: { color: '#222' } }
      }
    }
  });

  // Summaries
  const s = data.summary;
  const ptEl = document.getElementById('pointsSummary');
  Object.entries(s.total_points).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => {
    const b = document.createElement('div');
    b.className = 'badge';
    b.innerHTML = `${k} <span>${v}</span>`;
    ptEl.appendChild(b);
  });
  const tmEl = document.getElementById('timeSummary');
  Object.entries(s.total_mins).sort((a,b) => b[1]-a[1]).forEach(([k,v]) => {
    const b = document.createElement('div');
    b.className = 'badge';
    b.innerHTML = `${k} <span>${v}m</span>`;
    tmEl.appendChild(b);
  });
});
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    app.run(port=5558, debug=False)
