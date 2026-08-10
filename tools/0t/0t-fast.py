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
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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

# toggl client-side throttle — shared (fcntl-locked) state file across every
# process that hits Toggl, so this standalone script can't burst past the others
# and trip the free-tier limit. Loaded by path (it has no relative imports).
_TH_PATH = Path.home() / "i446-monorepo/mcp/toggl_server/throttle.py"
_TH_SPEC = importlib.util.spec_from_file_location("toggl_throttle", _TH_PATH)
_throttle = importlib.util.module_from_spec(_TH_SPEC)
sys.modules["toggl_throttle"] = _throttle
_TH_SPEC.loader.exec_module(_throttle)

# toggl — direct API calls (can't import toggl_api due to relative imports)
import urllib.error  # noqa: E402
TOGGL_API_BASE = "https://api.track.toggl.com/api/v9"
SLEEP_PROJECT_ID = 108358083
HCMC_PROJECT_ID = 109932707

# 0分 writes go through the excel-http daemon (lib/neon/excel) so they land in
# the audit ledger — same ban as did-fast.py: raw AppleScript 0分 writes are
# prohibited. Other sheets (0n) still use ix_run above.
sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel as neon_excel  # noqa: E402


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
    _throttle.acquire()  # share the cross-process pacing + post-402 cooldown
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (402, 429):
            _throttle.note_rate_limit()
        raise

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


# Toggl returns timestamps in UTC; all hour/date logic must be done in local time.
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def entry_local_dt(e: dict) -> datetime | None:
    """Parse a Toggl entry's start into a LOCAL_TZ-aware datetime (None if unparseable)."""
    s = e.get("start", "")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LOCAL_TZ)


def gather_entries_local(*days: date) -> list[dict]:
    """Fetch entries across one or more UTC day-buckets and dedupe by id.

    A PDT evening entry lands in the *next* UTC bucket, so the overnight window
    must be reconstructed from both yesterday's and today's fetches.
    """
    seen: dict = {}
    for d in days:
        for e in get_toggl_entries(d):
            eid = e.get("id")
            if eid is not None:
                seen[eid] = e
    return list(seen.values())


# 0n columns by Toggl tag. Must match the live headers (AU=1+, AV=-1, AW=-2,
# AX=-3) and build-order-daemon.py's TOGGL_TAG_COLS. A "1+" column was inserted
# at AU, shifting -1→AV and -2→AW; keep these in sync to avoid writing -1 into
# the 1+ column (AU).
# AZ ("∑xk87") is NOT a tag target: it's a live =SUM(AJ:AO) formula aggregating
# the individual kid/family columns (xk20/xk22/xk26/qft/xk88/NVC+e). A prior
# "xk87": "AZ" entry here made this raw-set the cell, clobbering the formula
# with a Toggl-tag-derived total whenever an entry carried an "xk87" tag.
TAG_COLUMNS = {"-1": "AV", "-2": "AW", "其他人": "AS", "-3": "AX"}
PROJECT_COLUMNS = {}  # project_id → (name, 0n column)


def compute_tag_minutes(yesterday: date, today: date) -> tuple[dict[str, int], dict[str, int]]:
    """Sum minutes for tagged and project entries on the target day (yesterday) only."""
    yesterday_entries = get_toggl_entries(yesterday)
    tag_totals: dict[str, int] = {}
    proj_totals: dict[str, int] = {}
    for e in yesterday_entries:
        dur = e.get("duration", 0)
        if dur <= 0:
            continue
        # Sleep (睡觉) carries the "-3" tag but is tracked separately in column D.
        # Without this skip its minutes pollute the -3/AX tag column, which then
        # reads as ~a full night of sleep (regression 2026-06-28: AX=439).
        if e.get("project_id") == SLEEP_PROJECT_ID:
            continue
        minutes = dur // 60
        for tag in (e.get("tags") or []):
            if tag in TAG_COLUMNS:
                tag_totals[tag] = tag_totals.get(tag, 0) + minutes
        pid = e.get("project_id")
        if pid in PROJECT_COLUMNS:
            name = PROJECT_COLUMNS[pid][0]
            proj_totals[name] = proj_totals.get(name, 0) + minutes
    return tag_totals, proj_totals


