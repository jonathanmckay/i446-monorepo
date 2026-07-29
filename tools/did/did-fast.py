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
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

HEADERS_PATH = Path.home() / ".claude/skills/did/headers.json"
import sys as _sys; _sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib")); import state_paths as _sp
TASK_QUEUE_PATH = _sp.TASK_QUEUE
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

# All 0分 writes go through the excel-http daemon (localhost:9876 on ix) via
# lib/neon/excel so they land in the audit ledger with a `src` label and get
# chain-checked. Raw AppleScript 0分 writes are banned (2026-07-28 migration
# after the batch AppleScript path wrote +15+10 to 0分!R invisibly on 7/27);
# the no-raw-0fen regression test scans this file to enforce the ban. Reads
# and other sheets (0n/1n+/hcbi) still use ix_run.
from neon import excel as neon_excel  # noqa: E402 (path inserted above)

# Import mark-completed
_MC_PATH = Path(__file__).parent / "mark-completed.py"
_MC_SPEC = importlib.util.spec_from_file_location("mark_completed", _MC_PATH)
mc = importlib.util.module_from_spec(_MC_SPEC)
_MC_SPEC.loader.exec_module(mc)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# Routing constants (from test_did_routing.py)
# ---------------------------------------------------------------------------

STOPWORDS = {"the", "a", "an", "to", "with", "and", "of"}
ALIASES = {"math": "问学", "skin2skin": "问学", "stats m5x2": "stats m5x2",
           # The daily 夜neon card is named "evening hcmc"; the 0n header is
           # "night hcmc" (registry alias since 2026-07-25 — mirrored here so
           # DIRECT did-fast invocations, e.g. dtd's worker, route it too).
           "evening hcmc": "night hcmc"}
CUMULATIVE_0N = {"问学"}
CUMULATIVE_1N = {}  # fixed increment per occurrence

# Variable tasks: points derived from timer duration, not fixed row-3 values
VARIABLE_0N = {"xk20", "xk22", "xk26", "xk88", "冥想", "o314", "其他人", "新闻",
               "night hcmc", "evening hcmc", "hiit"}  # evening hcmc = the card's name
VARIABLE_1N = {"s897", "family", "relax {60}", "s+hcbp", "一起饭", "业写",
               "长冥想", "长o314", "aos", "1 kids nature"}
# Points formulas from 1n+ row 5 ("expected points"): value = base + rate×min.
# Default rate is 1/min ("1/m"); entries here override (".5/m", "15+1/m").
# Keys are header_normalize()d (lowercase).
VARIABLE_1N_RATES: dict[str, float] = {"长冥想": 0.5, "长o314": 0.5}
VARIABLE_1N_BASES: dict[str, int] = {"一起饭": 15, "aos": 15}
# Default points when completed with no minutes at all (falls back to base).
VARIABLE_1N_DEFAULTS: dict[str, int] = {}


def variable_1n_points(resolved_1n: str, minutes: int) -> int:
    """Points for a variable 1n+ habit given minutes: base + rate×minutes."""
    base = VARIABLE_1N_BASES.get(resolved_1n, 0)
    rate = VARIABLE_1N_RATES.get(resolved_1n, 1.0)
    return base + int(round(rate * minutes))

# 0₦ habit → Toggl project code (for time_range Toggl entries)
HABIT_PROJECT: dict[str, str] = {
    "wake up": "hcb", "hiit": "hcb", "bio": "hcb",
    "新闻": "hcmc", "hcmc": "hcmc", "night hcmc": "hcmc",
    "词汇": "hcmc",
    "冥想": "hcm", "o314": "hcm", "其他人": "hcm",
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
    "bball": ("hcbp", "W"),
}

# Habits that also write to the hcbi sheet (append +N to formula).
# key = habit name (lowercase), value = hcbi column letter
HCBI_HABITS: dict[str, str] = {
    "bball": "Y",
}
# 1n+ header aliases: map variant names to actual 1n+ headers
ONENEON_ALIASES: dict[str, str] = {
    "1 hcbp": "1 hcb",
    "家": "family",
    "relax": "relax {60}",
    "一起吃": "一起饭",
    "long o314": "长o314",
    # Card is "1 groceries" but the 1n+ header is bare "groceries" — without
    # this the completion closed the card and silently skipped the column
    # write (found by neon-task-checksum's first run, 2026-07-26).
    "1 groceries": "groceries",
    # Weekly card "1 i447" vs bare 1n+ header "i447": the bare name collides
    # with the DAILY 0n habit "i447", which routing hits first — the weekly
    # card could never be completed by name and sat overdue forever (bug
    # 2026-07-27: "i447 keeps recurring even though I mark it done").
    "1 i447": "i447",
}

ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")


def time_range_minutes(start: str, end: str) -> int:
    """Compute duration in minutes from HHMM-HHMM."""
    sh, sm = int(start[:2]), int(start[2:])
    eh, em = int(end[:2]), int(end[2:])
    return (eh * 60 + em) - (sh * 60 + sm)
PUNCT_RE = re.compile(r"[^\w\s一-鿿]+", re.UNICODE)
TIME_RANGE_RE = re.compile(r"(\d{4})-(\d{4})")
# SQUARE brackets only. {N} is the 0g bonus and flows via item.curly_points →
# column Q; matching braces here made every completed {N} goal credit its
# DOMAIN column too — a silent double-count on top of the Q credit (bug
# 2026-07-27: "what earned me the +30+20+20+10 in hcb?" — three of the four
# were {N} duplicates).
POINTS_RE = re.compile(r"\[(\d+)\]")
# "[1/m]" / "[.5/m]" / "[15+1/m]" — display-only rate markers on variable 1n+
# cards. Stripped before routing (the rates themselves live in
# VARIABLE_1N_RATES/BASES, keyed by header).
RATE_ANNOT_RE = re.compile(r"\s*\[[0-9.+]*/m\]")

# 1n+ task name → 0分 column mapping (updated 2026.04.28 after 9-column removal)
ONENEON_TO_0FEN: dict[str, str] = {
    "1s": "T", "1g": "T", "1 hpm": "R", "s+hcbp": "W",
    "1 f692": "Z", "1 f693": "R", "1 m7": "R", "1 i9": "R",
    "1 -2g": "T", "1 vm+li+msgr": "R", "1 -1n": "P",
    "1 f694": "S", "1 xk88": "Y", "1 xk87": "X",
    "1 xk87 wknd": "X", "1 s897": "Y", "1 hcbc": "W",
    "一起饭": "X", "family": "X", "s897": "Y",
    "relax {60}": "W", "业写": "R",
}


