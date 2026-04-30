#!/usr/bin/env python3
"""did-fast.py — Fast /did pipeline replacing agent-based execution.

Handles parsing, routing, batched Excel writes, parallel Todoist closes,
and completed-today tracking. Prints JSON results to stdout.

Usage:
    python3 did-fast.py "新闻 10, hcmc 35, push"
    python3 did-fast.py --refresh-headers
    python3 did-fast.py --refresh-cache
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

HEADERS_PATH = Path.home() / ".claude/skills/did/headers.json"
TASK_QUEUE_PATH = Path.home() / "vault/z_ibx/task-queue.json"
TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
TODOIST_BASE = "https://api.todoist.com/api/v1"
HEADERS_MAX_AGE_HOURS = 24

# Import ix_osa (hyphenated filename → importlib)
_IX_PATH = Path.home() / ".claude/skills/_lib/ix-osa.py"
_IX_SPEC = importlib.util.spec_from_file_location("ix_osa", _IX_PATH)
_ix_mod = importlib.util.module_from_spec(_IX_SPEC)
sys.modules["ix_osa"] = _ix_mod  # register so dataclass resolution works
_IX_SPEC.loader.exec_module(_ix_mod)  # type: ignore[union-attr]
ix_run = _ix_mod.run

# Import mark-completed
_MC_PATH = Path(__file__).parent / "mark-completed.py"
_MC_SPEC = importlib.util.spec_from_file_location("mark_completed", _MC_PATH)
mc = importlib.util.module_from_spec(_MC_SPEC)
_MC_SPEC.loader.exec_module(mc)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# Routing constants (from test_did_routing.py)
# ---------------------------------------------------------------------------

STOPWORDS = {"the", "a", "an", "to", "with", "and", "of"}
ALIASES = {"math": "问学", "skin2skin": "问学", "stats m5x2": "stats m5x2"}
CUMULATIVE_0N = {"问学"}
CUMULATIVE_1N = {"一起饭": 30}  # fixed increment per occurrence

# 0₦ habit → Toggl project code (for time_range Toggl entries)
HABIT_PROJECT: dict[str, str] = {
    "wake up": "hcb", "hiit": "hcb", "bio": "hcb",
    "新闻": "hcmc", "hcmc": "hcmc", "night hcmc": "hcmc",
    "词汇": "hcmc",
    "冥想": "hcm", "o314": "hcm",
    "早餐": "家", "问学": "家",
    "xk88": "xk88", "xk20": "xk88", "xk22": "xk88", "xk26": "xk88",
    "睡觉": "睡觉",
    "startup": "g245", "0g": "g245", "tmrw": "g245",
    "i444": "i9", "teams": "i9", "slack github": "i9", "ibx i9": "i9", "stats i9": "i9",
    "m5x2 stats": "m5x2", "ibx m5x2": "m5x2", "slack m5x2": "m5x2",
}

# Variable task keyword → (domain label, 0分 column)
VARIABLE_DOMAIN: dict[str, tuple[str, str]] = {
    "bio": ("hcb", "W"),
    "startup": ("g245", "T"),
    "walk": ("hcb", "W"),
    "run": ("hcb", "W"),
    "nap": ("hcb", "W"),
    "lunch": ("家", "X"),
    "dinner": ("家", "X"),
}
ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")


def time_range_minutes(start: str, end: str) -> int:
    """Compute duration in minutes from HHMM-HHMM."""
    sh, sm = int(start[:2]), int(start[2:])
    eh, em = int(end[:2]), int(end[2:])
    return (eh * 60 + em) - (sh * 60 + sm)
PUNCT_RE = re.compile(r"[^\w\s一-鿿]+", re.UNICODE)
TIME_RANGE_RE = re.compile(r"(\d{4})-(\d{4})")
POINTS_RE = re.compile(r"[\[\{](\d+)[\]\}]")

# 1n+ task name → 0分 column mapping (updated 2026.04.28 after 9-column removal)
ONENEON_TO_0FEN: dict[str, str] = {
    "1s": "T", "1g": "T", "1 hpm": "R", "s+hcbp": "Y",
    "1 f692": "Z", "1 f693": "R", "1 m7": "R", "1 i9": "R",
    "1 -2g": "T", "1 vm+li+msgr": "R", "1 -1n": "P",
    "1 f694": "S", "1 xk88": "Y", "1 xk87": "X",
    "1 xk87 wknd": "X", "1 s897": "Y", "1 hcbc": "W",
    "一起饭": "X", "family": "X", "s897": "Y",
}


def calc_week_mw(d: date) -> str:
    """Calculate M.W format: month.ceil(day/7)."""
    import math
    return f"{d.month}.{math.ceil(d.day / 7)}"

# Label → 0分 column mapping (updated 2026.04.28 after 9-column removal)
LABEL_TO_0FEN = {
    "i9": "R", "i447": "R", "f693": "R", "f694": "R",
    "m5x2": "S",
    "g245": "T", "infra": "T", "cc": "T",
    "hcmc": "U",
    "hcb": "W", "hcbp": "W",
    "xk87": "X", "xk88": "X",
    "s897": "Y",
}


# ---------------------------------------------------------------------------
# Tokenization & matching
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = ANNOT_RE.sub(" ", text)
    text = text.replace("'", "").replace("\u2019", "")
    text = PUNCT_RE.sub(" ", text)
    return [t for t in text.split() if t and t not in STOPWORDS]


def dash_normalize(s: str) -> str:
    return s.replace(" - ", " ")


def overlap_ratio(query_tokens: list[str], task_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    task_set = set(task_tokens)
    return sum(1 for t in query_tokens if t in task_set) / len(query_tokens)


def match_todoist_task(query: str, tasks: list[dict]) -> Optional[dict]:
    """Find best Todoist task match using word overlap."""
    queries = [query]
    alias = ALIASES.get(query.strip().lower())
    if alias and alias != query.strip().lower():
        queries.append(alias)

    best_ratio, best_task = 0.0, None
    for q in queries:
        q_tokens = tokenize(dash_normalize(q))
        if not q_tokens:
            continue
        for task in tasks:
            c_tokens = tokenize(dash_normalize(task["content"]))
            ratio = overlap_ratio(q_tokens, c_tokens)
            if ratio > best_ratio:
                best_ratio, best_task = ratio, task

    threshold = 0.4 if len(tasks) == 1 else 0.6
    return best_task if best_ratio >= threshold else None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class ParsedItem:
    raw: str
    name: str
    time_value: Optional[int] = None
    target_date: Optional[str] = None  # M/D
    points_override: Optional[int] = None
    curly_points: Optional[int] = None  # {N} triggers 0g bonus
    time_range: Optional[tuple[str, str]] = None  # (HHMM, HHMM)
    project_override: Optional[str] = None


def parse_input(raw: str) -> list[ParsedItem]:
    """Split on comma/semicolon, parse each item."""
    today = date.today()
    today_md = f"{today.month}/{today.day}"

    # Check for trailing date
    parts = raw.rstrip().split()
    target_date = today_md
    if parts and parts[-1] == "yesterday":
        yesterday = today - timedelta(days=1)
        target_date = f"{yesterday.month}/{yesterday.day}"
        raw = " ".join(parts[:-1])
    elif parts and re.fullmatch(r"\d{1,2}/\d{1,2}", parts[-1]):
        target_date = parts[-1]
        raw = " ".join(parts[:-1])

    items = []
    for chunk in re.split(r"[,;]", raw):
        chunk = chunk.strip()
        if not chunk:
            continue

        item = ParsedItem(raw=chunk, name=chunk, target_date=target_date)

        # Extract @project override
        at_match = re.search(r"@(\w+)", chunk)
        if at_match:
            item.project_override = at_match.group(1)
            chunk = chunk[:at_match.start()] + chunk[at_match.end():]
            chunk = chunk.strip()

        # Extract {N} curly points
        curly_match = re.search(r"\{(\d+)\}", chunk)
        if curly_match:
            item.curly_points = int(curly_match.group(1))
            item.points_override = item.curly_points
            chunk = chunk[:curly_match.start()] + chunk[curly_match.end():]
            chunk = chunk.strip()

        # Extract [N] points override
        bracket_match = re.search(r"\[(\d+)\]", chunk)
        if bracket_match:
            item.points_override = int(bracket_match.group(1))
            chunk = chunk[:bracket_match.start()] + chunk[bracket_match.end():]
            chunk = chunk.strip()

        # Extract HHMM-HHMM time range
        tr_match = TIME_RANGE_RE.search(chunk)
        if tr_match:
            item.time_range = (tr_match.group(1), tr_match.group(2))
            chunk = chunk[:tr_match.start()] + chunk[tr_match.end():]
            chunk = chunk.strip()

        # Extract trailing number as time value
        name_parts = chunk.split()
        if len(name_parts) >= 2 and name_parts[-1].isdigit():
            item.time_value = int(name_parts[-1])
            chunk = " ".join(name_parts[:-1])

        # Apply aliases
        lower = chunk.strip().lower()
        if lower in ALIASES:
            chunk = ALIASES[lower]

        item.name = chunk.strip()
        items.append(item)

    return items


# ---------------------------------------------------------------------------
# Header cache
# ---------------------------------------------------------------------------

def load_headers() -> dict:
    """Load headers.json, auto-refresh if stale or missing."""
    if HEADERS_PATH.exists():
        data = json.loads(HEADERS_PATH.read_text())
        refreshed = data.get("refreshed", "")
        if refreshed:
            try:
                age = datetime.now() - datetime.fromisoformat(refreshed)
                if age.total_seconds() < HEADERS_MAX_AGE_HOURS * 3600:
                    return data
            except ValueError:
                pass
    return refresh_headers()


def refresh_headers() -> dict:
    """Read 0n and 1n+ row 1 headers from Excel via one AppleScript call."""
    script = '''tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set ws0 to sheet "0n" of wb
    set ws1 to sheet "1n+" of wb
    set colLetters to {"A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z","AA","AB","AC","AD","AE","AF","AG","AH","AI","AJ","AK","AL","AM","AN","AO","AP","AQ","AR","AS","AT","AU","AV","AW","AX","AY","AZ","BA","BB","BC","BD","BE","BF","BG","BH","BI","BJ","BK","BL"}
    set r0 to ""
    repeat with c from 1 to 62
        set cellVal to value of cell c of row 1 of ws0
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed is not "" then
                set r0 to r0 & c & "\\t" & trimmed & "\\n"
            end if
        end if
    end repeat
    set r1 to ""
    repeat with c from 3 to 40
        set cellVal to value of cell c of row 1 of ws1
        if cellVal is not missing value then
            set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
            if trimmed is not "" then
                set colLetter to item c of colLetters
                set r1 to r1 & colLetter & "\\t" & trimmed & "\\n"
            end if
        end if
    end repeat
    return "0N\\n" & r0 & "1N\\n" & r1
end tell'''

    res = ix_run(script, timeout=30.0)
    if res.returncode != 0:
        print(f"ERROR: refresh_headers failed: {res.stderr}", file=sys.stderr)
        sys.exit(3)

    headers_0n: dict[str, int] = {}
    headers_1n: dict[str, str] = {}
    section = None

    for line in res.stdout.strip().split("\n"):
        line = line.strip()
        if line == "0N":
            section = "0n"
            continue
        elif line == "1N":
            section = "1n"
            continue
        if not line or "\t" not in line:
            continue
        key, name = line.split("\t", 1)
        if section == "0n":
            headers_0n[name.lower()] = int(key)
        elif section == "1n":
            headers_1n[name.lower()] = key  # column letter

    data = {
        "refreshed": datetime.now().isoformat(),
        "0n": headers_0n,
        "1n": headers_1n,
    }
    HEADERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEADERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


# ---------------------------------------------------------------------------
# Task queue cache
# ---------------------------------------------------------------------------

def load_task_queue() -> dict:
    if not TASK_QUEUE_PATH.exists():
        return {}
    return json.loads(TASK_QUEUE_PATH.read_text())


def refresh_task_queue() -> dict:
    """Fetch 0neon + 1neon + 夜neon + 关键路径 from Todoist, rebuild cache."""
    labels = ["0neon", "1neon", "%E5%A4%9Cneon", "%E5%85%B3%E9%94%AE%E8%B7%AF%E5%BE%84"]
    keys = ["0neon", "1neon", "夜neon", "关键路径"]
    results = {}

    def fetch_label(label):
        url = f"{TODOIST_BASE}/tasks?label={label}&limit=200"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {TODOIST_TOKEN}",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                tasks = data.get("results", data) if isinstance(data, dict) else data
                return [{"id": t["id"], "content": t["content"],
                         "labels": t.get("labels", []),
                         "due": t.get("due", {}).get("date", "") if t.get("due") else ""}
                        for t in tasks]
        except Exception as e:
            print(f"WARN: fetch {label}: {e}", file=sys.stderr)
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_label, lbl): key for lbl, key in zip(labels, keys)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    cache = {"updated": datetime.now().isoformat()}
    cache.update(results)
    TASK_QUEUE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
    return cache


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    item: ParsedItem
    step: str  # "0n", "1n", "todoist", "variable", "needs_agent"
    col_num: Optional[int] = None  # 0n column number
    col_letter: Optional[str] = None  # 1n column letter
    todoist_task: Optional[dict] = None
    write_value: int | float = 1
    fen_col: Optional[str] = None  # 0分 column for points
    fen_points: int = 0
    fen_cell_ref: Optional[str] = None  # 1n+ cell ref like "'1n+'!D20"
    is_cumulative_1n: bool = False
    cumulative_increment: int = 0
    error: Optional[str] = None


def route_items(items: list[ParsedItem], headers: dict, tq: dict) -> list[RouteResult]:
    """Route each item through 0₦ → 1n+ → Todoist → variable."""
    h0n = headers.get("0n", {})
    h1n = headers.get("1n", {})
    all_tasks = tq.get("0neon", []) + tq.get("夜neon", []) + tq.get("1neon", [])
    results = []

    for item in items:
        name_lower = item.name.lower()

        # Step 0.1: 0₦ match
        if name_lower in h0n:
            today_md = item.target_date or f"{date.today().month}/{date.today().day}"
            today_date = date.today()
            # Past date → needs agent (posthoc flow)
            target_parts = today_md.split("/")
            if len(target_parts) == 2:
                t_month, t_day = int(target_parts[0]), int(target_parts[1])
                if (t_month, t_day) != (today_date.month, today_date.day):
                    r = RouteResult(item=item, step="needs_agent",
                                    error="past date requires posthoc flow")
                    results.append(r)
                    continue

            col = h0n[name_lower]
            if item.time_range:
                val = time_range_minutes(item.time_range[0], item.time_range[1])
            elif item.time_value is not None:
                val = item.time_value
            else:
                val = 1
            # Cumulative columns: add to existing (handled in AppleScript)
            is_cumulative = item.name in CUMULATIVE_0N

            r = RouteResult(item=item, step="0n", col_num=col, write_value=val)

            # Find matching Todoist task to close
            neon_tasks = tq.get("0neon", []) + tq.get("夜neon", [])
            matched = match_todoist_task(item.name, neon_tasks)
            if matched:
                r.todoist_task = matched
                # 0n habits do NOT write to 0分 directly.
                # Excel's own formulas roll up 0n data into 0分.
                # Writing here would double-count.

            results.append(r)
            continue

        # Step 0.2: 1n+ match (now handled in fast path)
        if name_lower in h1n:
            col_letter = h1n[name_lower]
            fen_col = ONENEON_TO_0FEN.get(name_lower)
            is_cumul = name_lower in CUMULATIVE_1N
            r = RouteResult(item=item, step="1n", col_letter=col_letter,
                            fen_col=fen_col,
                            is_cumulative_1n=is_cumul,
                            cumulative_increment=CUMULATIVE_1N.get(name_lower, 0))
            # Find matching Todoist 1neon task to close
            neon_1n_tasks = tq.get("1neon", [])
            matched = match_todoist_task(item.name, neon_1n_tasks)
            if matched:
                r.todoist_task = matched
            results.append(r)
            continue

        # Step 0.3: Todoist match
        matched = match_todoist_task(item.name, all_tasks)
        if matched:
            # Extract points
            pts_match = POINTS_RE.search(matched["content"])
            points = item.points_override or (int(pts_match.group(1)) if pts_match else 0)

            # Map label to 0分 column
            fen_col = None
            for lbl in matched.get("labels", []):
                if lbl in LABEL_TO_0FEN:
                    fen_col = LABEL_TO_0FEN[lbl]
                    break

            r = RouteResult(item=item, step="todoist", todoist_task=matched,
                            fen_col=fen_col, fen_points=points)
            results.append(r)
            continue

        # Step 0.4: Variable task
        # Resolve domain from @project override, keyword map, or bail
        domain_label, fen_col = None, None
        if item.project_override and item.project_override in LABEL_TO_0FEN:
            domain_label = item.project_override
            fen_col = LABEL_TO_0FEN[item.project_override]
        elif name_lower in VARIABLE_DOMAIN:
            domain_label, fen_col = VARIABLE_DOMAIN[name_lower]
        else:
            r = RouteResult(item=item, step="needs_agent",
                            error="no match, needs domain disambiguation")
            results.append(r)
            continue

        # Compute points: explicit override > time_range duration > 0
        pts = item.points_override or 0
        if pts == 0 and item.time_range:
            pts = time_range_minutes(item.time_range[0], item.time_range[1])

        r = RouteResult(item=item, step="variable",
                        fen_col=fen_col, fen_points=pts)
        r.todoist_task = None  # will create posthoc in main
        r.error = domain_label  # stash domain label for posthoc creation
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# Batched AppleScript writes
# ---------------------------------------------------------------------------

def build_0n_script(writes: list[RouteResult], target_date: str) -> Optional[str]:
    """Build AppleScript for batch 0₦ writes."""
    if not writes:
        return None

    parts = target_date.split("/")
    month, day = parts[0], parts[1]

    set_lines = []
    verify_lines = []
    for w in writes:
        is_cumulative = w.item.name in CUMULATIVE_0N
        col = w.col_num
        val = w.write_value
        if is_cumulative:
            set_lines.append(f'''    set oldVal to value of cell {col} of row todayRow of ws
    if oldVal is missing value or (oldVal as text) = "" or (oldVal as text) = "0" then
        set value of cell {col} of row todayRow of ws to {val}
    else
        set value of cell {col} of row todayRow of ws to (oldVal as number) + {val}
    end if''')
        else:
            set_lines.append(f"    set value of cell {col} of row todayRow of ws to {val}")
        verify_lines.append(
            f'    set v{col} to value of cell {col} of row todayRow of ws\n'
            f'    set results to results & "{col}=" & (v{col} as text) & "|"'
        )

    script = f'''tell application "Microsoft Excel"
    set ws to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of ws
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
    if todayRow = 0 then return "ERROR: date {target_date} not found"
{chr(10).join(set_lines)}
    set results to ""
{chr(10).join(verify_lines)}
    return "OK:row=" & todayRow & "|" & results
end tell'''
    return script


def build_0fen_script(appends: list[tuple[str, int]], target_date: str) -> Optional[str]:
    """Build AppleScript for batch 0分 appends. appends = [(col_letter, points), ...]"""
    if not appends:
        return None

    append_lines = []
    for col, pts in appends:
        append_lines.append(f'''    set theCell to range ("{col}" & todayRow) of ws
    set oldFormula to formula of theCell
    if oldFormula = "" or oldFormula = "0" then
        set formula of theCell to "=0+{pts}"
    else if character 1 of oldFormula is not "=" then
        set formula of theCell to "=" & oldFormula & "+{pts}"
    else
        set formula of theCell to oldFormula & "+{pts}"
    end if''')

    script = f'''tell application "Microsoft Excel"
    set ws to sheet "0分" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of range ("B" & i) of ws) = "{target_date}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {target_date} not found in 0分"
{chr(10).join(append_lines)}
    return "OK:0fen row=" & todayRow
end tell'''
    return script


def build_1n_script(writes: list[RouteResult], week_mw: str) -> Optional[str]:
    """Build AppleScript for batch 1n+ writes. Finds week row, reads row 3 points, writes."""
    if not writes:
        return None

    # Build per-column write + verify lines
    write_lines = []
    verify_lines = []
    for w in writes:
        col = w.col_letter
        if w.is_cumulative_1n:
            inc = w.cumulative_increment
            write_lines.append(f'''    set oldVal to string value of range ("{col}" & weekRow) of ws1n
    if oldVal = "" or oldVal = "0" then
        set value of range ("{col}" & weekRow) of ws1n to {inc}
    else
        set value of range ("{col}" & weekRow) of ws1n to ((oldVal as number) + {inc})
    end if''')
        else:
            write_lines.append(f'''    set pts{col} to value of range ("{col}3") of ws1n
    set value of range ("{col}" & weekRow) of ws1n to pts{col}''')
        verify_lines.append(
            f'    set v{col} to string value of range ("{col}" & weekRow) of ws1n\n'
            f'    set results to results & "{col}=" & v{col} & "|"'
        )

    script = f'''tell application "Microsoft Excel"
    set wb to workbook "Neon分v12.2.xlsx"
    set ws1n to sheet "1n+" of wb
    set weekRow to 0
    repeat with r from 4 to 100
        set bVal to string value of range ("B" & r) of ws1n
        if bVal = "{week_mw}" then
            set weekRow to r
            exit repeat
        end if
    end repeat
    if weekRow = 0 then return "ERROR: week {week_mw} not found"
{chr(10).join(write_lines)}
    set results to "weekRow=" & weekRow & "|"
{chr(10).join(verify_lines)}
    return "OK:" & results
end tell'''
    return script


def build_1n_0fen_script(refs: list[tuple[str, str, str]], target_date: str) -> Optional[str]:
    """Build AppleScript to append 1n+ cell references to 0分.
    refs = [(fen_col, 1n_col_letter, weekRow_placeholder), ...]
    weekRow is unknown until the 1n script runs, so we pass it as a known value."""
    if not refs:
        return None

    append_lines = []
    for fen_col, one_col, week_row in refs:
        append_lines.append(f'''    set theCell to range ("{fen_col}" & todayRow) of ws
    set oldFormula to formula of theCell
    if oldFormula = "" or oldFormula = "0" then
        set formula of theCell to "=0+'1n+'!{one_col}{week_row}"
    else if character 1 of oldFormula is not "=" then
        set formula of theCell to "=" & oldFormula & "+'1n+'!{one_col}{week_row}"
    else
        set formula of theCell to oldFormula & "+'1n+'!{one_col}{week_row}"
    end if''')

    script = f'''tell application "Microsoft Excel"
    set ws to sheet "0分" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with i from 2 to 200
        if (string value of range ("B" & i) of ws) = "{target_date}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {target_date} not found in 0分"
{chr(10).join(append_lines)}
    return "OK:1n_0fen row=" & todayRow
end tell'''
    return script


# ---------------------------------------------------------------------------
# Toggl timer stop
# ---------------------------------------------------------------------------

TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"


def stop_matching_toggl(item_names: list[str]) -> Optional[dict]:
    """If a running Toggl timer matches any item name, stop it.

    Returns dict with timer info if stopped, None otherwise.
    """
    try:
        proc = subprocess.run(
            ["python3", str(TOGGL_CLI), "current"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None

        # Parse "Running: HH:MM-running <desc> @<project> (running) [id:NNN]"
        line = proc.stdout.strip()
        if not line.startswith("Running:"):
            return None

        # Strip "Running: " prefix and time prefix like "07:34-running "
        desc_part = line[len("Running:"):].strip()
        time_prefix = re.match(r"\d{2}:\d{2}-running\s+", desc_part)
        if time_prefix:
            desc_part = desc_part[time_prefix.end():]

        # Extract description (before " @")
        at_idx = desc_part.find(" @")
        desc = desc_part[:at_idx].strip() if at_idx >= 0 else desc_part.split("(")[0].strip()

        # Check if any item matches the timer description
        desc_lower = desc.lower()
        matched = any(n.lower() == desc_lower for n in item_names)
        if not matched:
            return None

        # Stop the timer
        stop_proc = subprocess.run(
            ["python3", str(TOGGL_CLI), "stop"],
            capture_output=True, text=True, timeout=10,
        )
        return {"description": desc, "stopped": stop_proc.returncode == 0,
                "output": stop_proc.stdout.strip()}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Todoist close
# ---------------------------------------------------------------------------

def close_todoist_task(task_id: str) -> tuple[str, bool]:
    url = f"{TODOIST_BASE}/tasks/{task_id}/close"
    req = urllib.request.Request(url, method="POST", headers={
        "Authorization": f"Bearer {TODOIST_TOKEN}",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return task_id, True
    except Exception as e:
        return task_id, False


def close_todoist_tasks(task_ids: list[str]) -> dict[str, bool]:
    if not task_ids:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(close_todoist_task, tid): tid for tid in task_ids}
        for future in as_completed(futures):
            tid, ok = future.result()
            results[tid] = ok
    return results


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("usage: did-fast.py <items> | --refresh-headers | --refresh-cache",
              file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--refresh-headers":
        data = refresh_headers()
        print(json.dumps({"status": "ok", "0n_count": len(data["0n"]),
                          "1n_count": len(data["1n"])}, indent=2))
        return

    if sys.argv[1] == "--refresh-cache":
        data = refresh_task_queue()
        counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        print(json.dumps({"status": "ok", "counts": counts}, indent=2))
        return

    raw = " ".join(sys.argv[1:])

    # 1. Parse
    items = parse_input(raw)
    if not items:
        print(json.dumps({"error": "no items parsed"}))
        sys.exit(1)

    # 2. Load caches
    headers = load_headers()
    tq = load_task_queue()

    # 3. Route
    routes = route_items(items, headers, tq)

    # Separate fast-path from agent-required
    fast = [r for r in routes if r.step in ("0n", "todoist", "1n", "variable")]
    agent_needed = [r for r in routes if r.step == "needs_agent"]

    # 3b. Stop matching Toggl timer
    all_names = [r.item.name for r in fast]
    toggl_stop = stop_matching_toggl(all_names) if all_names else None

    # 4. Batch 0₦ writes
    on_writes = [r for r in fast if r.step == "0n" and r.col_num]
    target_date = items[0].target_date or f"{date.today().month}/{date.today().day}"

    on_result = None
    if on_writes:
        script = build_0n_script(on_writes, target_date)
        if script:
            on_result = ix_run(script, timeout=30.0)

    # 4b. Batch 1n+ writes
    one_n_writes = [r for r in fast if r.step == "1n" and r.col_letter]
    one_n_result = None
    week_row = None
    if one_n_writes:
        week_mw = calc_week_mw(date.today())
        script = build_1n_script(one_n_writes, week_mw)
        if script:
            one_n_result = ix_run(script, timeout=30.0)
            # Parse weekRow from output for 0分 cell references
            if one_n_result.returncode == 0:
                import re as _re
                m = _re.search(r"weekRow=(\d+)", one_n_result.stdout)
                if m:
                    week_row = m.group(1)

    # 4c. Batch 1n+ → 0分 cell reference appends
    one_n_fen_result = None
    if one_n_writes and week_row:
        refs = []
        for r in one_n_writes:
            if r.fen_col and r.col_letter:
                refs.append((r.fen_col, r.col_letter, week_row))
        if refs:
            script = build_1n_0fen_script(refs, target_date)
            if script:
                one_n_fen_result = ix_run(script, timeout=30.0)

    # 5. Batch 0分 appends (for 0n and todoist items with direct points)
    fen_appends = []
    for r in fast:
        if r.step != "1n" and r.fen_col and r.fen_points > 0:
            fen_appends.append((r.fen_col, r.fen_points))

    fen_result = None
    if fen_appends:
        script = build_0fen_script(fen_appends, target_date)
        if script:
            fen_result = ix_run(script, timeout=30.0)

    # 6. Close Todoist tasks in parallel
    task_ids = []
    id_to_name = {}
    for r in fast:
        if r.todoist_task:
            tid = r.todoist_task["id"]
            task_ids.append(tid)
            id_to_name[tid] = r.item.name

    close_results = close_todoist_tasks(task_ids)

    # 6b. Create Toggl entries for items with time_range (parallel)
    toggl_created = {}
    toggl_items = [(r.item.name, r.item.time_range,
                    r.item.project_override or HABIT_PROJECT.get(r.item.name.lower()),
                    r.item.target_date)
                   for r in fast if r.item.time_range]
    if toggl_items:
        def _create_toggl(args):
            name, tr, proj, td = args
            today_str = date.today().isoformat()
            # If target_date differs from today, compute ISO date
            if td:
                parts = td.split("/")
                if len(parts) == 2:
                    today_str = f"{date.today().year}-{int(parts[0]):02d}-{int(parts[1]):02d}"
            cmd = ["python3", str(TOGGL_CLI), "create", name,
                   f"{tr[0][:2]}:{tr[0][2:]}", f"{tr[1][:2]}:{tr[1][2:]}"]
            if proj:
                cmd.append(proj)
            cmd.extend(["--date", today_str])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                return name, proc.returncode == 0, proc.stdout.strip()
            except Exception as e:
                return name, False, str(e)

        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, ok, out in pool.map(_create_toggl, toggl_items):
                toggl_created[name] = {"ok": ok, "output": out}

    # 6c. Create posthoc Todoist tasks for variable items (parallel)
    posthoc_results = {}
    variable_items = [r for r in fast if r.step == "variable"]
    if variable_items:
        today_iso = date.today().isoformat()
        target_md = variable_items[0].item.target_date or f"{date.today().month}/{date.today().day}"

        def _create_posthoc(r):
            domain_label = r.error  # stashed domain label
            content = f"{r.item.name} @posthoc @{today_iso}"
            labels = ["posthoc"]
            if domain_label:
                labels.append(domain_label)
            body = json.dumps({
                "content": content,
                "labels": labels,
                "due_date": today_iso,
            }).encode()
            req = urllib.request.Request(
                f"{TODOIST_BASE}/tasks",
                data=body,
                headers={
                    "Authorization": f"Bearer {TODOIST_TOKEN}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    task = json.loads(resp.read())
                    tid = task["id"]
                    # Immediately close it
                    close_req = urllib.request.Request(
                        f"{TODOIST_BASE}/tasks/{tid}/close",
                        method="POST",
                        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"},
                    )
                    urllib.request.urlopen(close_req, timeout=15)
                    return r.item.name, {"id": tid, "closed": True}
            except Exception as e:
                return r.item.name, {"error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, result in pool.map(_create_posthoc, variable_items):
                posthoc_results[name] = result

    # 7. Update completed-today (with points for build order enrichment)
    completed_names = [r.item.name for r in fast]
    completed_points = {}
    for r in fast:
        if r.fen_points:
            completed_points[r.item.name] = r.fen_points
    if completed_names:
        mc.append_names(completed_names, points=completed_points)

    # 8. Build output
    output = {"results": [], "agent_needed": []}

    for r in fast:
        entry = {
            "name": r.item.name,
            "step": r.step,
            "value": r.write_value if r.step == "0n" else None,
            "col": r.col_num,
        }
        if r.todoist_task:
            tid = r.todoist_task["id"]
            entry["todoist"] = {
                "id": tid,
                "content": r.todoist_task["content"],
                "closed": close_results.get(tid, False),
            }
        if r.step == "variable" and r.item.name in posthoc_results:
            entry["posthoc"] = posthoc_results[r.item.name]
        if r.fen_col:
            entry["0fen"] = {"col": r.fen_col, "points": r.fen_points}
        if r.item.time_range and r.item.name in toggl_created:
            entry["toggl"] = toggl_created[r.item.name]
        output["results"].append(entry)

    for r in agent_needed:
        output["agent_needed"].append({
            "name": r.item.name,
            "raw": r.item.raw,
            "reason": r.error,
        })

    if toggl_stop:
        output["toggl_stopped"] = toggl_stop

    if one_n_result:
        output["1n_write"] = {
            "ok": one_n_result.returncode == 0,
            "output": one_n_result.stdout.strip(),
        }
        if one_n_result.returncode != 0:
            output["1n_write"]["error"] = one_n_result.stderr.strip() or f"ix-osa exit {one_n_result.returncode}"
    if one_n_fen_result:
        output["1n_0fen_write"] = {
            "ok": one_n_fen_result.returncode == 0,
            "output": one_n_fen_result.stdout.strip(),
        }
        if one_n_fen_result.returncode != 0:
            output["1n_0fen_write"]["error"] = one_n_fen_result.stderr.strip() or f"ix-osa exit {one_n_fen_result.returncode}"

    if on_result:
        output["0n_write"] = {
            "ok": on_result.returncode == 0,
            "output": on_result.stdout.strip(),
        }
        if on_result.returncode != 0:
            output["0n_write"]["error"] = on_result.stderr.strip() or f"ix-osa exit {on_result.returncode}"
    if fen_result:
        output["0fen_write"] = {
            "ok": fen_result.returncode == 0,
            "output": fen_result.stdout.strip(),
        }
        if fen_result.returncode != 0:
            output["0fen_write"]["error"] = fen_result.stderr.strip() or f"ix-osa exit {fen_result.returncode}"

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