def write_tag_minutes(tag_totals: dict[str, int], target_date: date,
                      proj_totals: dict[str, int] | None = None) -> str:
    """Write tag and project minute sums to 0n columns for the target date's row."""
    if not tag_totals and not proj_totals:
        return "no tagged entries"
    set_lines = []
    for tag, minutes in tag_totals.items():
        col = TAG_COLUMNS[tag]
        set_lines.append(f'    set value of range ("{col}" & todayRow) of theSheet to {minutes}')
    for name, minutes in (proj_totals or {}).items():
        col = next(c for pid, (n, c) in PROJECT_COLUMNS.items() if n == name)
        set_lines.append(f'    set value of range ("{col}" & todayRow) of theSheet to {minutes}')
    set_block = "\n".join(set_lines)
    month = target_date.month
    day = target_date.day
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
{set_block}
    return "OK: tags written row=" & todayRow
end tell'''
    res = ix_run(script, timeout=30.0)
    out = res.stdout.strip()
    if res.returncode != 0 or not out or out.startswith("ERROR"):
        raise RuntimeError(f"tag write failed (rc={res.returncode}): {out or res.stderr.strip()}")
    return out


def detect_night_hcmc(yesterday: date) -> int | None:
    """If an hcmc entry ends right when sleep begins (yesterday evening, local >=20:00),
    return its minutes. Times are evaluated in LOCAL_TZ; bedtime (e.g. 23:xx PDT) lands in
    the next UTC bucket, so both day-buckets are gathered."""
    today = yesterday + timedelta(days=1)
    timed = []
    for e in gather_entries_local(yesterday, today):
        if e.get("duration", 0) <= 0:
            continue
        ldt = entry_local_dt(e)
        if ldt is None:
            continue
        timed.append((ldt, e))
    timed.sort(key=lambda t: t[0])

    # Find the first sleep entry that begins yesterday evening (local date == yesterday, >=20:00)
    first_sleep_idx = None
    for i, (ldt, e) in enumerate(timed):
        if (e.get("project_id") == SLEEP_PROJECT_ID
                and ldt.date() == yesterday and ldt.hour >= 20):
            first_sleep_idx = i
            break

    if first_sleep_idx is None or first_sleep_idx == 0:
        return None

    # Check if the immediately preceding entry is hcmc
    _, prev = timed[first_sleep_idx - 1]
    if prev.get("project_id") != HCMC_PROJECT_ID:
        return None

    return prev.get("duration", 0) // 60


def mark_night_hcmc(minutes: int, target_date: date) -> dict:
    """Write auto-detected pre-sleep hcmc minutes to 0n "night hcmc" on the day
    the entry OCCURRED, and only into an EMPTY cell.

    Direct row-targeted write, not did-fast: did-fast's 0n path refuses past
    dates ("posthoc flow"), which is why the old call routed through `today` —
    and on 2026-07-24 a backfill run (`0t-fast.py 2026-07-22`) therefore
    stamped 7/22's detected 30min onto 7/24's row, clobbering a manual 1.
    The detection is a fallback; a manual /did night hcmc value always wins.
    """
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo"))
        from lib.neon import cols
        col = cols.maybe_col("0n", "night hcmc") or "P"
    except Exception:
        col = "P"
    script = f'''tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set targetRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = {target_date.month} and d = {target_date.day} then
                    set targetRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if targetRow = 0 then return "ERROR: date {target_date.month}/{target_date.day} not found"
    set prev to string value of range ("{col}" & targetRow) of theSheet
    if prev = "" or prev = "0" then
        set value of range ("{col}" & targetRow) of theSheet to {minutes}
        return "OK: night hcmc={minutes} row=" & targetRow
    end if
    return "SKIPPED: manual value " & prev & " kept, row=" & targetRow