def calc_week_mw(d: date) -> str:
    """Calculate M.W format using Sunday-anchored weeks.

    Find the Sunday that starts this date's week, then count which
    Sunday-block of the month it falls in. The week label uses the
    Sunday's month, so Mon-Sat after a month-boundary Sunday still
    belong to the Sunday's month (e.g. Jun 1 Mon → 5.5 if Sunday
    was May 31).
    """
    days_since_sunday = (d.weekday() + 1) % 7  # Sun=0, Mon=1, ..., Sat=6
    week_start = date.fromordinal(d.toordinal() - days_since_sunday)
    week_num = ((week_start.day - 1) // 7) + 1
    return f"{week_start.month}.{week_num}"

# Label → 0分 column mapping (updated 2026.04.28 after 9-column removal)
LABEL_TO_0FEN = {
    "i9": "R", "i447": "R", "f693": "R", "f694": "R",
    "m5x2": "S",
    "g245": "T", "infra": "T", "cc": "T",
    "hcmc": "U",
    "hcm": "V", "hci": "V",
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


def header_normalize(s: str) -> str:
    """Normalize a habit name for header lookup: collapse hyphens, dashes, and
    runs of whitespace into a single space; lowercase. So `wake-up`, `Wake Up`,
    and `wake — up` all map to `wake up`."""
    import re as _re
    return _re.sub(r"[\s\-—–]+", " ", s).strip().lower()


_TOGGL_TODAY: Optional[list] = None  # one fetch per invocation


def toggl_minutes_for(name: str) -> Optional[int]:
    """Sum today's Toggl minutes for entries whose description matches `name`
    (header-normalized equality), including the running entry's elapsed time.

    Feature (2026-07-24): completing a 0₦ habit in dtd with no typed value
    should record how long it actually took — the user usually has a Toggl
    entry with the same name — instead of a flat 1. Returns None when Toggl
    is unreachable or nothing matches (caller falls back to 1), so dtd keeps
    working offline.
    """
    global _TOGGL_TODAY
    try:
        if _TOGGL_TODAY is None:
            sys.path.insert(0, str(Path.home() / "i446-monorepo"))
            from mcp.toggl_server import toggl_api
            today = date.today()
            _TOGGL_TODAY = toggl_api.get_entries(
                start_date=today.isoformat(),
                end_date=(today + timedelta(days=1)).isoformat()) or []
        target = header_normalize(name)
        secs = 0.0
        for e in _TOGGL_TODAY:
            if header_normalize(e.get("description") or "") != target:
                continue
            dur = e.get("duration") or 0
            if dur < 0:  # running entry: elapsed = now - start
                st = datetime.fromisoformat(
                    e["start"].replace("Z", "+00:00"))
                dur = (datetime.now(st.tzinfo) - st).total_seconds()
            secs += max(0, dur)
        minutes = round(secs / 60)
        return minutes if minutes > 0 else None
    except Exception:
        return None


def overlap_ratio(query_tokens: list[str], task_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    task_set = set(task_tokens)
    return sum(1 for t in query_tokens if t in task_set) / len(query_tokens)


def match_todoist_task(query: str, tasks: list[dict],
                       preferred_id: str | None = None) -> Optional[dict]:
    """Find best Todoist task match using word overlap.

    preferred_id (dtd's collision-proof path): if given and a task in `tasks`
    carries that id, return it directly — the EXACT row the user selected, so a
    duplicate task name can't complete the wrong instance (2026-07-12).
    """
    if preferred_id:
        for task in tasks:
            if str(task.get("id")) == str(preferred_id):
                return task
        # Not in this bucket (stale task-queue cache, or the row came from a
        # different bucket): fetch the exact task rather than dropping to the
        # name match below, which can pick a DIFFERENT same-named instance
        # (2026-07-24: completing an overdue "AoS" one-off copy name-matched
        # the recurring parent instead — wrong task, and its future due date
        # then tripped the already-done-today close guard).
        fetched = _fetch_task_by_id(preferred_id)
        if fetched:
            return fetched
    queries = [query]
    alias = ALIASES.get(query.strip().lower())
    if alias and alias != query.strip().lower():
        queries.append(alias)

    best_score, best_task = (0.0, 0.0), None
    for q in queries:
        q_tokens = tokenize(dash_normalize(q))
        if not q_tokens:
            continue
        q_set = set(q_tokens)
        for task in tasks:
            c_tokens = tokenize(dash_normalize(task["content"]))
            ratio = overlap_ratio(q_tokens, c_tokens)
            # Tiebreak on task-side coverage: when two tasks share the query's
            # tokens equally (e.g. "stats" matches both "stats" and "m5x2
            # stats"), prefer the task with fewer leftover tokens — the exact
            # match — instead of whichever happened to be iterated first.
            task_cov = (sum(1 for t in c_tokens if t in q_set) / len(c_tokens)
                        if c_tokens else 0.0)
            score = (ratio, task_cov)
            if score > best_score:
                best_score, best_task = score, task

    threshold = 0.4 if len(tasks) == 1 else 0.6
    return best_task if best_score[0] >= threshold else None


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
    defer_date: Optional[str] = None  # ISO date (YYYY-MM-DD) for partial completion
    toggl_tags: list = None  # #tag tokens → Toggl tags
    bonus_points: Optional[int] = None  # +N bonus points added on top of computed value


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
    # Split on ASCII , ; AND their fullwidth forms ，； — a fullwidth comma from
    # CJK input used to leave two items lumped into one (e.g. "睡觉，0744 xk22").
    for chunk in re.split(r"[,;，；]", raw):
        chunk = chunk.strip()
        if not chunk:
            continue

        item = ParsedItem(raw=chunk, name=chunk, target_date=target_date)

        # Extract --defer flag (--tmrw, --tomorrow, --Mon, --Jun 15, etc.)
        defer_match = re.search(r"--(\S+(?:\s+\d{1,2})?)\s*$", chunk)
        if defer_match:
            defer_raw = defer_match.group(1).strip()
            chunk = chunk[:defer_match.start()].strip()
            # Resolve defer date
            _today = date.today()
            dl = defer_raw.lower()
            if dl in ("tmrw", "tomorrow"):
                item.defer_date = (_today + timedelta(days=1)).isoformat()
            elif dl in ("mon", "monday"):
                days_ahead = (0 - _today.weekday()) % 7 or 7
                item.defer_date = (_today + timedelta(days=days_ahead)).isoformat()
            elif dl in ("tue", "tuesday"):
                days_ahead = (1 - _today.weekday()) % 7 or 7
                item.defer_date = (_today + timedelta(days=days_ahead)).isoformat()
            elif dl in ("wed", "wednesday"):
                days_ahead = (2 - _today.weekday()) % 7 or 7
                item.defer_date = (_today + timedelta(days=days_ahead)).isoformat()
            elif dl in ("thu", "thursday"):
                days_ahead = (3 - _today.weekday()) % 7 or 7
                item.defer_date = (_today + timedelta(days=days_ahead)).isoformat()
            elif dl in ("fri", "friday"):
                days_ahead = (4 - _today.weekday()) % 7 or 7
                item.defer_date = (_today + timedelta(days=days_ahead)).isoformat()
            else:
                # Try "Mon DD" or "Month DD" (e.g. "Jun 15", "Jan 3")
                try:
                    from dateutil import parser as _dp
                    parsed = _dp.parse(defer_raw, default=datetime(_today.year, 1, 1))
                    if parsed.date() <= _today:
                        parsed = parsed.replace(year=_today.year + 1)
                    item.defer_date = parsed.date().isoformat()
                except Exception:
                    # Last resort: try as ISO date
                    try:
                        datetime.fromisoformat(defer_raw)
                        item.defer_date = defer_raw
                    except ValueError:
                        pass  # ignore unparseable defer

        # Extract #tag tokens → Toggl tags (e.g. #-2, #focus)
        tag_matches = re.findall(r"#(-?\w+)", chunk)
        if tag_matches:
            item.toggl_tags = tag_matches
            chunk = re.sub(r"\s*#-?\w+", "", chunk).strip()

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

        # Drop display-only "[1/m]" rate markers so they can't ride into the
        # habit name and break header matching (feature 2026-07-25).
        chunk = RATE_ANNOT_RE.sub("", chunk).strip()

        # Extract +N bonus points
        bonus_match = re.search(r"\+(\d+)\b", chunk)
        if bonus_match:
            item.bonus_points = int(bonus_match.group(1))
            chunk = chunk[:bonus_match.start()] + chunk[bonus_match.end():]
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
            num = int(name_parts[-1])
            remaining_name = " ".join(name_parts[:-1]).lower()
            # 4-digit HHMM for variable 0₦ habits (e.g. "xk22 1823" = started at 18:23)
            # Only for VARIABLE_0N habits where the number is always minutes, not points
            if (len(name_parts[-1]) == 4
                    and 0 <= num // 100 <= 23 and 0 <= num % 100 <= 59
                    and remaining_name in {v.lower() for v in VARIABLE_0N}):
                from datetime import datetime as _dt
                now = _dt.now()
                start_h, start_m = num // 100, num % 100
                start_min = start_h * 60 + start_m
                now_min = now.hour * 60 + now.minute
                item.time_value = max(1, now_min - start_min)
            else:
                item.time_value = num
            chunk = " ".join(name_parts[:-1])
        # Extract leading number as time value (e.g. "46 xk88") but only
        # when the remainder is a known variable 0₦ column — otherwise
        # "1 xk88" would wrongly strip the "1" instead of matching the
        # 1n+ header "1 xk88".
        elif len(name_parts) >= 2 and name_parts[0].isdigit():
            remainder = " ".join(name_parts[1:]).lower()
            if remainder in {v.lower() for v in VARIABLE_0N}:
                num = int(name_parts[0])
                if len(name_parts[0]) == 4 and 0 <= num // 100 <= 23 and 0 <= num % 100 <= 59:
                    from datetime import datetime as _dt
                    now = _dt.now()
                    start_h, start_m = num // 100, num % 100
                    start_min = start_h * 60 + start_m
                    now_min = now.hour * 60 + now.minute
                    item.time_value = max(1, now_min - start_min)
                else:
                    item.time_value = num
                chunk = " ".join(name_parts[1:])

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


def refresh_task_queue(block: bool = False) -> dict:
    """Fetch 0neon + 1neon + 夜neon + 関键路径 from Todoist, rebuild cache.
    Uses a file lock to prevent concurrent refreshes from clobbering each other.

    block=False (opportunistic callers): if another refresh holds the lock, skip
    and return the existing cache — piling up redundant refreshes is pointless.

    block=True (an EXPLICIT 'refresh now' — the --refresh-cache CLI that /0g,
    /-1g etc. rely on to surface a just-created goal): WAIT for the in-flight
    refresh, then run. Skipping here silently returned the stale cache, so a goal
    created moments earlier didn't reach dtd until the periodic daemon's next
    cycle (~3min) — the 'I did 0g but it didn't show at once' bug (2026-07-01)."""
    import fcntl
    lock_path = TASK_QUEUE_PATH.with_suffix(".lock")
    lock_fd = open(lock_path, "w")
    flags = fcntl.LOCK_EX if block else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fcntl.flock(lock_fd, flags)
    except (IOError, OSError):
        # Non-blocking only: another refresh is already running → return existing.
        print("WARN: refresh_task_queue skipped (lock held by another process)", file=sys.stderr)
        lock_fd.close()
        if TASK_QUEUE_PATH.exists():
            return json.loads(TASK_QUEUE_PATH.read_text())
        return {}
    try:
        return _refresh_task_queue_inner()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()

def _refresh_task_queue_inner() -> dict:
    """Inner refresh logic, called under file lock."""
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
                         "priority": t.get("priority", "p4"),
                         "due": t.get("due", {}).get("date", "") if t.get("due") else "",
                         "due_string": (t.get("due") or {}).get("string", "") or "",
                         "recurring": bool((t.get("due") or {}).get("is_recurring"))}
                        for t in tasks]
        except Exception as e:
            print(f"WARN: fetch {label}: {e}", file=sys.stderr)
            return []

    def fetch_today():
        """Fetch all tasks due today or overdue (no label filter), with pagination and retry."""
        from urllib.parse import quote
        all_tasks = []
        filt = "today | overdue"
        cursor = None
        for _ in range(5):  # max 5 pages
            url = f"{TODOIST_BASE}/tasks/filter?query={quote(filt)}&limit=200"
            if cursor:
                url += f"&cursor={cursor}"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
            })
            success = False
            for attempt in range(3):  # retry each page up to 3 times
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        raw = json.loads(resp.read())
                    success = True
                    break
                except Exception as e:
                    print(f"WARN: fetch today page (attempt {attempt+1}): {e}", file=sys.stderr)
                    if attempt < 2:
                        import time; time.sleep(1)
            if not success:
                return None  # signal total failure
            tasks = raw if isinstance(raw, list) else raw.get("results", [])
            for t in tasks:
                all_tasks.append({
                    "id": t.get("id", ""),
                    "content": t.get("content", ""),
                    "labels": t.get("labels", []),
                    "priority": t.get("priority", "p4"),
                    "due": t.get("due", {}).get("date", "") if t.get("due") else "",
                    "due_string": (t.get("due") or {}).get("string", "") or "",
                    "recurring": bool(t.get("due", {}).get("is_recurring")) if t.get("due") else False,
                })
            cursor = raw.get("next_cursor") if isinstance(raw, dict) else None
            if not cursor:
                break
        return all_tasks

    # Fetch labels in parallel, then fetch today AFTER (not concurrent).
    # Running all 6+ requests simultaneously triggers Todoist rate limiting,
    # which intermittently returns empty results for fetch_today.
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_label, lbl): key for lbl, key in zip(labels, keys)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    # Now fetch today sequentially (no rate-limit contention)
    today_result = fetch_today()
    # Filter out tasks with due date in the future — Todoist's "today | overdue"
    # returns recurring tasks whose next occurrence is future, inflating the count.
    if today_result:
        today_iso = datetime.now().strftime("%Y-%m-%d")
        today_result = [t for t in today_result
                        if t.get("due") and t["due"] <= today_iso]

    # Atomic write: only update "today" if the fetch fully succeeded
    # Protect "today": if fetch returned empty/None but old cache had data, retry once then keep old
    old_cache: dict = {}
    if TASK_QUEUE_PATH.exists():
        try:
            old_cache = json.loads(TASK_QUEUE_PATH.read_text())
        except Exception:
            pass
    old_today = old_cache.get("today", [])

    # Keep-old guard for the label buckets (2026-07-26): under rate limiting
    # Todoist intermittently returns EMPTY results with a 200 — the exact
    # failure mode fetch_today already guards. An empty label bucket written
    # here wiped every row of that tier from dtd until the next refresh
    # (user report: "complete one -1n task, the others disappear for ~10s" —
    # same class, see the -1neon union guard below). An empty fetch with a
    # non-empty old bucket is far more likely a flake than a real all-closed.
    for _k in keys:
        if not results.get(_k) and old_cache.get(_k):
            print(f"WARN: {_k} fetch empty, keeping {len(old_cache[_k])} cached", file=sys.stderr)
            results[_k] = old_cache[_k]

    if today_result and len(today_result) > 0:
        results["today"] = today_result
    elif old_today:
        # Retry once before giving up
        print(f"WARN: fetch_today returned {len(today_result) if today_result is not None else 'None'}, retrying...", file=sys.stderr)
        import time; time.sleep(2)
        retry_result = fetch_today()
        if retry_result and len(retry_result) > 0:
            results["today"] = retry_result
            print(f"  retry succeeded: {len(retry_result)} tasks", file=sys.stderr)
        else:
            results["today"] = old_today
            print(f"  retry also failed, keeping {len(old_today)} from cache", file=sys.stderr)
    else:
        results["today"] = today_result or []

    # Union the -1neon block-ritual cards in via the DIRECT label endpoint.
    # The daemon (re)creates these 5 cards at each 2h boundary; they reach the
    # cache ONLY through fetch_today's "today | overdue" FILTER query, whose
    # index lags minutes behind task creation — so a boundary refresh fired
    # ~30-90s after the daemon made the cards returned stale results and the new
    # block's -1n cards didn't surface in dtd until the next ~3min daemon cycle
    # (the "dtd won't auto-refresh at block turnover" bug, 2026-07-09). The
    # /tasks?label=-1neon endpoint is fresh within seconds, so fetching it
    # directly and merging (dedup by id, due<=today) closes the gap. -1neon is
    # deliberately NOT a top-level cache key (dtd reads ritual cards from
    # `today`), so union rather than add a bucket.
    try:
        today_iso = datetime.now().strftime("%Y-%m-%d")
        neg1 = fetch_label("-1neon")
        if not neg1:
            # Empty label fetch + lagging filter = every OTHER ritual card
            # vanishes from dtd until the next refresh (bug 2026-07-26:
            # completing one -1n hid the rest for ~10s). Rate limiting is the
            # likely cause — the completion-time refresh follows a burst of
            # close/stamp API calls. Carry the old cache's ritual cards
            # forward instead; genuinely-closed ones stay hidden via the
            # completed-today id overlay, and the next clean refresh prunes.
            neg1 = [t for t in old_today if "-1neon" in (t.get("labels") or [])]
            if neg1:
                print(f"WARN: -1neon fetch empty, carrying {len(neg1)} cached card(s)", file=sys.stderr)
        else:
            # Partial-fetch erosion guard (2026-07-28): a 5xx/rate storm can
            # also return a strict SUBSET with a 200, which sails past the
            # empty-only guard above and shrinks the cached ritual set on
            # every refresh (this morning's storm eroded it to 1 card —
            # "-1n tasks disappeared from dtd after completing a task").
            # Union the old cache's ritual cards back in (dedup by id),
            # pruning ids recorded closed in completed-today (run_ritual
            # records them on a successful close) — but only while the old
            # cache was written in the SAME 2h block, so cards the daemon
            # retired at a boundary never outlive their block.
            _now = datetime.now()
            try:
                _upd = datetime.fromisoformat(old_cache.get("updated", ""))
                same_block = (_upd.date() == _now.date()
                              and _upd.hour // 2 == _now.hour // 2)
            except (ValueError, TypeError):
                same_block = False
            if same_block:
                closed_ids: set = set()
                try:
                    _ctj = mc._load(mc.COMPLETED)
                    if _ctj.get("date") == today_iso:
                        closed_ids = {str(v) for v in (_ctj.get("ids") or {}).values()}
                except Exception:
                    pass
                _have_neg1 = {t.get("id") for t in neg1}
                carried = [t for t in old_today
                           if "-1neon" in (t.get("labels") or [])
                           and t.get("id") not in _have_neg1
                           and str(t.get("id")) not in closed_ids]
                if carried:
                    print(f"WARN: -1neon fetch partial ({len(neg1)}), carrying "
                          f"{len(carried)} more cached card(s)", file=sys.stderr)
                    neg1 = neg1 + carried
        have = {t.get("id") for t in results["today"]}
        for t in neg1:
            if t.get("id") in have:
                continue
            if t.get("due") and t["due"] <= today_iso:
                results["today"].append(t)
                have.add(t.get("id"))
    except Exception as e:
        print(f"WARN: -1neon union skipped: {e}", file=sys.stderr)

    # Atomic file write: write to temp, then rename
    tmp_path = TASK_QUEUE_PATH.with_suffix(".tmp")
    cache = {"updated": datetime.now().isoformat()}
    cache.update(results)
    # Attach Haiku short names so a ctrl-r / startup refresh keeps the compact
    # display instead of reverting to the long names (which overflow the border).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import shorten
        shorten.attach_to_cache(cache)
    except Exception as e:
        print(f"shorten skipped: {e}", file=sys.stderr)
    tmp_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n")
    tmp_path.rename(TASK_QUEUE_PATH)
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
    is_variable_1n: bool = False
    variable_value: Optional[int] = None
    error: Optional[str] = None


def route_items(items: list[ParsedItem], headers: dict, tq: dict,
                skip_todoist: bool = False,
                preferred_id: str | None = None) -> list[RouteResult]:
    """Route each item through 0₦ → 1n+ → Todoist → variable.

    skip_todoist=True bypasses the Todoist match/close and build-order steps
    (0.3/0.35/0.37) and routes straight to the variable path. Used by dtd's
    split flow, which has already handled the Todoist task itself and only
    wants the points logged — matching here would re-find the just-renamed
    remainder task and close it.
    """
    h0n = headers.get("0n", {})
    h1n = headers.get("1n", {})
    # Build hyphen/space-tolerant lookups so `wake-up` matches header `wake up`
    # (and vice versa). Headers may already be stored lowercased; we re-normalize
    # the keys so any internal hyphens or extra whitespace also collapse.
    h0n_norm = {header_normalize(k): v for k, v in h0n.items()}
    h1n_norm = {header_normalize(k): v for k, v in h1n.items()}
    all_tasks = tq.get("0neon", []) + tq.get("夜neon", []) + tq.get("1neon", [])
    results = []

    # "bigs" = time with both big kids → split the minutes between xk20 (Theo)
    # and xk22 (Ren), then let the normal 0₦ path handle each. Odd minute goes
    # to xk20. e.g. `did bigs 26` → xk20 +13, xk22 +13.
    expanded: list[ParsedItem] = []
    for item in items:
        if item.name.strip().lower() == "bigs":
            if item.time_range:
                total = time_range_minutes(item.time_range[0], item.time_range[1])
            elif item.time_value is not None:
                total = item.time_value
            else:
                total = 1
            expanded.append(ParsedItem(raw=item.raw, name="xk20",
                                       time_value=(total + 1) // 2,
                                       target_date=item.target_date))
            expanded.append(ParsedItem(raw=item.raw, name="xk22",
                                       time_value=total // 2,
                                       target_date=item.target_date))
        else:
            expanded.append(item)
    items = expanded

    for item in items:
        name_lower = item.name.lower()
        name_norm = header_normalize(item.name)

        # Step 0.1: 0₦ match (hyphen/space-insensitive)
        if name_norm in h0n_norm:
            today_md = item.target_date or f"{date.today().month}/{date.today().day}"
            today_date = date.today()
            # Past date → needs agent (posthoc flow), UNLESS dtd passed a
            # specific task_id (preferred_id): that means the user is
            # completing an EXACT, already-open Todoist task they're looking
            # at (e.g. a manually-created "<habit> M/D" catch-up placeholder
            # whose bare name happens to collide with a 0₦ header once the
            # date suffix is parsed off) — not asking to abstractly log the
            # habit for some date. dtd's worker calls this CLI directly with
            # no live agent to catch needs_agent, so that path just sat there
            # forever no matter how many times it was "completed" (bug
            # 2026-07-19: "ibx i9 tasks marked done twice, still here" — 5
            # stray one-off catch-up tasks that could never close). Resolve
            # and close that specific task instead; no Neon write (same as
            # the agent's Step 6b) since it's not today's occurrence.
            target_parts = today_md.split("/")
            if len(target_parts) == 2:
                t_month, t_day = int(target_parts[0]), int(target_parts[1])
                if (t_month, t_day) != (today_date.month, today_date.day):
                    fetched = _fetch_task_by_id(preferred_id) if preferred_id else None
                    if fetched:
                        r = RouteResult(item=item, step="variable",
                                        fen_col=None, fen_points=0)
                        r.todoist_task = fetched
                        results.append(r)
                    else:
                        r = RouteResult(item=item, step="needs_agent",
                                        error="past date requires posthoc flow")
                        results.append(r)
                    continue

            col = h0n_norm[name_norm]
            if item.time_range:
                val = time_range_minutes(item.time_range[0], item.time_range[1])
            elif item.time_value is not None:
                val = item.time_value
            else:
                # No typed value: record how long the habit actually took,
                # from today's same-named Toggl entries (2026-07-24).
                val = toggl_minutes_for(item.name) or 1
            # Cumulative columns: add to existing (handled in AppleScript)
            is_cumulative = item.name in CUMULATIVE_0N

            r = RouteResult(item=item, step="0n", col_num=col, write_value=val)

            # Find matching Todoist task to close
            neon_tasks = tq.get("0neon", []) + tq.get("夜neon", [])
            matched = match_todoist_task(item.name, neon_tasks, preferred_id=preferred_id)
            if matched:
                r.todoist_task = matched
                # By default, 0n habits do NOT write to 0分: Excel's own
                # formulas roll up 0n data into 0分, and writing here
                # would double-count.

            # +N bonus points: write duration + bonus to 0分
            if item.bonus_points:
                proj = HABIT_PROJECT.get(name_lower)
                fen_col_bonus = LABEL_TO_0FEN.get(proj) if proj else None
                if fen_col_bonus:
                    r.fen_col = fen_col_bonus
                    r.fen_points = val + item.bonus_points

            # Exception: explicit [N] override on a 0₦ habit appends +N
            # to that habit's domain column in 0分 — a deliberate boost
            # on top of the rollup. Triggered ONLY by [N], not {N}: {N}
            # already has its own 0g-column path (col Z) and would
            # double-write here.
            if item.points_override and item.curly_points is None:
                proj = HABIT_PROJECT.get(name_lower)
                fen_col_override = LABEL_TO_0FEN.get(proj) if proj else None
                if fen_col_override:
                    r.fen_col = fen_col_override
                    r.fen_points = item.points_override

            results.append(r)
            continue

        # Step 0.2: 1n+ match (now handled in fast path)
        # Check aliases first (e.g. "1 hcbp" → "1 hcb", "家" → "family")
        resolved_1n_raw = ONENEON_ALIASES.get(name_lower, name_lower)
        resolved_1n = header_normalize(resolved_1n_raw)
        if resolved_1n in h1n_norm:
            col_letter = h1n_norm[resolved_1n]
            fen_col = ONENEON_TO_0FEN.get(resolved_1n)
            if fen_col is None:
                # Generic fallback (bug 2026-07-27: "1 m5x2" missing from the
                # hand-kept map → its points never reached 0分): "1 <domain>"
                # names resolve via the domain token, same as route.py does
                # through the registry.
                fen_col = LABEL_TO_0FEN.get(resolved_1n.split()[-1])
            is_cumul = resolved_1n in CUMULATIVE_1N
            is_var = resolved_1n in VARIABLE_1N
            # 2026-07-27 redesign: the WEEK CELL records MINUTES (explicit
            # value/range > today's matching Toggl entries > 1); the habit's
            # POINTS go to today's 0分 domain column instead (row-5 expected
            # points for standard habits, base+rate×minutes for variable).
            minutes = None
            if item.time_range:
                minutes = time_range_minutes(*item.time_range)
            elif item.time_value is not None:
                minutes = item.time_value
            else:
                minutes = toggl_minutes_for(item.name)
            cell_minutes = minutes if minutes else 1
            var_val = None
            if is_var:
                if item.points_override:
                    var_val = item.points_override
                elif minutes is not None:
                    var_val = variable_1n_points(resolved_1n, minutes)
                else:
                    var_val = (VARIABLE_1N_DEFAULTS.get(resolved_1n)
                               or VARIABLE_1N_BASES.get(resolved_1n) or None)
                if var_val and item.bonus_points:
                    var_val += item.bonus_points
            r = RouteResult(item=item, step="1n", col_letter=col_letter,
                            fen_col=fen_col,
                            write_value=cell_minutes,
                            is_cumulative_1n=is_cumul,
                            cumulative_increment=CUMULATIVE_1N.get(resolved_1n, 0),
                            is_variable_1n=is_var,
                            variable_value=var_val)
            # Find matching Todoist 1neon task to close
            neon_1n_tasks = tq.get("1neon", [])
            matched = match_todoist_task(item.name, neon_1n_tasks, preferred_id=preferred_id)
            if matched:
                r.todoist_task = matched
            results.append(r)
            continue

        # Step 0.3: Todoist match
        matched = None if skip_todoist else match_todoist_task(item.name, all_tasks, preferred_id=preferred_id)
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

        # Step 0.35: Live Todoist search (fallback when cache misses)
        # Searches all open tasks by text, not just neon-labeled ones.
        live_matched = None if skip_todoist else _live_todoist_search(item.name)
        if live_matched:
            pts_match = POINTS_RE.search(live_matched["content"])
            points = item.points_override or (int(pts_match.group(1)) if pts_match else 0)
            fen_col = None
            for lbl in live_matched.get("labels", []):
                if lbl in LABEL_TO_0FEN:
                    fen_col = LABEL_TO_0FEN[lbl]
                    break
            r = RouteResult(item=item, step="todoist", todoist_task=live_matched,
                            fen_col=fen_col, fen_points=points)
            results.append(r)
            continue

        # Step 0.37: Build order -1₲ goal match
        # If the input matches an unchecked goal in the build order, flip it
        # and write {N} bonus points to 0分 column Z.
        bo_path = Path.home() / "vault/g245/5e-1/build-order.md"
        if not skip_todoist and bo_path.exists():
            bo_text = bo_path.read_text()
            if "## -1₲" in bo_text:
                bo_lines = bo_text.split("\n")
                in_1g = False
                for bi, bline in enumerate(bo_lines):
                    if bline.strip() == "## -1₲":
                        in_1g = True
                        continue
                    if in_1g and bline.startswith("## "):
                        break
                    if not in_1g:
                        continue
                    if re.match(r"^    - \[ \] .+", bline):
                        goal_text = bline.strip()[6:]  # strip "- [ ] "
                        # Extract {N} from goal
                        curly = re.search(r"\{(\d+)\}", goal_text)
                        bare_goal = re.sub(r"\s*\{(\d+)\}", "", goal_text).strip()
                        q_tokens = tokenize(item.name)
                        g_tokens = tokenize(bare_goal)
                        ratio = overlap_ratio(q_tokens, g_tokens) if q_tokens else 0
                        if ratio >= 0.6 or bare_goal.lower().startswith(item.name.lower()):
                            # Flip checkbox
                            bo_lines[bi] = bline.replace("- [ ]", "- [x]", 1)
                            bo_path.write_text("\n".join(bo_lines))
                            bonus = int(curly.group(1)) if curly else 0
                            r = RouteResult(item=item, step="build_order",
                                            fen_col="Z" if bonus else None,
                                            fen_points=bonus)
                            results.append(r)
                            break
                else:
                    pass  # no match; fall through to step 0.4
                if results and results[-1].item is item:
                    continue  # matched in build order

        # Step 0.4: Variable task
        # Resolve domain from @project override, keyword map, or bail
        domain_label, fen_col = None, None
        if item.project_override and item.project_override in LABEL_TO_0FEN:
            domain_label = item.project_override
            fen_col = LABEL_TO_0FEN[item.project_override]
        elif name_lower in VARIABLE_DOMAIN:
            domain_label, fen_col = VARIABLE_DOMAIN[name_lower]
        else:
            # Try "N domain" pattern: e.g. "1 hcbp", "5 i9"
            var_parts = name_lower.split()
            if (len(var_parts) == 2 and var_parts[0].isdigit()
                    and var_parts[1] in LABEL_TO_0FEN):
                domain_label = var_parts[1]
                fen_col = LABEL_TO_0FEN[var_parts[1]]
                item.points_override = int(var_parts[0])
            else:
                r = RouteResult(item=item, step="needs_agent",
                                error="no match, needs domain disambiguation")
                results.append(r)
                continue

        # Compute points: explicit [N] > typed minutes (e.g. "bball 30") >
        # time_range duration > 0. Honoring time_value here keeps the manual
        # path ("bball 30") and the timer path (apply_timer_minutes) in sync.
        pts = item.points_override or item.time_value or 0
        if pts == 0 and item.time_range:
            pts = time_range_minutes(item.time_range[0], item.time_range[1])
        if item.bonus_points:
            pts += item.bonus_points

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
    pre_lines = []
    for w in writes:
        is_cumulative = w.item.name in CUMULATIVE_0N
        col = w.col_num
        val = w.write_value
        # Pre-image capture (for ctrl-z undo): read the cell BEFORE writing.
        pre_lines.append(f'''    set pv{col} to value of cell {col} of row todayRow of ws
    if pv{col} is missing value then
        set preOut to preOut & "PRE" & (character id 9) & "{col}" & (character id 9) & linefeed
    else
        set preOut to preOut & "PRE" & (character id 9) & "{col}" & (character id 9) & (pv{col} as text) & linefeed
    end if''')
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
    set preOut to ""
{chr(10).join(pre_lines)}
{chr(10).join(set_lines)}
    set results to ""
{chr(10).join(verify_lines)}
    return "OK:row=" & todayRow & "|" & results & linefeed & preOut
end tell'''
    return script


def _did_src(names: list[str]) -> str:
    """Ledger `src` label for a /did credit: the habit/task names earning it."""
    uniq = list(dict.fromkeys(n for n in names if n))
    if not uniq:
        return "did"
    if len(uniq) == 1:
        return f"did {uniq[0]}"
    return "did batch: " + ",".join(uniq)


def _warn_chain_broken(resp: dict, sheet: str = "0分") -> None:
    """Warn-only chain feedback from the excel-http daemon: "broken" means the
    cell was modified outside the daemon since its last ledger entry. Surface
    it on stderr; never fail the write it accompanies."""
    cols: list[str] = []
    if resp.get("chain") == "broken" and resp.get("col"):
        cols.append(resp["col"])
    for c in resp.get("chain_broken_cols") or []:
        if c not in cols:
            cols.append(c)
    for item in resp.get("results") or []:
        if isinstance(item, dict) and item.get("chain") == "broken":
            c = item.get("col")
            if c and c not in cols:
                cols.append(c)
    for c in cols:
        print(f"⚠ {sheet}!{c} chain broken — cell modified outside daemon",
              file=sys.stderr)


def append_0fen_batch(appends: list[tuple[str, object]], target_date: str,
                      src_names: list[str], tag: str) -> subprocess.CompletedProcess:
    """Batch 0分 appends via the excel-http daemon — ONE round-trip (the row
    is resolved once server-side; each append is journaled + chain-checked).

    `appends` = [(col, value)] where value is a numeric point count (→ "+N")
    or a ready-made append string (e.g. "+'1n+'!K5"), passed through as-is.
    Empty-cell / bare-number / formula normalization happens server-side.

    Returns a CompletedProcess shaped like the old ix_run(osascript) result so
    the output builder and error handling downstream stay unchanged:
      rc 0, stdout "OK:<tag> row=N"                — all appends landed
      rc 0, stdout "ERROR: date … not found in 0分" — same string the old
                                                     AppleScript returned
      rc 1, stderr <error>                          — daemon/transport failure
    """
    values = [(col, v if isinstance(v, str) else f"+{v}") for col, v in appends]
    try:
        resp = neon_excel.batch_append("0分", values, date=target_date,
                                       src=_did_src(src_names))
    except Exception as e:  # noqa: BLE001 — surface as the old rc!=0 path
        return subprocess.CompletedProcess("excel-http", 1, stdout="", stderr=str(e))
    _warn_chain_broken(resp)
    sub_results = [r for r in resp.get("results") or [] if isinstance(r, dict)]
    if resp.get("ok"):
        row = resp.get("row")
        if row is None:  # ssh fallback: per-cell results carry the row
            row = next((r.get("row") for r in sub_results if r.get("row")), "?")
        return subprocess.CompletedProcess("excel-http", 0,
                                           stdout=f"OK:{tag} row={row}", stderr="")
    err = str(resp.get("error")
              or next((r.get("error") for r in sub_results if r.get("error")), "")
              or "excel-http batch append failed")
    if "date_not_found" in err:
        return subprocess.CompletedProcess(
            "excel-http", 0,
            stdout=f"ERROR: date {target_date} not found in 0分", stderr="")
    return subprocess.CompletedProcess("excel-http", 1, stdout="", stderr=err)


def build_0l_time_script(target_date: str) -> str:
    """0l special case: stamp the completion time (HHMM) into 0n's
    "N Color" column (cell 32) for the target date's row. 0n write —
    stays raw AppleScript (only 0fen writes are daemon-routed)."""
    return f'''tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set targetMonth to {target_date.split("/")[0]}
    set targetDay to {target_date.split("/")[1]}
    set todayRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = targetMonth and d = targetDay then
                    set todayRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if todayRow = 0 then return "SKIP: date not found"
    set h to hours of (current date)
    set mn to minutes of (current date)
    set timeStr to (h * 100 + mn)
    set value of cell 32 of row todayRow of theSheet to timeStr
    return "OK: N Color=" & timeStr & " row=" & todayRow
end tell'''


def build_hcbi_script(appends: list[tuple[str, int]], target_date: str) -> Optional[str]:
    """Build AppleScript for batch hcbi appends. appends = [(col_letter, minutes), ...]
    Uses column B for date lookup (M/D format), appends +N to formula."""
    if not appends:
        return None

    append_lines = []
    for col, mins in appends:
        append_lines.append(f'''    set theCell to range ("{col}" & todayRow) of ws
    set oldFormula to formula of theCell
    if oldFormula = "" or oldFormula = "0" then
        set formula of theCell to "=0+{mins}"
    else if character 1 of oldFormula is not "=" then
        set formula of theCell to "=" & oldFormula & "+{mins}"
    else
        set formula of theCell to oldFormula & "+{mins}"
    end if''')

    script = f'''tell application "Microsoft Excel"
    set ws to sheet "hcbi" of workbook "Neon分v12.2.xlsx"
    set todayRow to 0
    repeat with i from 2 to 500
        if (string value of range ("B" & i) of ws) = "{target_date}" then
            set todayRow to i
            exit repeat
        end if
    end repeat
    if todayRow = 0 then return "ERROR: date {target_date} not found in hcbi"
{chr(10).join(append_lines)}
    return "OK:hcbi row=" & todayRow
end tell'''
    return script


def build_1n_script(writes: list[RouteResult], week_mw: str) -> Optional[str]:
    """Build AppleScript for batch 1n+ writes. Finds week row, reads row 3 points, writes."""
    if not writes:
        return None

    # Build per-column write + verify lines
    write_lines = []
    verify_lines = []
    pre_lines = []
    for w in writes:
        col = w.col_letter
        # Pre-image capture (for ctrl-z undo): formula text before writing.
        pre_lines.append(f'''    try
        set pf{col} to (formula of range ("{col}" & weekRow) of ws1n) as text
    on error
        set pf{col} to ""
    end try
    set preOut to preOut & "PRE" & (character id 9) & "{col}" & (character id 9) & pf{col} & linefeed''')
        if w.is_cumulative_1n:
            inc = w.cumulative_increment
            write_lines.append(f'''    set theCellCum to range ("{col}" & weekRow) of ws1n
    set oldFormulaCum to formula of theCellCum
    if oldFormulaCum = "" or oldFormulaCum = "0" then
        set formula of theCellCum to "=0+{inc}"
    else if character 1 of oldFormulaCum is not "=" then
        set formula of theCellCum to "=" & oldFormulaCum & "+{inc}"
    else
        set formula of theCellCum to oldFormulaCum & "+{inc}"
    end if''')
        elif w.is_variable_1n:
            # Variable 1n+ tasks: append MINUTES to the formula so a week of
            # repeated sessions accumulates (2026-07-27 redesign — the cell
            # records time; points go to 0fen separately).
            val = w.write_value or 1
            write_lines.append(f'''    set theCell1n to range ("{col}" & weekRow) of ws1n
    set oldFormula1n to formula of theCell1n
    if oldFormula1n = "" or oldFormula1n = "0" then
        set formula of theCell1n to "=0+{val}"
    else if character 1 of oldFormula1n is not "=" then
        set formula of theCell1n to "=" & oldFormula1n & "+{val}"
    else
        set formula of theCell1n to oldFormula1n & "+{val}"
    end if''')
        else:
            # 2026-07-27 redesign: the cell records the MINUTES the habit took
            # (1 when unknown), never the points — those land on 0fen via the
            # row-5 expected-points reference (step 4c's append_0fen_batch).
            write_lines.append(
                f'''    set value of range ("{col}" & weekRow) of ws1n to {w.write_value or 1}''')
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
    -- NO same-month fallback: when the new week's row doesn't exist yet
    -- (Sunday, before the row is added), the old fallback silently credited
    -- LAST week's row — backward-looking data corruption (2026-07-27: a
    -- Sunday /1-2g landed on the 7.3 row). Failing loudly is recoverable;
    -- a wrong-week write is invisible.
    if weekRow = 0 then return "ERROR: week {week_mw} not found"
    set preOut to ""
{chr(10).join(pre_lines)}
{chr(10).join(write_lines)}
    set results to "weekRow=" & weekRow & "|"
{chr(10).join(verify_lines)}
    return "OK:" & results & linefeed & preOut
end tell'''
    return script


def parse_pre_lines(stdout: str) -> dict[str, str]:
    """Parse 'PRE\\t<col>\\t<value>' pre-image lines emitted by the 0n/1n+
    write scripts. Returns {col_key: prev_value_text}; missing value → ''."""
    pre: dict[str, str] = {}
    for line in stdout.splitlines():
        if line.startswith("PRE\t"):
            parts = line.split("\t", 2)
            if len(parts) >= 2:
                pre[parts[1]] = parts[2] if len(parts) > 2 else ""
    return pre


# ---------------------------------------------------------------------------
# Toggl timer stop
# ---------------------------------------------------------------------------

TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"


def _toggl_api():
    """Load toggl_cli.py's own toggl_api handle (importlib, same pattern as the
    ix_osa/mark-completed imports above) instead of duplicating its API-key
    loading and sys.path setup here. Lazy: only paid by callers that actually
    touch Toggl (trim/overlap checks), not --refresh-cache/--ritual runs."""
    spec = importlib.util.spec_from_file_location("toggl_cli_lib", TOGGL_CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.toggl_api


def _trim_toggl_range(start_dt: datetime, end_dt: datetime) -> list[str]:
    """Ensure no existing Toggl entry keeps covering [start_dt, end_dt)
    before a new entry lands there. Thin delegate to toggl_api.trim_range
    (promoted out of here 2026-07-19 once janus.py's entry-edit-to-a-new-
    time feature needed the identical overlap-cleanup logic — originally
    added here 2026-07-16 after backfilling "asha" then "asha prep" over the
    same half hour double-counted both time and points)."""
    return _toggl_api().trim_range(start_dt, end_dt)


def _parse_stop_minutes(output: str) -> Optional[int]:
    """Parse duration minutes from toggl_cli stop output.

    Handles '(39m)', '(48min)', '(1h03m)', '(2h)'. Returns None if absent.
    """
    m = re.search(r"\((?:(\d+)h)?(\d+)m(?:in)?\)", output)
    if m:
        return int(m.group(1) or 0) * 60 + int(m.group(2))
    m = re.search(r"\((\d+)h\)", output)
    if m:
        return int(m.group(1)) * 60
    return None


def apply_timer_minutes(results: list, toggl_stop: Optional[dict]) -> None:
    """Backfill variable-task values from the stopped Toggl timer.

    Variable tasks picked without an explicit time (e.g. from dtd) default to
    1 minute (0n) or a static default (1n+). If the timer we just stopped
    matches the task, its elapsed minutes are the time the user would have
    typed — use them. Explicit user-provided values always win.
    """
    if not toggl_stop or not toggl_stop.get("minutes"):
        return
    mins = toggl_stop["minutes"]
    desc = toggl_stop.get("description", "").lower()
    var_0n = {v.lower() for v in VARIABLE_0N}
    for r in results:
        if r.item.name.lower() != desc:
            continue
        explicit = (r.item.time_value is not None
                    or r.item.points_override is not None
                    or r.item.time_range)
        if explicit:
            continue
        if r.step == "0n" and r.item.name.lower() in var_0n and r.write_value == 1:
            r.write_value = mins
            r.item.time_value = mins
        elif r.step == "1n" and getattr(r, "is_variable_1n", False):
            # Same base + rate×minutes formula as the routing path — raw
            # minutes would over/under-credit rated habits (长冥想 .5/m,
            # AoS 15+1/m). The week CELL takes the raw minutes (write_value,
            # 2026-07-27 redesign); the computed points go to 0分.
            resolved = header_normalize(
                ONENEON_ALIASES.get(r.item.name.lower(), r.item.name.lower()))
            r.variable_value = (variable_1n_points(resolved, mins)
                                + (r.item.bonus_points or 0))
            r.write_value = mins
            r.item.time_value = mins
        elif r.step == "variable" and r.item.name.lower() in VARIABLE_DOMAIN:
            # bball/run/walk/nap/etc.: points = elapsed minutes (+ any bonus).
            # Without this they log 0 when completed from dtd with a timer up.
            r.fen_points = mins + (r.item.bonus_points or 0)
            r.item.time_value = mins


def stop_matching_toggl(item_names: list[str]) -> Optional[dict]:
    """If a running Toggl timer matches any item name, stop it.

    Returns dict with timer info (including parsed duration "minutes")
    if stopped, None otherwise.
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
                "output": stop_proc.stdout.strip(),
                "minutes": _parse_stop_minutes(stop_proc.stdout)}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Todoist close
# ---------------------------------------------------------------------------

def _todoist_request(url: str, method: str = "GET", timeout: float = 15.0):
    """Wrapped HTTP call returning (status, body_bytes) or raising."""
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {TODOIST_TOKEN}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _classify_error(e: Exception) -> tuple[str, bool]:
    """Return (error_string, is_transient). Transient → retry once."""
    import socket
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body = ""
        is_transient = e.code >= 500
        return f"HTTP {e.code}: {body}".strip(), is_transient
    if isinstance(e, urllib.error.URLError):
        return f"URLError: {e.reason!r}", True
    if isinstance(e, socket.timeout):
        return "timeout", True
    return repr(e), False


def _fetch_task_by_id(task_id: str) -> Optional[dict]:
    """Fetch a single task by id, shaped like the other task dicts in this
    file (id/content/labels/due/due_string/recurring). None on any failure."""
    try:
        status, body = _todoist_request(f"{TODOIST_BASE}/tasks/{task_id}")
        if status != 200:
            return None
        t = json.loads(body)
        due = t.get("due") or {}
        return {
            "id": t.get("id", ""),
            "content": t.get("content", ""),
            "labels": t.get("labels", []),
            "due": due.get("date", ""),
            "due_string": due.get("string", "") or "",
            "recurring": bool(due.get("is_recurring")),
        }
    except Exception:
        return None


def _verify_closed(task_id: str) -> tuple[bool, str | None]:
    """Read back the task; return (closed_ok, error). 404 = closed (archived).

    Recurring tasks are tricky: completing one increments due date instead of
    archiving, so `checked` may stay false. We treat those as ok if the GET
    succeeds and `due.is_recurring` is true.
    """
    try:
        status, body = _todoist_request(f"{TODOIST_BASE}/tasks/{task_id}", "GET")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, None  # archived
        msg, _ = _classify_error(e)
        return False, f"verify_failed: {msg}"
    except Exception as e:
        msg, _ = _classify_error(e)
        return False, f"verify_failed: {msg}"

    try:
        data = json.loads(body)
    except Exception as e:
        return False, f"verify_failed: bad json {e!r}"

    if data.get("checked") is True:
        return True, None
    due = data.get("due") or {}
    if due.get("is_recurring"):
        return True, None  # recurring tasks reschedule, not archive
    return False, "verify_failed: task still open after close"


def _live_todoist_search(query: str) -> Optional[dict]:
    """Search all open Todoist tasks by text. Returns best match or None.

    Used as a fallback when the neon-labeled task cache misses. Paginates
    through today + overdue + upcoming (7 days) tasks and applies the same
    word-overlap matching as the cache path to avoid false positives.
    """
    try:
        from urllib.parse import quote
        all_tasks: list[dict] = []
        # Fetch today+overdue and next 7 days in two calls
        for filt in ("today | overdue", "7 days"):
            cursor = None
            for _ in range(3):  # max 3 pages per filter
                url = f"{TODOIST_BASE}/tasks/filter?query={quote(filt)}&limit=100"
                if cursor:
                    url += f"&cursor={cursor}"
                req = urllib.request.Request(url, headers={
                    "Authorization": f"Bearer {TODOIST_TOKEN}",
                })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = json.loads(resp.read())
                tasks = raw if isinstance(raw, list) else raw.get("results", [])
                for t in tasks:
                    all_tasks.append({
                        "id": t.get("id", ""),
                        "content": t.get("content", ""),
                        "labels": t.get("labels", []),
                        # Carry due info so the future-due close guard in main()
                        # works for live-search matches too (it reads .due).
                        "due": (t.get("due") or {}).get("date", ""),
                        "due_string": (t.get("due") or {}).get("string", "") or "",
                        "recurring": bool((t.get("due") or {}).get("is_recurring")),
                    })
                cursor = raw.get("next_cursor") if isinstance(raw, dict) else None
                if not cursor:
                    break
        if not all_tasks:
            return None
        # Deduplicate by id
        seen = set()
        unique = []
        for t in all_tasks:
            if t["id"] not in seen:
                seen.add(t["id"])
                unique.append(t)
        best = match_todoist_task(query, unique)
        return best
    except Exception:
        return None


def defer_todoist_task(task_id: str, defer_date: str, points_claimed: int,
                       current_content: str) -> tuple[bool, str | None]:
    """Reschedule a task and deduct claimed points from its [N] value."""
    # 1. Reschedule to defer_date
    try:
        body = json.dumps({"due_date": defer_date}).encode()
        req = urllib.request.Request(
            f"{TODOIST_BASE}/tasks/{task_id}",
            data=body,
            headers={
                "Authorization": f"Bearer {TODOIST_TOKEN}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        return False, f"reschedule failed: {e}"

    # 2. Deduct points from [N] in the task content
    if points_claimed > 0:
        pts_match = re.search(r"\[(\d+)\]", current_content)
        if pts_match:
            old_pts = int(pts_match.group(1))
            new_pts = max(0, old_pts - points_claimed)
            new_content = current_content[:pts_match.start()] + f"[{new_pts}]" + current_content[pts_match.end():]
            try:
                body = json.dumps({"content": new_content}).encode()
                req = urllib.request.Request(
                    f"{TODOIST_BASE}/tasks/{task_id}",
                    data=body,
                    headers={
                        "Authorization": f"Bearer {TODOIST_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=15)
            except Exception:
                pass  # content update is best-effort

    return True, None


def close_todoist_task(task_id: str, _retry: bool = True) -> tuple[str, bool, str | None]:
    """POST /tasks/{id}/close, then verify. Returns (id, ok, error)."""
    url = f"{TODOIST_BASE}/tasks/{task_id}/close"
    try:
        _todoist_request(url, method="POST")
    except Exception as e:
        msg, transient = _classify_error(e)
        if transient and _retry:
            import time
            time.sleep(0.5)
            return close_todoist_task(task_id, _retry=False)
        return task_id, False, msg

    ok, verr = _verify_closed(task_id)
    return task_id, ok, verr


def close_todoist_tasks(task_ids: list[str]) -> dict[str, tuple[bool, str | None]]:
    if not task_ids:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(close_todoist_task, tid): tid for tid in task_ids}
        for future in as_completed(futures):
            tid, ok, err = future.result()
            results[tid] = (ok, err)
    return results


# ---------------------------------------------------------------------------
# d359 outreach tasks: completing one via /did means contact happened.
# ---------------------------------------------------------------------------

S897_UPDATE = Path.home() / "i446-monorepo/tools/d359/s897_update.py"
D359_AUTO_MARK = "😈"


def d359_outreach_slug(task: dict) -> str | None:
    """The d359/<slug> label on `task`, but ONLY if it's a daemon-created
    outreach reminder (content starts with the same 😈 marker stale-contacts
    uses) — a hand-written task that happens to carry the label is never
    diverted. None when it isn't one of these."""
    content = task.get("content") or ""
    if not content.startswith(D359_AUTO_MARK):
        return None
    for lbl in task.get("labels") or []:
        if lbl.startswith("d359/"):
            return lbl[len("d359/"):]
    return None


def run_d359_met(item_name: str, slug: str) -> dict:
    """Route a completed outreach task through the same 'met' flow /s897
    uses: last_contact -> today, robot task DELETED (not completed — no
    points claimed for a task a daemon invented, matching /s897's own
    policy). slug -> name reconstruction is safe because both s897_update's
    _slug() and _bare() collapse hyphens/spaces/underscores to the same
    space-joined lowercase key before matching."""
    name_guess = slug.replace("-", " ")
    try:
        proc = subprocess.run(
            ["python3", str(S897_UPDATE), f"{name_guess} met"],
            capture_output=True, text=True, timeout=30)
        ok = proc.returncode == 0
        out = (proc.stdout or proc.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        ok, out = False, str(e)
    return {"name": item_name, "step": "d359_met",
            "d359": {"slug": slug, "ok": ok, "output": out}}


def _is_daily_recurrence(due_string: str) -> bool:
    """True for DAILY recurrences only, where the next occurrence == tomorrow.
    Weekly/monthly recurrences have their own next-occurrence math (next matching
    weekday / next month), so fast-forwarding them to 'tomorrow' would break the
    cadence — a plain /close (advances one interval) is the right catch-up there
    for a single miss."""
    s = (due_string or "").lower()
    return ("every day" in s or "daily" in s
            or bool(re.search(r"\bevery (morning|afternoon|evening|night)\b", s)))


def catch_up_recurring(task_id: str, due_string: str, target_iso: str) -> tuple[bool, str | None]:
    """Reschedule an OVERDUE recurring task forward to `target_iso`, preserving
    its recurrence, instead of a plain /close.

    /close advances a recurrence by ONE interval from the task's (stale) due
    date, so a daily habit that fell behind can never catch up — it stays
    overdue and lingers in the Todoist mobile Today view forever (2026-07-13:
    '2nd hci' stuck at 2026-06-29). Passing due_date + the bare recurrence
    due_string re-anchors the date to the next occurrence without dropping the
    repeat (same shape defer-fast uses for recurring parents)."""
    body = {"due_date": target_iso}
    if due_string:
        body["due_string"] = due_string
    try:
        req = urllib.request.Request(
            f"{TODOIST_BASE}/tasks/{task_id}",
            data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {TODOIST_TOKEN}",
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _on_ix() -> bool:
    """True when this process runs on Ix (the Mac Mini, hostname
    Jonathans-Mac-mini.local) — the single writer for build-order stamps."""
    import socket
    return "mac-mini" in socket.gethostname().lower()


def _stamp_on_ix(block: str, emoji: str) -> Optional[bool]:
    """Apply a block-header stamp on IX's build-order copy (single writer).
    Returns True = freshly stamped, False = already present, None = ssh
    failed (caller falls back to the local write)."""
    py = (
        "import sys; sys.path.insert(0, '/Users/mckay/i446-monorepo/lib')\n"
        "import neon_blocks as nb\n"
        "from pathlib import Path\n"
        "bo = Path.home() / 'vault/g245/5e-1/build-order.md'\n"
        "t = bo.read_text(encoding='utf-8')\n"
        f"nt, ch = nb.stamp_emoji(t, {block!r}, {emoji!r})\n"
        "if ch: bo.write_text(nt, encoding='utf-8')\n"
        "print('CH' if ch else 'NC')\n"
    )
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "ix", "python3", "-"],
            input=py, capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    outp = (r.stdout or "").strip().splitlines()
    tokenized = outp[-1] if outp else ""
    if r.returncode != 0 or tokenized not in ("CH", "NC"):
        return None
    return tokenized == "CH"


def run_ritual(tag: str) -> dict:
    """Complete one block ritual (-1neon card): close its open Todoist task,
    stamp the ritual emoji on the CURRENT 地支 block in build-order.md, and
    credit 0分!P immediately.

    All 5 rituals now earn their points on manual completion, including ⏱️/✅
    (auto rituals) — see the note in the body for the OR semantics with the
    daemon's automatic Toggl/Todoist validation.
    """
    import sys as _s
    _s.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
    import neon_blocks as nb

    cfg = nb.load_config()
    rituals = nb.ritual_by_tag()
    if tag not in rituals:
        return {"error": f"unknown ritual tag {tag!r}", "known": list(rituals)}
    r = rituals[tag]
    emoji = r["emoji"]
    label = cfg["label"]
    marker = cfg.get("auto_marker", "")
    out: dict = {"ritual": tag, "emoji": emoji, "points": r["points"]}

    # 1. Close the matching open -1neon Todoist task (content minus 😈 == tag).
    from urllib.parse import quote
    closed_id = None
    try:
        url = f"{TODOIST_BASE}/tasks?label={quote(label)}&limit=200"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        tasks = data.get("results", data) if isinstance(data, dict) else data
        match = None
        for t in tasks:
            bare = (t.get("content") or "").replace(marker, "").strip()
            if bare == tag or tag in bare.split():
                match = t
                break
        if match:
            tid = match["id"]
            _id, ok, err = close_todoist_task(tid)
            out["todoist"] = {"id": tid, "content": match.get("content", ""),
                              "closed": ok}
            if ok:
                closed_id = tid
            if err:
                out["todoist"]["error"] = err
        else:
            out["todoist"] = {"closed": False, "note": "no open -1neon task matched"}
    except Exception as e:  # noqa: BLE001
        out["todoist"] = {"closed": False, "error": str(e)}

    # Auto rituals (-1t/-1l): OR semantics (2026-07-13 redesign) — completing
    # the card is now an independent, equally-valid path to earning the marker,
    # alongside the daemon's automatic Toggl/Todoist validation at block close.
    is_auto = r.get("mode") == "auto"

    # 2. Stamp the emoji on the CURRENT block header (local build-order.md).
    #    ALL 5 rituals target the current block, matching the daemon's own
    #    convention: evaluate_and_mark_block's ⏱️/✅ CHECK looks at the
    #    PREVIOUS block's window (hour-4..hour-2 — you can't know a block's
    #    Toggl/task coverage is complete until it's over), but the resulting
    #    STAMP always lands on `block_name`, the block currently being scored
    #    — never on the block that was checked. (2026-07-13: an earlier cut
    #    of this fix stamped the previous block instead, which put the
    #    manual credit on the wrong header vs. what the user — and the
    #    daemon — actually see as "this block's" ⏱️/✅.)
    bo = Path.home() / "vault/g245/5e-1/build-order.md"
    block = nb.current_block(datetime.now().hour)
    out["block"] = block
    if not bo.exists():
        out["error"] = "build-order.md not found"
        return out
    # SINGLE-WRITER stamps (2026-07-27): completions run on both Straylight
    # (dtd/skills) and Ix (mobile dtd/daemon), and each used to stamp its OWN
    # build-order copy — Syncthing last-writer-wins then dropped whichever
    # side synced second (bug: all 5 午 rituals completed across the two
    # machines, header merged to ☀️📧✅, P showed 7/13). All stamps now land
    # on Ix's copy (where the daemon reconciles); Syncthing brings them back.
    # Ix unreachable → old local write as a loud, noted fallback.
    text = bo.read_text(encoding="utf-8")
    new_text, changed = nb.stamp_emoji(text, block, emoji)
    if _on_ix():
        if changed:
            bo.write_text(new_text, encoding="utf-8")
    else:
        remote = _stamp_on_ix(block, emoji)
        if remote is None:
            out["stamp_fallback_local"] = True
            if changed:
                bo.write_text(new_text, encoding="utf-8")
        else:
            # Ix's copy is the truth: credit points only if IX stamped fresh
            # (a local-stale "would change" must not double-credit a ritual
            # another machine already stamped and credited).
            changed = remote
    out["stamped"] = changed
    out["auto"] = is_auto

    # 3. Record the completion in completed-today so dtd hides the card at once.
    #    dtd hides a cached task when its id is in completed-today's id map; the
    #    ritual card lives in dtd's 'today' cache bucket, so without this record
    #    a ritual completed in /inbound lingers in dtd until a full cache refresh
    #    (regression 2026-06-29). Keyed by id → collision-proof.
    if closed_id:
        try:
            name = (out.get("todoist", {}).get("content") or f"{marker} {tag}").strip()
            mc.append_names([name], ids={name: closed_id})
            out["completed_today"] = True
        except Exception as e:  # noqa: BLE001 — never fail the ritual on a log write
            out["completed_today_error"] = str(e)

    # 4. Credit 0分!P IMMEDIATELY: one term per block, always (2026-07-13).
    #
    #    Prefer a full recompute from `new_text` (score_day: one term per
    #    currently-stamped block, in chronological order) — this is what
    #    keeps P readable, since positional append/merge can't tell "the
    #    previous block" apart from "the current block's own later term"
    #    (⏱️/✅ target the former, manual rituals credit the latter, and
    #    once both exist in the same formula neither position is reliably
    #    "the last term" for a given block). This is a best-effort immediate
    #    credit only — the daemon's own boundary reconcile is still the
    #    periodic validating checksum, independent of what did-fast does here.
    #
    #    Guard against the 2026-07-11 clobber: that bug was a recompute off a
    #    build-order.md copy that can lag Ix's over Syncthing, so it could
    #    UNDERCOUNT and SET P below what the daemon (self-contained on Ix) had
    #    already correctly written moments earlier. The guard is simple and
    #    sufficient — read the CURRENT live P total first; only SET the
    #    recomputed formula if its total is >= the live total. Our own
    #    just-written stamp on `new_text` means the common case recomputes
    #    to something >= live (an improvement: more merged, same or higher
    #    total). In the rare race (a daemon marker landed on Ix moments ago
    #    and hasn't synced to us yet), the recompute would be LOWER than
    #    live — the guard skips the SET and falls back to a plain `+N`
    #    append instead, so P can still grow but never drops.
    pts = int(r.get("points") or 0)
    if pts and changed:
        now = datetime.now()
        _, computed_total, computed_formula = nb.score_day(new_text)

        # Read + write via the excel-http daemon (audit ledger + chain check)
        # — never raw AppleScript against 0分 (2026-07-28 migration).
        try:
            read_res = neon_excel.read("0分", "P", date=f"{now.month}/{now.day}")
            if not read_res.get("ok") or not read_res.get("row"):
                raise RuntimeError(
                    str(read_res.get("error") or "ERR: no 0分 row")[:120])
            p_row = int(read_res["row"])
            f = str(read_res.get("formula") or "")
            v = read_res.get("value")
            live_total = float(v or 0)

            if computed_total >= live_total:
                new_formula = computed_formula
                regrouped = True
            else:
                # Fall back to a safe append — never decreases P.
                # No literal leading "0" term (see neon_blocks.score_day) —
                # each block gets exactly one term, so term-count == block-count.
                if f in ("", "0", "=0"):
                    terms = []
                elif f.startswith("="):
                    inner = f[1:]
                    terms = inner.split("+") if inner else []
                    if terms == ["0"]:
                        terms = []
                else:
                    terms = [f]
                terms.append(str(pts))
                new_formula = "=" + "+".join(terms)
                regrouped = False

            # SET the composed formula (recompute or safe append) on col P.
            write_res = neon_excel.write("0分", "P", row=p_row,
                                         value=new_formula,
                                         src=f"ritual {block} -1n")
            _warn_chain_broken(write_res)
            out["p_credit"] = {"points": pts, "ok": bool(write_res.get("ok")),
                               "regrouped": regrouped,
                               "excel": (f"P={write_res.get('value')}"
                                         if write_res.get("ok")
                                         else str(write_res.get("error") or ""))[:60]}
        except Exception as e:  # noqa: BLE001 — never fail the ritual on a P write
            out["p_credit_error"] = str(e)
    return out


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
        # Explicit refresh: block on the lock so a concurrent daemon/dtd refresh
        # can't make this silently no-op (goals must reach dtd immediately).
        data = refresh_task_queue(block=True)
        counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        print(json.dumps({"status": "ok", "counts": counts}, indent=2))
        return

    if sys.argv[1] == "--ritual":
        if len(sys.argv) < 3:
            print("usage: did-fast.py --ritual <tag>", file=sys.stderr)
            sys.exit(1)
        result = run_ritual(sys.argv[2])
        # Rebuild the task cache so the just-closed ritual drops from the 'today'
        # bucket AND the cache mtime advances — that mtime bump is the only thing
        # dtd's auto-reload watcher polls, so an open dtd updates live instead of
        # showing the completed ritual until a manual ctrl-r (regression 2026-06-29).
        try:
            refresh_task_queue(block=True)  # explicit: must not skip on lock
            result["cache_refreshed"] = True
        except Exception as e:  # noqa: BLE001 — never fail the ritual on a refresh
            result["cache_refresh_error"] = str(e)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # Regression (2026-07-19): p_credit_error (e.g. ix unreachable over ssh)
        # was recorded in the result dict but this handler always exited 0, and
        # every skill invoking --ritual redirects with `>/dev/null 2>&1` — so a
        # totally failed point-credit was silently reported to the user as a
        # successful ritual completion. Exit nonzero so a caller checking $?
        # (or a human re-running with output visible) can tell.
        if result.get("p_credit_error"):
            sys.exit(1)
        return

    argv = sys.argv[1:]
    # --points-only: log points to 0分/completed-today but skip all Todoist
    # side effects (match/close/posthoc/build-order). Used by dtd's split.
    points_only = "--points-only" in argv
    if points_only:
        argv = [a for a in argv if a != "--points-only"]
    # --task-id <id>: dtd passes the fzf row id so completion closes the EXACT
    # selected task, not a name match (duplicate names would close the wrong
    # instance). Only honoured for a single-item completion — a batch has no
    # single target. Habits/rituals still route by name; the id only wins when
    # it's present in the matched candidate list (see match_todoist_task).
    task_id_override = None
    if "--task-id" in argv:
        _i = argv.index("--task-id")
        task_id_override = argv[_i + 1] if _i + 1 < len(argv) else None
        del argv[_i:_i + 2]
    raw = " ".join(argv)

    # 1. Parse
    items = parse_input(raw)
    if not items:
        print(json.dumps({"error": "no items parsed"}))
        sys.exit(1)

    # 1b. Ritual cards: a daemon-created -1neon card (`😈 <tag>`) completed BY
    # NAME — dtd's enter/alt-enter worker pipes the card content here verbatim —
    # must go through run_ritual (header emoji stamp + instant -1₦ credit), not
    # the generic Todoist close below. The generic path closes the card without
    # the stamp, and the daemon's turnover reconcile scores manual rituals FROM
    # the header stamps, so the points were silently lost (2026-07-03: -1ibx
    # completed in dtd left 辰/巳 with no 📧, -1₦ 3 short per block). Skipped
    # under --points-only: the split flow promises no Todoist side effects and
    # run_ritual is all side effects.
    ritual_entries: list[dict] = []
    if not points_only:
        # Tag resolution is pure; only IT gets the fallback-to-generic except.
        # A failure AFTER a run_ritual must not re-feed that item to the
        # generic path (it would double-close and double-credit).
        try:
            sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
            import neon_blocks as nb
            _r_cfg = nb.load_config()
            resolved = [(it, nb.ritual_card_tag(it.name, _r_cfg)) for it in items]
        except Exception as e:  # noqa: BLE001 — no side effects yet
            print(f"ritual-card resolution failed: {e}", file=sys.stderr)
            resolved = [(it, None) for it in items]
        items = [it for it, _tag in resolved if _tag is None]
        for it, _tag in resolved:
            if _tag is None:
                continue
            try:
                res = run_ritual(_tag)
            except Exception as e:  # noqa: BLE001 — surface, never reroute
                res = {"error": str(e)}
            td = res.get("todoist") or {}
            # Deliberately NO Todoist id here: undo-fast reopens any results
            # entry carrying todoist.id, but nothing un-stamps the header — a
            # half-undo that leaves points scored on an open card. Without the
            # id, undo skips it (the --ritual CLI path never journals at all).
            ritual_entries.append({
                "name": it.name, "step": "ritual",
                "todoist": {"closed": bool(td.get("closed")),
                            "content": td.get("content", it.name)},
                "ritual": res,
            })
        if ritual_entries:
            # The cache mtime bump is dtd's only reload signal (2026-06-29), so
            # refresh even in a mixed batch, not just the all-ritual early return.
            try:
                refresh_task_queue(block=True)
            except Exception as e:  # noqa: BLE001
                print(f"cache refresh failed: {e}", file=sys.stderr)
        if not items:
            print(json.dumps({"results": ritual_entries, "agent_needed": []},
                             ensure_ascii=False, indent=2))
            return

    # 2. Load caches
    headers = load_headers()
    tq = load_task_queue()

    # 3. Route
    routes = route_items(items, headers, tq, skip_todoist=points_only,
                         preferred_id=(task_id_override if len(items) == 1 else None))

    # Separate fast-path from agent-required
    fast = [r for r in routes if r.step in ("0n", "todoist", "1n", "variable")]
    agent_needed = [r for r in routes if r.step == "needs_agent"]

    # 3a-ii. d359 outreach tasks (😈-labelled `d359/<slug>`): completing one
    # via /did (and hence dtd) means contact happened — divert to the same
    # 'met' flow /s897 uses (last_contact -> today, robot task DELETED) BEFORE
    # the generic close/point-credit paths below ever see it. Points are
    # deliberately never credited here, matching /s897's own policy ("no
    # points are claimed for a task a daemon invented"). Gated on points_only
    # for the same reason ritual cards are (see 1b. above): --points-only
    # promises no Todoist side effects, and this is all side effects.
    d359_met_entries: list[dict] = []
    if not points_only:
        remaining = []
        for r in fast:
            slug = d359_outreach_slug(r.todoist_task) if r.todoist_task else None
            if slug:
                d359_met_entries.append(run_d359_met(r.item.name, slug))
            else:
                remaining.append(r)
        fast = remaining
        if d359_met_entries:
            try:
                refresh_task_queue(block=True)
            except Exception as e:  # noqa: BLE001
                print(f"cache refresh failed: {e}", file=sys.stderr)

    # 3b. Stop matching Toggl timer; backfill variable-task values from
    # the timer's elapsed minutes (dtd: no need to type the time when a
    # matching timer is running)
    all_names = [r.item.name for r in fast]
    toggl_stop = stop_matching_toggl(all_names) if all_names else None
    apply_timer_minutes(fast, toggl_stop)

    # 4. Batch 0₦ writes
    on_writes = [r for r in fast if r.step == "0n" and r.col_num]
    target_date = items[0].target_date or f"{date.today().month}/{date.today().day}"

    on_result = None
    if on_writes:
        script = build_0n_script(on_writes, target_date)
        if script:
            on_result = ix_run(script, timeout=30.0)

    # 4a-ii. 0l special case: write completion time to "N Color" column (AF)
    if any(r.item.name.lower() == "0l" for r in on_writes):
        ol_time_result = ix_run(build_0l_time_script(target_date), timeout=15.0)
        if ol_time_result.returncode == 0:
            print(f"0l completion: {ol_time_result.stdout.strip()}", file=sys.stderr)

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

    # 4c. Batch 1n+ → 0分 appends (via the excel-http daemon, one round-trip).
    # The week cell holds MINUTES (2026-07-27 redesign), so 0分 must NOT
    # reference it: standard habits reference the column's ROW-5 expected
    # points (a constant), variable habits and [N] overrides append literal
    # points via step 5's fen_appends.
    one_n_fen_result = None
    if one_n_writes and week_row:
        ref_appends = []
        ref_names = []
        for r in one_n_writes:
            if not r.fen_col:
                continue
            if r.is_variable_1n and r.variable_value:
                r.fen_points = r.variable_value
            elif r.item.points_override:
                r.fen_points = r.item.points_override
            elif r.col_letter:
                ref_appends.append((r.fen_col, f"+'1n+'!{r.col_letter}5"))
                ref_names.append(r.item.name)
        if ref_appends:
            one_n_fen_result = append_0fen_batch(ref_appends, target_date,
                                                 ref_names, "1n_0fen")

    # 5. Batch 0分 appends via the excel-http daemon (for 0n, todoist, and
    # variable 1n+ items with direct points)
    fen_appends = []
    fen_names = []
    for r in fast:
        if r.fen_col and r.fen_points > 0 and not (r.step == "1n" and not r.is_variable_1n):
            fen_appends.append((r.fen_col, r.fen_points))
            fen_names.append(r.item.name)
        # {N} curly points → 0分 column Q (0g bonus)
        if r.item.curly_points and r.item.curly_points > 0:
            fen_appends.append(("Q", r.item.curly_points))
            fen_names.append(r.item.name)

    fen_result = None
    if fen_appends:
        fen_result = append_0fen_batch(fen_appends, target_date, fen_names, "0fen")

    # 5b. hcbi writes (habits that log minutes to the hcbi sheet)
    hcbi_appends = []
    for r in fast:
        hcbi_col = HCBI_HABITS.get(r.item.name.lower())
        if hcbi_col:
            mins = r.item.time_value or r.write_value or 0
            if mins > 0:
                hcbi_appends.append((hcbi_col, mins))
    hcbi_result = None
    if hcbi_appends:
        script = build_hcbi_script(hcbi_appends, target_date)
        if script:
            hcbi_result = ix_run(script, timeout=30.0)

    # 5c. Prayer marker: write ☀️ to build order for current block.
    # ☀️ is the صلاة prayer marker: -2n/inbound, wakeup, and the 1-1n heatmap
    # all read it as "prayer logged for this block" and suppress the salah card
    # when present. Only the actual prayer habit (ص) may stamp it. Mindfulness
    # habits (冥想/o314/其他人) used to stamp ☀️, which falsely suppressed the
    # inbound prayer card for any block where you meditated but hadn't prayed.
    PRAYER_HABITS = {"ص"}
    prayer_done = any(r.item.name.lower() in PRAYER_HABITS for r in fast if r.step == "0n")
    if prayer_done:
        try:
            _bo = Path.home() / "vault/g245/5e-1/build-order.md"
            if _bo.exists():
                _now_h = datetime.now().hour
                _bidx = max(0, min(8, (_now_h - 4) // 2))
                _branches = ["卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
                _bname = _branches[_bidx]
                _bo_text = _bo.read_text()
                if "## -1₲" in _bo_text:
                    _lines = _bo_text.split("\n")
                    _new = []
                    for _l in _lines:
                        if (_l.startswith(f"- {_bname}") and "☀️" not in _l):
                            _new.append(f"{_l.rstrip()} ☀️")
                        else:
                            _new.append(_l)
                    _bo.write_text("\n".join(_new))
        except Exception:
            pass  # non-critical

    # 5d. (removed) The -1l task marker (✅) is daemon-owned. It used to be
    # stamped here on the *current* in-progress block on every task completion,
    # but -1t/-1l are retrospective rituals: they belong to the most-recently-
    # completed block and are evaluated at its closing fire by the build-order
    # daemon's evaluate_and_mark_block (which validates that the block's in-window
    # completions were all pointed). Eager-stamping the live block both mis-
    # attributed the marker (current block, not the completed one) and over-
    # stamped it (any completion, pointed or not), so it was removed.

    # 5e. Flip build order checkboxes for completed tasks
    # Matches closed Todoist tasks and build_order items against -1₲ goals
    try:
        _bo = Path.home() / "vault/g245/5e-1/build-order.md"
        if _bo.exists():
            _bo_text = _bo.read_text()
            if "## -1₲" in _bo_text:
                _bo_lines = _bo_text.split("\n")
                _changed = False
                for r in fast:
                    if r.step in ("todoist", "build_order"):
                        content = ""
                        if r.todoist_task:
                            content = r.todoist_task.get("content", "")
                        elif r.step == "build_order":
                            content = r.item.name
                        if not content:
                            continue
                        bare = re.sub(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]", "", content).strip().lower()
                        for bi, bl in enumerate(_bo_lines):
                            if re.match(r"^ {2,4}- \[ \] .+", bl):
                                goal = bl.strip()[6:]
                                bare_goal = re.sub(r"\s*[\[\(\{][^\]\)\}]*[\]\)\}]", "", goal).strip().lower()
                                if bare_goal and (bare_goal == bare or bare_goal in bare or bare in bare_goal):
                                    _bo_lines[bi] = bl.replace("- [ ]", "- [x]", 1)
                                    _changed = True
                                    break
                if _changed:
                    _bo.write_text("\n".join(_bo_lines))
    except Exception:
        pass  # non-critical

    # 6. Close or defer Todoist tasks in parallel
    task_ids = []
    defer_items = {}  # tid → (defer_date, points_claimed, content)
    id_to_name = {}
    future_skipped = []  # tasks skipped because due date is in the future
    catch_up = []  # (tid, due_string) for OVERDUE recurring habits to fast-forward
    # 0neon recurring tasks that may be completed in advance
    ADVANCE_ALLOWED = {"新闻", "stats i9", "m5x2 stats", "push", "hiit"}
    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    # Idempotency source of truth: a recurring habit already in today's
    # completed-today.json must NOT be closed again. Each close advances an
    # "every day" recurrence by a day, so a same-day double-tap drifts the due
    # date past today and the habit silently vanishes from dtd (regression
    # 2026-06-27: 0t drifted to due-tomorrow). completed-today is authoritative
    # regardless of cache/Todoist due-date lag, which the due-date guard below
    # can miss. Date-gated so a stale file can't suppress a fresh day.
    _ct = mc._load(mc.COMPLETED)
    done_today = ({mc._normalize(n) for n in _ct.get("names", [])}
                  if _ct.get("date") == today_str else set())
    for r in fast:
        if r.todoist_task:
            tid = r.todoist_task["id"]
            id_to_name[tid] = r.item.name
            # Already completed today + recurring → skip the re-close so the
            # recurrence doesn't advance a second time.
            if (r.todoist_task.get("recurring")
                    and mc._normalize(r.item.name) in done_today):
                future_skipped.append({
                    "id": tid,
                    "name": r.item.name,
                    "content": r.todoist_task.get("content", ""),
                    "due": r.todoist_task.get("due", ""),
                    "warning": "already done today (skipped re-close to avoid recurrence drift)",
                })
                continue
            # Guard: don't close tasks due in the future (prevents double-tap on recurring)
            task_due = r.todoist_task.get("due", "")
            if task_due and task_due > today_str:
                # Allow advance-completion for specific 0neon tasks, but ONLY one
                # day ahead (due == tomorrow). Without the ceiling, an
                # advance-allowed daily habit (新闻/push/hiit/...) advances one
                # more day on every re-complete and drifts arbitrarily far into
                # the future, dropping off dtd's today list entirely (2026-07-14:
                # hiit reached due+2 and vanished). completed-today can't backstop
                # it because advance-completion is exactly the "not yet done for
                # this occurrence" case.
                is_0neon = "0neon" in r.todoist_task.get("labels", [])
                name_lower = r.item.name.lower()
                advance_ok = (is_0neon and name_lower in ADVANCE_ALLOWED
                              and task_due <= tomorrow_str)
                if not advance_ok:
                    future_skipped.append({
                        "id": tid,
                        "name": r.item.name,
                        "content": r.todoist_task.get("content", ""),
                        "due": task_due,
                        "warning": "already done today",
                    })
                    continue
            if r.item.defer_date:
                pts = r.item.points_override or r.fen_points or 0
                defer_items[tid] = (r.item.defer_date, pts, r.todoist_task["content"])
            elif (r.todoist_task.get("recurring") and task_due and task_due < today_str
                  and _is_daily_recurrence(r.todoist_task.get("due_string", ""))):
                # OVERDUE *daily* habit: a plain /close advances only +1 interval
                # from the stale due date, so a multi-day-behind daily task can
                # never catch up and lingers overdue on mobile forever
                # (2026-07-13: '2nd hci' stuck at 2026-06-29). Fast-forward it to
                # tomorrow instead. Scoped to daily because tomorrow is only the
                # correct next occurrence for a daily recurrence; weekly/monthly
                # tasks self-heal via a normal /close (advances one interval).
                catch_up.append((tid, r.todoist_task.get("due_string", "")))
            else:
                task_ids.append(tid)

    close_results = close_todoist_tasks(task_ids)

    # Fast-forward OVERDUE recurring habits to their next occurrence (see
    # catch_up_recurring) so they stop lingering overdue in Todoist mobile.
    catch_up_results = {}
    for _tid, _due_string in catch_up:
        catch_up_results[_tid] = catch_up_recurring(_tid, _due_string, tomorrow_str)

    # Defer tasks (reschedule + deduct points)
    defer_results = {}
    for tid, (dd, pts, content) in defer_items.items():
        ok, err = defer_todoist_task(tid, dd, pts, content)
        defer_results[tid] = (ok, err)

    # 6b. Create Toggl entries for items with time_range (parallel)
    toggl_created = {}
    def _resolve_toggl_project(r):
        """Resolve Toggl project code: explicit override > habit map > Todoist labels."""
        if r.item.project_override:
            return r.item.project_override
        habit = HABIT_PROJECT.get(r.item.name.lower())
        if habit:
            return habit
        # Fall back to Todoist task labels
        if r.todoist_task:
            for lbl in r.todoist_task.get("labels", []):
                if lbl in LABEL_TO_0FEN:
                    return lbl
        return None
    toggl_items = [(r.item.name, r.item.time_range,
                    _resolve_toggl_project(r),
                    r.item.target_date,
                    r.item.toggl_tags or [])
                   for r in fast if r.item.time_range]
    if toggl_items:
        def _create_toggl(args):
            name, tr, proj, td, tags = args
            today_str = date.today().isoformat()
            # If target_date differs from today, compute ISO date
            if td:
                parts = td.split("/")
                if len(parts) == 2:
                    today_str = f"{date.today().year}-{int(parts[0]):02d}-{int(parts[1]):02d}"
            ref_date = date.fromisoformat(today_str)
            def _parse_hhmm(t):
                h, m = int(t[:2]), int(t[2:4])
                return datetime(ref_date.year, ref_date.month, ref_date.day, h, m, tzinfo=TZ)
            start_dt, end_dt = _parse_hhmm(tr[0]), _parse_hhmm(tr[1])
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            try:
                trim_lines = _trim_toggl_range(start_dt, end_dt)
            except Exception as e:  # noqa: BLE001 — never block entry creation on a trim failure
                trim_lines = [f"trim failed: {e}"]
            cmd = ["python3", str(TOGGL_CLI), "create", name,
                   f"{tr[0][:2]}:{tr[0][2:]}", f"{tr[1][:2]}:{tr[1][2:]}"]
            if proj:
                cmd.append(proj)
            for tag in tags:
                cmd.extend(["--tag", tag])
            cmd.extend(["--date", today_str])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                out = proc.stdout.strip()
                if trim_lines:
                    out = "\n".join(trim_lines) + ("\n" + out if out else "")
                return name, proc.returncode == 0, out
            except Exception as e:
                return name, False, str(e)

        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, ok, out in pool.map(_create_toggl, toggl_items):
                toggl_created[name] = {"ok": ok, "output": out}

    # 6c. Create posthoc Todoist tasks for variable items (parallel)
    # Skipped under --points-only: the caller (split) makes its own posthoc.
    # Also skipped for a "variable" item that already carries a real
    # todoist_task (the past-date-with-preferred_id fast path above): that
    # item references an EXISTING task being closed directly, not a fresh
    # activity that needs a brand-new posthoc record fabricated for it.
    posthoc_results = {}
    variable_items = [] if points_only else [
        r for r in fast if r.step == "variable" and r.todoist_task is None]
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
    # Record the Todoist id of every task we actually closed, so dtd hides by id
    # (collision-proof) and a completed task can't suppress a different open task
    # sharing its annotation-stripped name.
    completed_ids = {id_to_name[tid]: tid
                     for tid, (ok, _err) in close_results.items()
                     if ok and tid in id_to_name}
    if completed_names:
        mc.append_names(completed_names, points=completed_points,
                        ids=completed_ids or None)

    # 8. Build output (ritual-card entries from step 1b lead the list)
    output = {"results": list(ritual_entries) + d359_met_entries, "agent_needed": []}

    # Pre-image maps for undo (captured by the write scripts themselves)
    pre_0n = parse_pre_lines(on_result.stdout) if on_result and on_result.returncode == 0 else {}
    pre_1n = parse_pre_lines(one_n_result.stdout) if one_n_result and one_n_result.returncode == 0 else {}

    for r in fast:
        entry = {
            "name": r.item.name,
            "step": r.step,
            "value": r.write_value if r.step == "0n" else None,
            "col": r.col_num,
        }
        # Only attach pre-images when the write script actually captured them
        # (a failed/ERROR script emits no PRE lines — undo must not guess).
        if r.step == "0n" and r.col_num is not None and str(r.col_num) in pre_0n:
            entry["undo"] = {"prev_0n": pre_0n[str(r.col_num)]}
        if r.step == "1n" and r.col_letter:
            entry["col_letter"] = r.col_letter
            entry["week_row"] = week_row
            entry["fen_col"] = r.fen_col
            if r.col_letter in pre_1n:
                entry["undo"] = {"prev_1n_formula": pre_1n[r.col_letter]}
        if r.item.curly_points and r.item.curly_points > 0:
            entry["curly_q"] = r.item.curly_points
        hcbi_col = HCBI_HABITS.get(r.item.name.lower())
        if hcbi_col:
            mins = r.item.time_value or r.write_value or 0
            if mins > 0:
                entry["hcbi"] = {"col": hcbi_col, "mins": mins}
        if r.is_variable_1n:
            entry["variable_1n"] = True
            entry["variable_value"] = r.variable_value
        if r.todoist_task:
            tid = r.todoist_task["id"]
            if tid in defer_results:
                ok, err = defer_results[tid]
                td_entry = {
                    "id": tid,
                    "content": r.todoist_task["content"],
                    "closed": False,
                    "deferred": r.item.defer_date,
                    "deferred_ok": ok,
                }
                if err:
                    td_entry["error"] = err
            else:
                ok, err = close_results.get(tid, (False, "no_attempt"))
                td_entry = {
                    "id": tid,
                    "content": r.todoist_task["content"],
                    "closed": ok,
                }
                if not ok and err:
                    td_entry["error"] = err
            # Pre-close state for undo: recurring closes advance the due date
            # instead of archiving, so undo needs the original due to restore.
            td_entry["prev_due"] = r.todoist_task.get("due", "")
            td_entry["due_string"] = r.todoist_task.get("due_string", "")
            td_entry["recurring"] = r.todoist_task.get("recurring", False)
            entry["todoist"] = td_entry
        if r.step == "variable" and r.item.name in posthoc_results:
            entry["posthoc"] = posthoc_results[r.item.name]
        if r.fen_col:
            fen_entry = {"col": r.fen_col, "points": r.fen_points}
            if r.item.bonus_points:
                fen_entry["bonus"] = r.item.bonus_points
            entry["0fen"] = fen_entry
        if r.item.time_range and r.item.name in toggl_created:
            entry["toggl"] = toggl_created[r.item.name]
        output["results"].append(entry)

    for r in agent_needed:
        output["agent_needed"].append({
            "name": r.item.name,
            "raw": r.item.raw,
            "reason": r.error,
        })

    if future_skipped:
        output["future_skipped"] = future_skipped
        # Also emit to stderr so dtd/callers see the warning
        for fs in future_skipped:
            warn = fs.get("warning", "future")
            print(f"⚠ SKIPPED: \"{fs['name']}\" — {warn} (due {fs['due']}, not closing Todoist)",
                  file=sys.stderr)

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
    if hcbi_result:
        output["hcbi_write"] = {
            "ok": hcbi_result.returncode == 0,
            "output": hcbi_result.stdout.strip(),
        }
        if hcbi_result.returncode != 0:
            output["hcbi_write"]["error"] = hcbi_result.stderr.strip() or f"ix-osa exit {hcbi_result.returncode}"

    # Fire-and-forget: invalidate personal-dashboard's 300s API cache so the
    # neon-fed cache cards (其他人, ص, o314, 冥想, hcbp, hcbc, xk88) reflect
    # this write on the next render instead of up to 5 minutes later.
    wrote_neon = any(
        r is not None and r.returncode == 0
        for r in (on_result, fen_result, one_n_result, one_n_fen_result, hcbi_result)
    )
    if wrote_neon:
        try:
            subprocess.Popen(
                ["curl", "-fsS", "-X", "POST", "--max-time", "2",
                 "http://ix:5558/api/refresh"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
