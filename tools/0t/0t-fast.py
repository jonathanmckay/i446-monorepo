#!/usr/bin/env python3
"""0t-fast.py — Fast /0t: compute sleep, write to 0₦, refresh dashboard, mark done.

No donut chart. Instead refreshes the personal dashboard points cache
so the Points/Day and Time/Day charts stay current.

Usage:
    python3 0t-fast.py [YYYY-MM-DD]   # date = yesterday (default)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Imports from project
# ---------------------------------------------------------------------------

# ix_osa
_IX_PATH = Path.home() / ".claude/skills/_lib/ix-osa.py"
_IX_SPEC = importlib.util.spec_from_file_location("ix_osa", _IX_PATH)
_ix_mod = importlib.util.module_from_spec(_IX_SPEC)
sys.modules["ix_osa"] = _ix_mod
_IX_SPEC.loader.exec_module(_ix_mod)
ix_run = _ix_mod.run

# toggl — direct API calls (can't import toggl_api due to relative imports)
TOGGL_API_BASE = "https://api.track.toggl.com/api/v9"
SLEEP_PROJECT_ID = 108358083


def _load_toggl_key() -> str:
    """Load Toggl API key from env or ~/.claude.json MCP config."""
    key = os.environ.get("TOGGL_API_KEY", "")
    if key:
        return key
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        data = json.loads(claude_json.read_text())
        env = data.get("mcpServers", {}).get("toggl_server", {}).get("env", {})
        key = env.get("TOGGL_API_KEY", "")
        if key:
            return key
    return ""


TOGGL_API_KEY = _load_toggl_key()


def _toggl_get(path: str) -> list | dict:
    import base64
    url = f"{TOGGL_API_BASE}{path}"
    creds = base64.b64encode(f"{TOGGL_API_KEY}:api_token".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

# did-fast
DID_FAST = Path.home() / "i446-monorepo/tools/did/did-fast.py"

# Dashboard cache
DASHBOARD_DIR = Path.home() / "i446-monorepo/tools/personal-dashboard"
POINTS_CACHE = DASHBOARD_DIR / ".points-cache.json"
NEON_XLSX = Path.home() / "OneDrive/vault-excel/Neon分v12.2.xlsx"

# Sleep project name
SLEEP_PROJECT = "睡觉"


def get_toggl_entries(d: date) -> list[dict]:
    """Fetch raw Toggl entries for a date via API."""
    start = d.isoformat()
    end = (d + timedelta(days=1)).isoformat()
    return _toggl_get(f"/me/time_entries?start_date={start}&end_date={end}")


def compute_sleep(yesterday: date, today: date) -> int:
    """Compute last night's sleep = pre-midnight (>=20:00) + post-midnight (<14:00)."""
    yesterday_entries = get_toggl_entries(yesterday)
    today_entries = get_toggl_entries(today)

    pre_midnight = 0
    for e in yesterday_entries:
        if e.get("project_id") != SLEEP_PROJECT_ID:
            continue
        start_str = e.get("start", "")
        if not start_str or len(start_str) < 14:
            continue
        start_hour = int(start_str[11:13])
        if start_hour >= 20:
            dur = e.get("duration", 0)
            if dur > 0:
                pre_midnight += dur // 60

    post_midnight = 0
    for e in today_entries:
        if e.get("project_id") != SLEEP_PROJECT_ID:
            continue
        start_str = e.get("start", "")
        if not start_str or len(start_str) < 14:
            continue
        start_hour = int(start_str[11:13])
        if start_hour < 14:
            dur = e.get("duration", 0)
            if dur > 0:
                post_midnight += dur // 60
            elif dur < 0:
                # Running timer
                from datetime import timezone
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                post_midnight += int((now - start_dt).total_seconds()) // 60

    return pre_midnight + post_midnight


def write_sleep(sleep_minutes: int, today: date) -> str:
    """Write sleep minutes to 0₦ column D for today."""
    month = today.month
    day = today.day
    script = f'''tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = {month} and d = {day} then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {month}/{day} not found"
    set value of cell 4 of row todayRow of theSheet to {sleep_minutes}
    set writtenVal to value of cell 4 of row todayRow of theSheet
    return "OK: sleep=" & (writtenVal as text) & " row=" & todayRow
end tell'''
    res = ix_run(script, timeout=30.0)
    out = res.stdout.strip()
    if res.returncode != 0 or not out or out.startswith("ERROR"):
        raise RuntimeError(f"sleep write failed (rc={res.returncode}): {out or res.stderr.strip()}")
    return out


def refresh_points_cache() -> str:
    """Save Excel on Ix, wait for sync, rebuild points cache from openpyxl."""
    # Save workbook on Ix
    save_script = 'tell application "Microsoft Excel" to save workbook "Neon分v12.2.xlsx"'
    ix_run(save_script, timeout=15.0)

    # Brief wait for OneDrive sync
    import time
    time.sleep(3)

    # Read with openpyxl
    import openpyxl
    COLS = {16: '-1₦', 17: '0₲', 18: 'i9', 19: 'm5', 20: '个',
            21: '媒', 22: '思', 23: 'hcb', 24: 'xk', 25: '社'}
    today = date.today()
    cutoff = today - timedelta(days=90)

    wb = openpyxl.load_workbook(str(NEON_XLSX), data_only=True, read_only=True)
    ws = wb['0分']
    result = {}
    for row in ws.iter_rows(min_row=3, values_only=True):
        b = row[1]
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
        day_data = {}
        for idx, label in COLS.items():
            val = row[idx - 1]
            if val is not None and isinstance(val, (int, float)) and val > 0:
                day_data[label] = int(round(float(val)))
        if day_data:
            result[d.isoformat()] = day_data
    wb.close()

    POINTS_CACHE.write_text(json.dumps(result, indent=2) + "\n")
    return f"{len(result)} days"


def mark_done() -> dict:
    """Run did-fast.py to mark 0t done in 0₦ + Todoist."""
    proc = subprocess.run(
        ["python3", str(DID_FAST), "0t"],
        capture_output=True, text=True, timeout=45,
    )
    if proc.returncode == 0:
        return json.loads(proc.stdout)
    return {"error": proc.stderr.strip()}


def main():
    today = date.today()

    # Parse optional date arg
    if len(sys.argv) > 1:
        yesterday = date.fromisoformat(sys.argv[1])
    else:
        yesterday = today - timedelta(days=1)

    output = {"yesterday": yesterday.isoformat(), "today": today.isoformat()}

    # 1. Compute sleep
    sleep = compute_sleep(yesterday, today)
    output["sleep_minutes"] = sleep
    output["sleep_display"] = f"{sleep // 60}h {sleep % 60}m"

    # 2. Write sleep to 0₦
    failed = False
    try:
        sleep_result = write_sleep(sleep, today)
        output["sleep_write"] = sleep_result
    except RuntimeError as e:
        output["sleep_write"] = f"FAILED: {e}"
        failed = True

    # 3. Mark 0t done (0₦ + Todoist + stop timer)
    did_result = mark_done()
    output["did"] = did_result
    if "error" in did_result:
        failed = True

    # 4. Refresh dashboard points cache
    try:
        days = refresh_points_cache()
        output["dashboard"] = f"points cache refreshed ({days})"
    except Exception as e:
        output["dashboard"] = f"ERROR: {e}"

    print(json.dumps(output, ensure_ascii=False, indent=2))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