end tell'''
    res = ix_run(script, timeout=30.0)
    out = res.stdout.strip()
    if res.returncode != 0 or not out or out.startswith("ERROR"):
        return {"error": out or res.stderr.strip()}
    return {"write": out}


# ── Sleep dock ───────────────────────────────────────────────────────────────
# 0.25 分 per minute still up past 22:00, capped at 30分/night, charged to hcb
# (0分 col W) on the day the night STARTED (`yesterday`), even for post-midnight
# bedtimes. "Up" ends at bedtime: the start of the contiguous fall-asleep → 睡觉
# chain — an earlier "fall asleep" attempt he got up from (e.g. 21:00 attempt,
# then youtube, then a 22:00 attempt that sticks) does not count.
DOCK_RATE = 0.25            # 分 docked per minute past DOCK_START_HOUR
DOCK_CAP = 30.0             # max 分 docked per night (binds at 2h past 22:00)
DOCK_START_HOUR = 22        # local time, on `yesterday`
DOCK_DESC = "fall asleep"   # Toggl description marking a sleep attempt
DOCK_CHAIN_GAP_MIN = 15     # max untracked gap (min) for chain contiguity
DOCK_COL_0FEN = "W"         # hcb column in 0分
DOCK_SRC = "0t sleep-dock"  # ledger src label; with the date, the idempotency key


def compute_sleep_dock(yesterday: date, sleep_date: date) -> dict | None:
    """Bedtime and dock for the night bridging yesterday → sleep_date.

    Bedtime = start of the first 睡觉 entry of the night, walked back through
    contiguous immediately-preceding "fall asleep" entries (gap <= 15 min).
    Mid-night wake-ups and morning naps can't inflate the dock: only entries
    before the night's first 睡觉 start are considered. Returns None when the
    night has no usable entries at all.
    """
    def in_window(ldt: datetime) -> bool:
        return ((ldt.date() == yesterday and ldt.hour >= 20)
                or (ldt.date() == sleep_date and ldt.hour < 14))

    falls: list[tuple[datetime, datetime]] = []
    sleeps: list[datetime] = []
    for e in gather_entries_local(yesterday, sleep_date):
        ldt = entry_local_dt(e)
        if ldt is None or not in_window(ldt):
            continue
        dur = e.get("duration", 0)
        end = ldt + timedelta(seconds=dur) if dur > 0 else ldt
        if e.get("project_id") == SLEEP_PROJECT_ID:
            sleeps.append(ldt)
        elif (e.get("description") or "").strip().lower() == DOCK_DESC:
            falls.append((ldt, end))

    if sleeps:
        bedtime = min(sleeps)
        basis = "睡觉"
        gap = timedelta(minutes=DOCK_CHAIN_GAP_MIN)
        changed = True
        while changed:
            changed = False
            for start, end in sorted(falls, reverse=True):
                if start < bedtime and end >= bedtime - gap:
                    bedtime = start
                    basis = DOCK_DESC
                    changed = True
    elif falls:
        # No 睡觉 tracked (data gap): the last attempt is the best signal.
        bedtime = max(s for s, _ in falls)
        basis = f"{DOCK_DESC} (no 睡觉 entry)"
    else:
        return None

    cutoff = datetime.combine(yesterday, dtime(hour=DOCK_START_HOUR), tzinfo=LOCAL_TZ)
    late = max(0, int((bedtime - cutoff).total_seconds() // 60))
    dock = min(late * DOCK_RATE, DOCK_CAP)
    return {"bedtime": bedtime.strftime("%m-%d %H:%M"), "basis": basis,
            "late_minutes": late, "dock": dock}


def _dock_already_logged(yesterday: date) -> bool | None:
    """True if the neon ledger already holds this night's dock, False if not,
    None if the ledger can't be read. The ledger on ix is the authority (the
    daemon writes it); a formula-grep guard would false-positive on manual
    negative adjustments and false-negative if Toggl edits change the value.
    """
    date_str = f"{yesterday.month}/{yesterday.day}"
    cmd = ("grep -h '\"src\": \"" + DOCK_SRC + "\"' "
           "~/vault/g245/neon-ledger/*.jsonl 2>/dev/null; true")
    try:
        res = subprocess.run(["ssh", "ix", cmd],
                             capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if res.returncode != 0:
        return None
    needle = f'"date": "{date_str}"'
    return any(needle in line for line in res.stdout.splitlines())


def write_sleep_dock(dock: float, yesterday: date) -> dict:
    """Append the dock to yesterday's hcb cell, exactly once per night.

    Fail closed: if the ledger can't be checked, do NOT append — a missed dock
    is recoverable (rerun /0t), a double dock is a silent double charge.
    """
    date_str = f"{yesterday.month}/{yesterday.day}"
    seen = _dock_already_logged(yesterday)
    if seen is None:
        return {"error": "ledger unreachable; dock NOT written (fail closed)"}
    if seen:
        return {"write": f"skipped: dock already in ledger for {date_str}"}
    term = f"-{dock:g}"
    res = neon_excel.append("0分", DOCK_COL_0FEN, date=date_str,
                            value=term, src=DOCK_SRC)
    if not res.get("ok"):
        return {"error": f"append failed: {res}"}
    return {"write": f"{term} → 0分!{DOCK_COL_0FEN} {date_str}",
            "hcb_after": res.get("after_value")}


def compute_sleep(yesterday: date, today: date) -> int:
    """Last night's sleep, in LOCAL_TZ: 睡觉 minutes from yesterday >=20:00 through today <14:00.

    Evaluated in local time so PDT bedtimes (which Toggl stores as next-day UTC) classify
    correctly. Entries are deduped across both UTC day-buckets."""
    now_local = datetime.now(timezone.utc).astimezone(LOCAL_TZ)
    total = 0
    for e in gather_entries_local(yesterday, today):
        if e.get("project_id") != SLEEP_PROJECT_ID:
            continue
        ldt = entry_local_dt(e)
        if ldt is None:
            continue
        in_window = ((ldt.date() == yesterday and ldt.hour >= 20)
                     or (ldt.date() == today and ldt.hour < 14))
        if not in_window:
            continue
        dur = e.get("duration", 0)
        if dur > 0:
            total += dur // 60
        elif dur < 0:
            # Running timer
            total += int((now_local - ldt).total_seconds()) // 60
    return total


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
    # G:O — per-block 分 (地支 卯..亥), read into a "__block__" sub-dict so the
    # dashboard's Points/Block chart has data. This cache is shared with
    # dashboard.py's load_points_data(), whose own xlwings fallback path
    # already writes this key — refresh_points_cache() must match that shape
    # or its daily overwrite silently wipes block data (regression 2026-07-19:
    # Points/Block showed empty because /0t clobbered the cache every morning
    # with a version that never had __block__ at all).
    BLOCK_COLS = {7: '卯', 8: '辰', 9: '巳', 10: '午', 11: '未',
                  12: '申', 13: '酉', 14: '戌', 15: '亥'}
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
        block_data = {}
        for idx, branch in BLOCK_COLS.items():
            val = row[idx - 1]
            if val is not None and isinstance(val, (int, float)) and val > 0:
                block_data[branch] = int(round(float(val)))
        if block_data:
            day_data['__block__'] = block_data
        if day_data:
            result[d.isoformat()] = day_data
    wb.close()

    POINTS_CACHE.write_text(json.dumps(result, indent=2) + "\n")
    _push_points_cache_to_ix()
    return f"{len(result)} days"


def _push_points_cache_to_ix() -> None:
    """The dashboard server (and the .points-cache.json it actually reads)
    lives on ix, but /0t normally runs on the laptop, and i446-monorepo isn't
    synced between hosts — so this cache write would otherwise never reach
    ix, leaving the Points/Block chart stuck on whatever ix last saw (bug
    2026-07-21). Push a copy straight to ix's matching path unless we're
    already running there. Best-effort: dashboard.py's own staleness check
    (_get_points_cache) self-heals from a live Excel read if this fails."""
    host_file = Path.home() / ".claude" / ".host-name"
    if host_file.exists() and host_file.read_text().strip() == "ix":
        return
    try:
        subprocess.run(
            ["scp", "-q", str(POINTS_CACHE), f"ix:{POINTS_CACHE}"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


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

    # "sleep_date" = the day the sleep is recorded under (morning after yesterday)
    sleep_date = yesterday + timedelta(days=1)
    output = {"yesterday": yesterday.isoformat(), "today": today.isoformat(),
              "sleep_date": sleep_date.isoformat()}

    # 1. Compute sleep (bridge yesterday evening → next morning)
    sleep = compute_sleep(yesterday, sleep_date)
    output["sleep_minutes"] = sleep
    output["sleep_display"] = f"{sleep // 60}h {sleep % 60}m"

    # 2. Write sleep to 0₦ (target row = sleep_date, not necessarily today)
    failed = False
    try:
        sleep_result = write_sleep(sleep, sleep_date)
        output["sleep_write"] = sleep_result
    except RuntimeError as e:
        output["sleep_write"] = f"FAILED: {e}"
        failed = True

    # 3. Scan tags and write to 0n
    try:
        tag_totals, proj_totals = compute_tag_minutes(yesterday, today)
        if tag_totals or proj_totals:
            tag_result = write_tag_minutes(tag_totals, yesterday, proj_totals)
            output["tags"] = {"totals": tag_totals, "projects": proj_totals, "write": tag_result}
        else:
            output["tags"] = "none"
    except RuntimeError as e:
        output["tags"] = f"FAILED: {e}"
        failed = True

    # 4. Detect hcmc right before sleep → /did night hcmc
    night_hcmc = detect_night_hcmc(yesterday)
    if night_hcmc and night_hcmc > 0:
        # The detected entry happened on `yesterday` evening — record it there,
        # never on today's row (backfill clobber, 2026-07-24).
        nhcmc_result = mark_night_hcmc(night_hcmc, yesterday)
        output["night_hcmc"] = {"minutes": night_hcmc, "did": nhcmc_result}
        if "error" in nhcmc_result:
            failed = True
    else:
        output["night_hcmc"] = "none"

    # 4b. Sleep dock: -0.25分/min past 22:00 (cap 30) → yesterday's hcb (0分 W).
    # Must run before step 6 so the refreshed points cache reflects the dock.
    try:
        dock_info = compute_sleep_dock(yesterday, sleep_date)
        if dock_info is None:
            output["sleep_dock"] = "no sleep entries found; nothing docked"
        elif dock_info["dock"] <= 0:
            output["sleep_dock"] = {**dock_info, "write": "none (in bed by 22:00)"}
        else:
            wr = write_sleep_dock(dock_info["dock"], yesterday)
            output["sleep_dock"] = {**dock_info, **wr}
            if "error" in wr:
                failed = True
    except Exception as e:
        output["sleep_dock"] = f"FAILED: {e}"
        failed = True

    # 5. Mark 0t done (0₦ + Todoist + stop timer)
    did_result = mark_done()
    output["did"] = did_result
    if "error" in did_result:
        failed = True

    # 5b. Refresh the dtd task cache so 0t drops off the list. mark_done() records
    # 0t in completed-today, but dtd's auto-reload watcher only fires on a cache
    # mtime change — without this, an open dtd keeps showing 0t until the user
    # interacts (regression 2026-06-30). Foreground so it actually completes.
    try:
        subprocess.run(["python3", str(DID_FAST), "--refresh-cache"],
                       capture_output=True, text=True, timeout=45)
        output["dtd_cache"] = "refreshed"
    except Exception as e:
        output["dtd_cache"] = f"ERROR: {e}"

    # 6. Refresh dashboard points cache
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
