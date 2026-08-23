#!/usr/bin/env python3
"""
build-order-daemon.py — Daily archive + live d357 linking for the build order.

Modes:
  link-meetings  Inject d357 meeting wikilinks into today's build order under
                 matching 地支 time blocks. Idempotent; safe to run often.

  lock-and-mark  At each 2-hour boundary 04–22, set the new block's marker
                 cell to 1 (auto-bumps Y by 12 via the COUNT formula) and
                 lock the just-ended block's points cell from rolling formula
                 to literal value. Idempotent. Schedule: com.jm.neon-lock-and-mark.

  archive        Snapshot yesterday's build order to a dated archive file,
                 defer up to 5 unchecked -1₲ items to 以后的目标, email a
                 rating using yesterday's locked Y value. Runs at 03:59 —
                 the existing com.jm.1g-daily-reset plist wipes -1₲ at 04:00.

Usage:
  python3 build-order-daemon.py link-meetings  [--dry-run]
  python3 build-order-daemon.py lock-and-mark  [--dry-run] [--hour HH]
  python3 build-order-daemon.py archive        [--dry-run]
"""

from __future__ import annotations  # PEP 604 `dict | None` hints on Python 3.9

import argparse
import base64
import datetime as dt
import json
import os
import re
import smtplib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zoneinfo
from email.mime.text import MIMEText
from pathlib import Path

# excel-http client — journaled Neon writes. Running on ix it curls localhost
# directly, and it carries its own ssh+osascript fallback if the daemon is down.
sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
from neon import excel as neon_excel
import neon_blocks as nb  # build_order_lock() — centralized 2026-08-02 after
# three separate incidents of the same unlocked build-order.md race
import daytime  # noqa: E402  shared "now"/"today" resolution — see lib/daytime.py

# --- Paths ---

VAULT = Path.home() / "vault"
BUILD_ORDER = VAULT / "g245" / "5e-1" / "build-order.md"
D357_DIR = VAULT / "d357"  # files live in week subfolders (D357_DIR/<M.W>/YYYY.MM.DD-<kebab>.md) — glob recursively
ARCHIVE_ROOT = VAULT / "g245" / "archive"
RESET_SCRIPT = Path.home() / "i446-monorepo" / "scripts" / "-1g-cron.py"
DID_FAST = Path.home() / "i446-monorepo" / "tools" / "did" / "did-fast.py"

# --- Constants ---

MAX_DEFERRED = 5
LATER_HEADING = "以后的目标"  # 以后的目标
NEG1_MARKER = "-1₲"  # -1₲

# Neon / email
NEON_XLSX = Path.home() / "OneDrive" / "vault-excel" / "Neon分v12.2.xlsx"
NEON_SHEET = "0分"
NEON_DATE_COL = "B"
NEON_NEG1_COL = "P"  # -1₦ column (was Y, shifted after column consolidation)
EMAIL_FROM = "mckay@m5c7.com"
EMAIL_TO = "mckay@m5x2.com"
SMTP_KEYCHAIN_SERVICE = "gmail-smtp-m5c7"

# Fire schedule: every 2 hours from 04 to 22. Scores block based on emojis.
BLOCK_FIRE_HOURS = {4, 6, 8, 10, 12, 14, 16, 18, 20, 22}

# -1₦ sub-habit scoring: emoji → points. Total max = 13.
# ☀️ and 📧 are written by /inbound; 🎯, ⏱️, ✅ are written by the daemon.
SCORE_EMOJI_MAP = {
    "☀️": 1,   # الشمس (prayer)
    "🎯": 3,   # -1g (goals set)
    "⏱️": 3,   # -1t (toggl coverage)
    "✅": 3,   # -1l (todoist completions)
    "📧": 3,   # ibx (inbox processed)
}
GOAL_MARKER = "🎯"
TOGGL_MARKER = "⏱️"
TODOIST_MARKER = "✅"
# 🔒 on a block header = user-attested full credit: every stamp on that line is
# trusted verbatim (including 🎯) and never stripped. Set manually when the
# user vouches for a block the validators can't confirm (2026-07-30: 辰 rituals
# done but no goal text on file, so 🎯 kept getting stripped by the reconcile).
LOCK_MARKER = "🔒"
TODOIST_KEYCHAIN_SERVICE = "todoist-api-key"
# -1t: minutes of the 120-min block that must be *categorized* (a Toggl entry
# with a project assigned). Stricter than the old 0.8 coverage of any tracked
# time — bare/uncategorized time no longer counts.
TOGGL_MIN_MINUTES = 115
# -1neon block-ritual config (tag/emoji/points/mode) — single source of truth
# shared with tools/did/did-fast.py via lib/neon_blocks.py.
BLOCK_RITUALS_CONFIG = Path.home() / "i446-monorepo" / "config" / "block-rituals.json"
# Labels that mark a completed item as NOT a real one-off task, for -1l. Recurring
# habits (0neon/1neon/夜neon) don't even appear in the completed API (completion
# advances the due date), so the live noise to strip is the -1neon rituals
# themselves. @posthoc is NOT blanket-excluded (2026-07-30): a posthoc meeting
# completion that carries [N] is a real, pointed piece of work and should count
# toward -1l — only posthoc *defer-tracking* records ("deferred: X → Y", logged
# by the reschedule flow, not by doing anything) are noise. See _is_defer_noise.
NON_TASK_LABELS = {"0neon", "1neon", "夜neon", "-1neon"}

# Map fire-hour → 地支 block (just-ended) in the build order. Used to drop a
# "fired" emoji on the block header so the user can see at a glance which
# blocks the daemon hit.
HOUR_TO_BRANCH_BLOCK = {
    6:  "卯",  # 卯 (04–06) just ended
    8:  "辰",  # 辰 (06–08)
    10: "巳",  # 巳 (08–10)
    12: "午",  # 午 (10–12)
    14: "未",  # 未 (12–14)
    16: "申",  # 申 (14–16)
    18: "酉",  # 酉 (16–18)
    20: "戌",  # 戌 (18–20)
    22: "亥",  # 亥 (20–22)
}
DAEMON_FIRED_EMOJI = "😈"  # demon/daemon pun — matches the -1neon card auto_marker

# At each fire hour, freeze the just-ended block's column in 0分 from formula
# (`=D-SUM(prior blocks)`) to literal value, so the next block's residual
# formula starts measuring fresh. 04 has no prior block to lock today.
LOCK_AT_FIRE_HOUR = {
    6:  "G",  # 卯 (04–06) ended at 06
    8:  "H",  # 辰 (06–08) ended at 08
    10: "I",  # 巳 (08–10) ended at 10
    12: "J",  # 午 (10–12) ended at 12
    14: "K",  # 未 (12–14) ended at 14
    16: "L",  # 申 (14–16) ended at 16
    18: "M",  # 酉 (16–18) ended at 18
    20: "N",  # 戌 (18–20) ended at 20
    22: "O",  # 亥 (20–22) ended at 22
}

# 地支 block → hour range (inclusive). Hours outside 04-21 clamp to nearest.
BRANCH_HOURS = [
    ("卯", 4, 5),   # 04–06
    ("辰", 6, 7),   # 06–08
    ("巳", 8, 9),   # 08–10
    ("午", 10, 11), # 10–12
    ("未", 12, 13), # 12–14
    ("申", 14, 15), # 14–16
    ("酉", 16, 17), # 16–18
    ("戌", 18, 19), # 18–20
    ("亥", 20, 21), # 20–22
]
BRANCH_NAMES = {b[0] for b in BRANCH_HOURS}

# d357 slug (the part after YYYY-MM-DD-) to skip — not real meetings.
SKIP_SLUGS = {"睡觉"}  # 睡觉 = sleep entries

LOG_PREFIX = "build-order-daemon"


def log(msg: str) -> None:
    ts = dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] [{LOG_PREFIX}] {msg}", flush=True)


# --- 地支 block lookup ---

def hour_to_branch(hour: int) -> str:
    if hour < 4:
        return BRANCH_HOURS[0][0]  # 卯
    if hour > 21:
        return BRANCH_HOURS[-1][0]  # 亥
    for name, lo, hi in BRANCH_HOURS:
        if lo <= hour <= hi:
            return name
    return BRANCH_HOURS[-1][0]


# --- d357 parsing ---

DATE_LINE_RE = re.compile(
    r"\*\*Date:\*\*\s+\w+\s+\w+\s+\d+,\s+\d{4}\s+(\d{1,2}):(\d{2})\s*(AM|PM)?",
    re.IGNORECASE,
)


def find_meetings_for_date(target: dt.date):
    """Scan vault/d357 for files matching target's date.
    Filename format: YYYY.MM.DD-<slug>.md
    Time extraction: first try `**Date:** <Day> <Mon> <D>, <YYYY> HH:MM (AM|PM)?` in the body
    (legacy d357 format); else fall back to file mtime.
    Returns list of (hour:int, wikilink:str) sorted chronologically."""
    if not D357_DIR.exists():
        return []
    prefix = target.strftime("%Y.%m.%d")
    results = []
    for path in sorted(D357_DIR.glob(f"**/{prefix}-*.md")):
        slug = path.stem[len(prefix) + 1:]  # +1 for the dash separator
        if slug in SKIP_SLUGS:
            continue
        hour, minute = _extract_meeting_time(path)
        results.append((hour, minute, path.stem))
    results.sort(key=lambda x: (x[0], x[1]))
    return results  # list of (hour, minute, stem)


FRONTMATTER_TIME_RE = re.compile(r'^time:\s*"?(\d{1,2}):(\d{2})"?\s*$', re.MULTILINE)


def _extract_meeting_time(path: Path) -> tuple[int, int]:
    """Return (hour, minute) for a meeting file.
    Priority: frontmatter `time:` (new format) > `**Date:**` line (legacy) > file mtime."""
    try:
        content = path.read_text(encoding="utf-8")
        # Frontmatter time: only look at the top of the file (first 30 lines)
        head = "\n".join(content.split("\n", 30)[:30])
        fm = FRONTMATTER_TIME_RE.search(head)
        if fm:
            return int(fm.group(1)), int(fm.group(2))
        m = DATE_LINE_RE.search(content)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2))
            ampm = (m.group(3) or "").upper()
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0
            return hour, minute
    except OSError:
        pass
    mtime = dt.datetime.fromtimestamp(path.stat().st_mtime)
    return mtime.hour, mtime.minute


# --- Build order parsing ---

def load_lines():
    return BUILD_ORDER.read_text(encoding="utf-8").split("\n")


def save_lines(lines, dry_run=False):
    if dry_run:
        return
    content = "\n".join(lines)
    tmp = BUILD_ORDER.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(BUILD_ORDER)


def find_neg1_section(lines):
    """Return (start, end) line indices of `## -1₲` section; end is exclusive."""
    start = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("## ") and NEG1_MARKER in s:
            start = i
            break
    if start < 0:
        return -1, -1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break
    return start, end


def find_branch_headers(lines, start, end):
    """Return list of (branch_name, line_idx) in file order within [start, end).
    Tolerates trailing emoji/text after the branch char (e.g. `- 卯 😈`)."""
    headers = []
    for i in range(start, end):
        line = lines[i]
        if line.startswith("- "):
            tail = line[2:].strip()
            first_token = tail.split()[0] if tail else ""
            if first_token in BRANCH_NAMES:
                headers.append((first_token, i))
    return headers


# --- Mode: link-meetings ---

TIME_ENTRY_RE = re.compile(r'^(\s*-\s*)(\d{1,2}):(\d{2})\s*-\s*(\d{1,2})?:?(\d{2})?\s*(.*)$')
MEETING_START_TOLERANCE_MIN = 7  # ±N min: match meeting recording-start to Toggl entry start


def _meeting_link(stem: str) -> str:
    return f"([[d357/{stem}|d357]])"


def _line_has_d357(line: str, stem: str = "") -> bool:
    """True if line already references a d357 link (any stem, or specific stem)."""
    if stem:
        return stem in line
    return "[[d357/" in line or "(d357)" in line


def _try_inline_append(lines, start, end, stem, m_h, m_min):
    """Try to append `(d357 link)` to a time-entry line whose START matches
    the meeting's recording-start within ±MEETING_START_TOLERANCE_MIN.
    Closest start wins. Returns modified line index, or None if no match."""
    target = m_h * 60 + m_min
    best_idx = None
    best_diff = MEETING_START_TOLERANCE_MIN + 1
    for i in range(start, end):
        m = TIME_ENTRY_RE.match(lines[i])
        if not m:
            continue
        te_start = int(m.group(2)) * 60 + int(m.group(3))
        diff = abs(te_start - target)
        if diff < best_diff:
            best_diff = diff
            best_idx = i
    if best_idx is None:
        return None
    if _line_has_d357(lines[best_idx], stem):
        return best_idx
    lines[best_idx] = lines[best_idx].rstrip() + " " + _meeting_link(stem)
    return best_idx


def _slug_tokens(stem: str):
    """Meaningful tokens from a d357 file stem.
    Strips the YYYY.MM.DD- prefix and any short / numeric segments."""
    name = re.sub(r'^\d{4}\.\d{2}\.\d{2}-', '', stem)
    return [t.lower() for t in name.split('-') if len(t) >= 3 and not t.isdigit()]


def _try_name_fallback(lines, start, end, stem):
    """Match by slug-token substring in the time-entry description.
    Used after the time-window match fails. Picks the time entry with the
    most slug-token hits in its description; ties go to earliest line."""
    tokens = _slug_tokens(stem)
    if not tokens:
        return None
    best_idx = None
    best_score = 0
    for i in range(start, end):
        m = TIME_ENTRY_RE.match(lines[i])
        if not m:
            continue
        desc = (m.group(6) or "").lower()
        score = sum(1 for t in tokens if t in desc)
        if score > best_score:
            best_score = score
            best_idx = i
    if best_score == 0 or best_idx is None:
        return None
    if _line_has_d357(lines[best_idx], stem):
        return best_idx
    lines[best_idx] = lines[best_idx].rstrip() + " " + _meeting_link(stem)
    return best_idx


def run_link_meetings(dry_run=False, target_date=None):
    """Inject d357 wikilinks into the build order's -1₲ section.
    Inline-appends to time entries whose start matches a meeting's recording
    start within ±MEETING_START_TOLERANCE_MIN; otherwise floats as a bullet
    under the meeting's hour-mapped 地支 block.
    target_date defaults to today (cron use). archive uses archive_date."""
    target = target_date or dt.date.today()
    meetings = find_meetings_for_date(target)
    if not meetings:
        log(f"link-meetings: no d357 meetings for {target}")
        return

    with nb.build_order_lock():
        lines = load_lines()
        start, end = find_neg1_section(lines)
        if start < 0:
            log("link-meetings: ERROR no -1₲ section found")
            return

        headers = find_branch_headers(lines, start, end)
        if not headers:
            log("link-meetings: ERROR no 地支 headers found in -1₲")
            return

        # block_end[name] = start of next branch header, or section end for last branch
        block_end = {}
        for k, (name, idx) in enumerate(headers):
            block_end[name] = headers[k + 1][1] if k + 1 < len(headers) else end

        section_text = "\n".join(lines[start:end])

        inlined = 0
        floated = 0
        floats_by_branch = {}  # name -> [stems to float-insert]

        for m_h, m_min, stem in meetings:
            if stem in section_text:
                # Already linked somewhere — leave alone (manual placement wins)
                continue
            # 1) Time-window match: ±MEETING_START_TOLERANCE_MIN around the
            # recording start. Reliable when Toggl entries align with the
            # meeting's actual start.
            idx = _try_inline_append(lines, start, end, stem, m_h, m_min)
            if idx is not None:
                inlined += 1
                section_text = "\n".join(lines[start:end])
                continue
            # 2) Name fallback: if no time match, look for slug-token substrings
            # in time-entry descriptions (e.g. "accounting-analytics" → entry
            # titled "m5x2 Accounting & Analytics"). Catches cases where the
            # recording started long after the meeting (retro-recording).
            idx = _try_name_fallback(lines, start, end, stem)
            if idx is not None:
                inlined += 1
                section_text = "\n".join(lines[start:end])
                continue
            # 3) No match anywhere — float as standalone bullet under the
            # branch the meeting hour maps to.
            branch = hour_to_branch(m_h)
            floats_by_branch.setdefault(branch, []).append(stem)

        # Insert floats in reverse branch order so indices stay valid.
        # Re-resolve block_end after potential prior changes (line lengths unchanged
        # for inline appends, so positions still valid, but be safe).
        if floats_by_branch:
            headers2 = find_branch_headers(lines, *find_neg1_section(lines))
            block_end2 = {}
            for k, (name, idx) in enumerate(headers2):
                block_end2[name] = headers2[k + 1][1] if k + 1 < len(headers2) else find_neg1_section(lines)[1]
            for name, _ in reversed(headers2):
                stems = floats_by_branch.get(name)
                if not stems:
                    continue
                insertion = [f"    - [[d357/{s}|{s}]]" for s in stems]
                if dry_run:
                    log(f"[DRY RUN] Would float under {name} @ line {block_end2[name]}:")
                    for t in insertion:
                        log(f"  {t}")
                else:
                    lines[block_end2[name]:block_end2[name]] = insertion
                floated += len(insertion)

        if inlined == 0 and floated == 0:
            log("link-meetings: no new links (idempotent)")
            return
        save_lines(lines, dry_run=dry_run)
    log(f"link-meetings: appended {inlined} inline, floated {floated}")


# --- Neon (Excel) write via AppleScript ---

def _osascript(script: str, timeout: int = 180):
    """Run AppleScript. Returns (stdout, stderr, returncode) or raises.
    180s default to survive Excel autosave / recalc; the actual cell op is fast."""
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )


# AppleScript that finds the row for a given M/D date string, then runs an
# inline cell-op subscript and returns its result.
NEON_FIND_ROW_TEMPLATE = r'''
tell application "Microsoft Excel"
    set theSheet to sheet "{sheet}" of workbook "Neon分v12.2.xlsx"
    set targetDate to "{date_str}"
    set targetRow to 0
    repeat with i from 2 to 500
        if (string value of cell ("{date_col}" & i) of theSheet) = targetDate then
            set targetRow to i
            exit repeat
        end if
    end repeat
    if targetRow = 0 then
        return "ERROR: date " & targetDate & " not found in column {date_col}"
    end if
    {body}
end tell
'''


def _date_str(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def neon_lock_cell(target_date: dt.date, col: str, dry_run: bool = False) -> str:
    """Read computed value of `col` for target_date, write back as literal.
    No-op if the cell is not currently a formula. Returns status string.

    Negative residuals clamp to 0: D includes live 0n penalty rollups that sit
    negative until the morning habits are logged, so the 06:00 fire would
    otherwise freeze that transient into 卯 and inflate every later block's
    residual by the same amount (2026-06-12: 卯 locked at -46, 巳 showed
    173分 of a 127分 day). Locking 0 leaves the negative in the unlocked
    tail (=D-SUM(locked)), which self-corrects as the penalties clear."""
    if dry_run:
        log(f"[DRY RUN] Would lock {NEON_SHEET}!{col} for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = neon_excel.read(NEON_SHEET, col, date=_date_str(target_date))
        if not r.get("ok"):
            log(f"lock {col}: FAILED {r.get('error', '')}")
            return "FAILED"
        f = r.get("formula") or ""
        if f == "":
            log(f"lock {col}: EMPTY")
            return "EMPTY"
        if not f.startswith("="):
            log(f"lock {col}: ALREADY_LOCKED {f}")
            return f"ALREADY_LOCKED {f}"
        try:
            v = float(r.get("value") or 0)
        except ValueError:
            v = 0.0
        if v < 0:
            v = 0.0
        if v == int(v):
            v = int(v)
        if v == 0:
            # No points earned in the block (usually 卯, sometimes 辰): blank
            # the cell and fill it the same medium gray JM has been applying
            # by hand (RGB 128,128,128, per his existing manual cells — JM
            # 2026-08-10: "I've been doing this manually but I want you to do
            # it automatically"). A blank behaves identically to 0 in the
            # later blocks' =D-SUM(...) residuals, and the negative-clamp
            # case (see docstring) lands here too — visually the same
            # "nothing earned" state.
            w = neon_excel.write(NEON_SHEET, col, row=r["row"], value="",
                                 src=f"block-turnover lock {col} (empty block)")
            if not w.get("ok"):
                log(f"lock {col}: FAILED {w.get('error', '')}")
                return "FAILED"
            _fill_cell(r["row"], col, EMPTY_BLOCK_FILL)
            log(f"lock {col}: LOCKED EMPTY (no points — grayed)")
            return "LOCKED EMPTY"
        w = neon_excel.write(NEON_SHEET, col, row=r["row"], value=str(v),
                             src=f"block-turnover lock {col}")
        if not w.get("ok"):
            log(f"lock {col}: FAILED {w.get('error', '')}")
            return "FAILED"
        log(f"lock {col}: LOCKED {v}")
        return f"LOCKED {v}"
    except Exception as e:
        log(f"lock {col}: ERROR {e}")
        return "ERROR"


# JM's manual empty-block gray, sampled from his hand-formatted cells
# (0分!G213-G220, 2026-08-10).
EMPTY_BLOCK_FILL = (128, 128, 128)


def _fill_cell(row: int, col: str, rgb: tuple) -> None:
    """Set a 0分 cell's interior fill via local osascript. Best-effort and
    cosmetic only — the excel-http daemon has no formatting endpoint, and a
    fill is not a value write, so the ledger/daemon-only rule doesn't apply.
    This script runs ON ix (launchd), so osascript is local."""
    script = f'''
tell application "Microsoft Excel"
    set theCell to cell ("{col}{row}") of sheet "{NEON_SHEET}" of workbook "Neon分v12.2.xlsx"
    set color of interior object of theCell to {{{rgb[0]}, {rgb[1]}, {rgb[2]}}}
end tell
'''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True,
                       text=True, timeout=30)
    except Exception as e:  # noqa: BLE001 — never fail a lock over formatting
        log(f"fill {col}{row}: skipped ({e})")


def neon_lock_cell_with_retry(target_date: dt.date, col: str,
                              dry_run: bool = False, attempts: int = 3,
                              retry_wait: int = 15) -> str:
    """neon_lock_cell, retried on transient failure.

    A single osascript_timeout at fire time used to be terminal: the fire
    logged FAILED and moved on, leaving the block column as a live
    `=D-SUM(...)` residual that silently swallowed every later block's
    points for the rest of the day (2026-08-10: the 08:00 lock of H timed
    out once, so 辰 accumulated all of 巳's points; the sheet needed a
    hand-reconstructed retro-lock). The daemon's Excel writes elsewhere
    already tolerate a slow excel-http daemon; the lock is the one write
    whose failure corrupts the whole day's split, so it retries hardest.
    """
    status = "ERROR"
    for attempt in range(1, attempts + 1):
        status = neon_lock_cell(target_date, col, dry_run=dry_run)
        # Only transient outcomes are worth retrying. EMPTY/ALREADY_LOCKED/
        # LOCKED/DRY_RUN are all final.
        if not (status == "FAILED" or status == "ERROR" or status.startswith("ERROR")):
            return status
        if attempt < attempts:
            log(f"lock {col}: attempt {attempt}/{attempts} failed — retrying in {retry_wait}s")
            time.sleep(retry_wait)
    log(f"lock {col}: gave up after {attempts} attempts — next fire's self-heal sweep will retry")
    return status


def self_heal_unlocked_blocks(today: dt.date, hour: int, dry_run: bool = False) -> list:
    """Lock any EARLIER fire-hour column still holding a formula.

    Backstop for a lock that failed every retry at its own fire: without
    this, the column stays a live residual until someone notices by hand
    (the 2026-08-10 incident sat corrupting attribution for 90 minutes).
    Locking a block late freezes it at a value inflated by the time since
    its boundary — attribution between the two adjacent blocks is off by
    that overlap, but the day total is conserved and every LATER block
    measures correctly again. Returns the columns healed.
    """
    healed = []
    for fire_hour, col in sorted(LOCK_AT_FIRE_HOUR.items()):
        if fire_hour >= hour:
            break
        try:
            r = neon_excel.read(NEON_SHEET, col, date=_date_str(today))
            f = (r.get("formula") or "") if r.get("ok") else ""
            if f.startswith("="):
                log(f"self-heal: {col} (fire {fire_hour:02d}) still a formula — locking late")
                status = neon_lock_cell_with_retry(today, col, dry_run=dry_run)
                healed.append((col, status))
        except Exception as e:  # noqa: BLE001 — healing must never block the fire
            log(f"self-heal: {col} check skipped: {e}")
    return healed


def neon_set_marker(target_date: dt.date, col: str, dry_run: bool = False) -> str:
    """Set `col` cell for target_date to 1, but only if currently empty.
    Returns status string."""
    if dry_run:
        log(f"[DRY RUN] Would mark {NEON_SHEET}!{col} = 1 for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = neon_excel.read(NEON_SHEET, col, date=_date_str(target_date))
        if not r.get("ok"):
            log(f"mark {col}: FAILED {r.get('error', '')}")
            return "FAILED"
        v = r.get("value") or ""
        if v != "":
            log(f"mark {col}: ALREADY {v}")
            return f"ALREADY {v}"
        w = neon_excel.write(NEON_SHEET, col, row=r["row"], value="1",
                             src=f"block-turnover mark {col}")
        if not w.get("ok"):
            log(f"mark {col}: FAILED {w.get('error', '')}")
            return "FAILED"
        log(f"mark {col}: SET")
        return "SET"
    except Exception as e:
        log(f"mark {col}: ERROR {e}")
        return "ERROR"


def neon_add_score_to_p(target_date: dt.date, score: int, dry_run: bool = False) -> str:
    """Append score to -1₦ column (P) for target_date's row as =13+10+8 formula,
    so the user can see a record of what was added at each block boundary.
    The client's append handles the empty-cell / bare-number normalization."""
    if dry_run:
        log(f"[DRY RUN] Would add {score} to P for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = neon_excel.append(NEON_SHEET, NEON_NEG1_COL,
                              date=_date_str(target_date), value=f"+{score}",
                              src="block-turnover -1n score")
        if not r.get("ok"):
            log(f"add_score_to_p: FAILED {r.get('error', '')}")
            return "FAILED"
        out = f"P_APPEND {r.get('formula', '')}"
        log(f"add_score_to_p: {out}")
        verify = neon_read_y(target_date)
        if verify == "ERROR" or verify == "" or verify == "0":
            log(f"add_score_to_p: VERIFY FAILED — wrote but read back {verify}. Excel may not be open.")
            return "VERIFY_FAILED"
        log(f"add_score_to_p: verified={verify}")
        return out
    except Exception as e:
        log(f"add_score_to_p: ERROR {e}")
        return "ERROR"


def _prev_block_window(hour: int, target_date: dt.date) -> tuple[int, int, dt.date]:
    """The (start_hour, end_hour, date) of the block immediately before the one
    that just ended at `hour`, for -1t/-1l's retrospective "was it recorded"
    check.

    For every block except 卯, the previous block is a clean 2h window earlier
    the SAME day (hour-4, hour-2 lands exactly on the prior 地支 block's own
    hours). 卯 (fires at 06) is the exception: hour-4/hour-2 = [02,04], which
    falls inside the unscored overnight sleep gap (22:00-04:00) — a single
    sleep Toggl entry always trivially covers it, so ⏱️/✅ for 卯 never
    actually signaled anything (observed live 2026-07-19: ⏱️ credited for 卯
    while asleep and having done nothing). 卯's true previous block is 亥
    (20:00-22:00) of the PREVIOUS calendar day."""
    if hour == 6:  # 卯 fires at 06 — its real previous block is 亥, prior day
        return 20, 22, target_date - dt.timedelta(days=1)
    return hour - 4, hour - 2, target_date


def _live_for_block(block_name: str, hour: int, target_date: dt.date):
    """Read-only re-validation of a block's daemon-checkable markers (🎯/⏱️/✅),
    with NO marker writes. Used to re-score already-fired blocks during a
    reconcile. Returns None on any validation error so the block falls back to
    trusting its header markers (legacy behavior) rather than losing points."""
    # ⏱️/✅ measure the PREVIOUS block: block X's -1t/-1l reward having RECORDED
    # block X-1 (its Toggl time categorized, its completed tasks pointed). 🎯
    # stays current-block (goals are set for X itself). See _prev_block_window
    # for the 卯/sleep-gap wraparound exception.
    prev_start, prev_end, prev_date = _prev_block_window(hour, target_date)
    try:
        return {
            GOAL_MARKER: _block_has_goals(block_name),
            TOGGL_MARKER: _toggl_covers_block(prev_date, prev_start, prev_end),
            TODOIST_MARKER: _todoist_l_satisfied(prev_date, prev_start, prev_end),
        }
    except Exception as e:  # noqa: BLE001 — never drop points on a transient API error
        log(f"_live_for_block {block_name}: validation error {e}; trusting header")
        return None


def neon_set_p(target_date: dt.date, formula: str, total: int, dry_run: bool = False) -> str:
    """SET -1₦ (col P) to `formula` (e.g. '=4+3+13'), replacing the cell. Used
    by the reconcile so the value is idempotent and self-healing (no double-count
    on re-fire). Verifies the write by reading the cell back, mirroring
    neon_add_score_to_p."""
    if dry_run:
        log(f"[DRY RUN] Would set P={formula} ({total}) for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = neon_excel.write(NEON_SHEET, NEON_NEG1_COL,
                             date=_date_str(target_date), value=formula,
                             src="block-turnover -1n reconcile")
        if not r.get("ok"):
            log(f"reconcile_p: FAILED {r.get('error', '')}")
            return "FAILED"
        out = f"P_RECONCILE {formula}"
        log(f"reconcile_p: {out}")
        verify = neon_read_y(target_date)
        if verify in ("ERROR", "", "0"):
            log(f"reconcile_p: VERIFY FAILED — wrote but read back {verify}. Excel may not be open.")
            return "VERIFY_FAILED"
        log(f"reconcile_p: verified={verify}")
        return out
    except Exception as e:
        log(f"reconcile_p: ERROR {e}")
        return "ERROR"


def reconcile_p_for_day(target_date: dt.date, upto_hour: int,
                        current_live: dict | None = None, dry_run: bool = False) -> str:
    """Recompute -1₦ (P) as the validated score of EVERY fired block today and
    SET the cell, instead of appending one block's score at its boundary.

    This self-heals late markers: a prayer (☀️) logged after its block's boundary,
    or a toggl/todoist entry that lands after the fire, is picked up on the next
    fire. Re-validating same-day past blocks is safe because -1g goals are only
    cleared next-day. The current block reuses `current_live` (already computed by
    evaluate_and_mark_block) to avoid a redundant re-query."""
    parts = []
    for fh in sorted(h for h in BLOCK_FIRE_HOURS if h <= upto_hour):
        bn = HOUR_TO_BRANCH_BLOCK.get(fh)
        if not bn:
            continue  # 04 is the day start — no just-ended block
        if fh == upto_hour and current_live is not None:
            live = current_live  # just-ended block: already evaluated + stamped
        else:
            # An already-closed block: re-validate against fresh live data and
            # back-fill ONLY the retrospective auto markers (-1t ⏱️, -1l ✅) when
            # Toggl/Todoist data that landed after the block's own boundary fire
            # now satisfies them. This keeps each completed block's -1t/-1l
            # accurate, not only the one that closed with its data already
            # settled. -1g (🎯) is deliberately excluded: goals are a manual,
            # current-block ritual, never back-filled onto a past block.
            live = _live_for_block(bn, fh, target_date)
            if live:
                if live.get(TOGGL_MARKER):
                    _write_block_marker(bn, TOGGL_MARKER, dry_run=dry_run)
                if live.get(TODOIST_MARKER):
                    _write_block_marker(bn, TODOIST_MARKER, dry_run=dry_run)
        # Drop any daemon-owned marker that fresh live data says wasn't earned,
        # so phantoms (stale ✅/⏱️/🎯) don't linger on the header.
        _strip_unearned_markers(bn, live, dry_run=dry_run)
        parts.append(score_block_from_emojis(bn, live=live))
    total = sum(parts)
    # One term per block — no literal leading "0" (see neon_blocks.score_day).
    formula = "=" + "+".join(str(p) for p in parts) if parts else "=0"
    log(f"reconcile_p: {target_date} parts={parts} total={total}")
    return neon_set_p(target_date, formula, total, dry_run=dry_run)


def _branch_for_hour(hour: int) -> str | None:
    """The in-progress 地支 block name for a wall-clock hour, or None outside
    the 04–22 ritual day."""
    for name, s, e in BRANCH_HOURS:
        if s <= hour <= e:
            return name
    return None


def compute_p_formula(target_date: dt.date, upto_hour: int,
                      current_block: str | None = None):
    """Pure -1₦ (P) score from CURRENTLY-STAMPED header emojis: every block that
    has closed (fire hour <= upto_hour) plus the in-progress `current_block`.

    Unlike reconcile_p_for_day this TRUSTS the stamps (live=None) — it does NOT
    re-validate against Toggl/Todoist and writes nothing: no Excel, no build-order
    mutation, no API calls. It backs the on-demand path (a ritual completed
    mid-block credits P immediately and instantly); the daemon's boundary
    reconcile_p_for_day stays the validating self-heal that strips stale stamps
    and re-SETs. The current block scores with live=None so its manual ☀️/🎯/📧
    count as stamped while its retrospective ⏱️/✅ (unknowable until the block
    closes) are simply absent. Returns (formula, total, parts)."""
    parts = []
    for fh in sorted(h for h in BLOCK_FIRE_HOURS if h <= upto_hour):
        bn = HOUR_TO_BRANCH_BLOCK.get(fh)
        if not bn:
            continue
        parts.append(score_block_from_emojis(bn, live=None))
    if current_block:
        parts.append(score_block_from_emojis(current_block, live=None))
    total = sum(parts)
    # One term per block — no literal leading "0" (see neon_blocks.score_day).
    formula = "=" + "+".join(str(p) for p in parts) if parts else "=0"
    return formula, total, parts


def neon_read_y(target_date: dt.date) -> str:
    """Read computed value of -1₦ (col Y) for target_date. Returns string or 'ERROR'."""
    body = (
        f'set theCell to cell ("{NEON_NEG1_COL}" & targetRow) of theSheet\n'
        '    return (value of theCell) as text\n'
    )
    script = NEON_FIND_ROW_TEMPLATE.format(
        sheet=NEON_SHEET, date_str=_date_str(target_date),
        date_col=NEON_DATE_COL, body=body,
    )
    try:
        r = _osascript(script)
        out = (r.stdout or "").strip()
        if r.returncode != 0 or out.startswith("ERROR"):
            log(f"read Y: FAILED {out or r.stderr.strip()}")
            return "ERROR"
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"read Y: ERROR {e}")
        return "ERROR"


# --- Block scoring: emoji-based detection and marking ---

def _block_line_name(line: str) -> str:
    """Block name from a `- 卯 ...` header line: the first whitespace token
    after the bullet. Headers accumulate annotations in the middle and at the
    end (`- 辰 (25min)   (32min) 😈`, `(15分, 163min)`), so any strip-suffixes
    approach breaks; the leading token is the only stable part."""
    rest = line[2:].strip()
    return rest.split()[0] if rest else ""


def _has_block_marker(block_name: str, marker: str) -> bool:
    """Check if a block header in -1₲ section has a specific emoji marker."""
    if not BUILD_ORDER.exists():
        return False
    text = BUILD_ORDER.read_text(encoding="utf-8")
    if "## -1₲" not in text:
        return False
    section = text[text.index("## -1₲"):]
    for line in section.split("\n"):
        if line.startswith("## ") and line != "## -1₲":
            break
        if line.startswith("- ") and not line.startswith("    "):
            # Extract block name (strip markers, duration suffix)
            name = _block_line_name(line)
            if name == block_name and marker in line:
                return True
    return False


def _write_block_marker(block_name: str, marker: str, dry_run: bool = False) -> bool:
    """Append an emoji marker to a block header in -1₲. Idempotent.

    Lock-protected (2026-08-02): the check-then-write (including the nested
    _has_block_marker call) must be atomic as a whole, or a concurrent
    writer (another daemon fire, an on-Ix did-fast ritual completion) can
    land between the check and the write and get silently discarded —
    third instance of this exact bug class."""
    with nb.build_order_lock():
        if _has_block_marker(block_name, marker):
            return False
        if not BUILD_ORDER.exists():
            return False
        text = BUILD_ORDER.read_text(encoding="utf-8")
        if "## -1₲" not in text:
            return False
        lines = text.split("\n")
        in_section = False
        for i, line in enumerate(lines):
            if line.strip() == "## -1₲":
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if not in_section:
                continue
            if line.startswith("- ") and not line.startswith("    "):
                name = _block_line_name(line)
                if name == block_name:
                    if dry_run:
                        log(f"[DRY RUN] Would write {marker} to {block_name}")
                        return True
                    lines[i] = line.rstrip() + " " + marker
                    BUILD_ORDER.write_text("\n".join(lines), encoding="utf-8")
                    log(f"marker: wrote {marker} to {block_name}")
                    return True
        return False


def _block_has_goals(block_name: str) -> bool:
    """Check if the block has any goals (checked or unchecked) in -1₲ section."""
    if not BUILD_ORDER.exists():
        return False
    text = BUILD_ORDER.read_text(encoding="utf-8")
    if "## -1₲" not in text:
        return False
    section = text[text.index("## -1₲"):]
    current_block = None
    for line in section.split("\n"):
        if line.startswith("## ") and current_block is not None:
            break
        if line.startswith("- ") and not line.startswith("    "):
            name = _block_line_name(line)
            current_block = name
        elif current_block == block_name:
            if re.match(r"^    - \[[ xX]\]\s*\S", line):  # require real goal text, not just trailing whitespace
                return True
    return False


def _toggl_covers_block(target_date: dt.date, start_hour: int, end_hour: int) -> bool:
    """-1t: at least TOGGL_MIN_MINUTES of the block window are *categorized* —
    covered by a Toggl entry that has a project assigned. Uncategorized
    (project-less) time does not count."""
    try:
        start = target_date.isoformat()
        end = (target_date + dt.timedelta(days=1)).isoformat()
        entries = _toggl_get(f"/me/time_entries?start_date={start}&end_date={end}")
    except Exception as e:
        log(f"toggl coverage check: ERROR {e}")
        return False

    block_start_min = start_hour * 60
    block_end_min = end_hour * 60
    block_duration = block_end_min - block_start_min  # 120 min

    # Collect covered minutes within the block window
    covered = [False] * block_duration
    for entry in entries:
        # -1t requires *categorized* time: skip entries with no project.
        if not entry.get("project_id"):
            continue
        dur = entry.get("duration", 0)
        start_str = entry.get("start", "")
        if not start_str:
            continue
        entry_start = dt.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        entry_start_local = entry_start.astimezone()
        if entry_start_local.date() != target_date:
            continue
        if dur > 0:
            entry_end_local = entry_start_local + dt.timedelta(seconds=dur)
        elif dur < 0:
            # Running timer
            entry_end_local = dt.datetime.now(dt.timezone.utc).astimezone()
        else:
            continue
        # Convert to minutes-of-day
        es_min = entry_start_local.hour * 60 + entry_start_local.minute
        ee_min = entry_end_local.hour * 60 + entry_end_local.minute
        # Clip to block window
        cs = max(es_min, block_start_min) - block_start_min
        ce = min(ee_min, block_end_min) - block_start_min
        if cs >= ce:
            continue
        for m in range(cs, ce):
            covered[m] = True

    categorized_min = sum(covered)
    log(f"toggl coverage: {block_name_for_hours(start_hour)} = {categorized_min}/{block_duration} min categorized (need {TOGGL_MIN_MINUTES})")
    return categorized_min >= TOGGL_MIN_MINUTES


def block_name_for_hours(start_hour: int) -> str:
    """Get block name from start hour."""
    for name, lo, _ in BRANCH_HOURS:
        if lo == start_hour:
            return name
    return "?"


def _todoist_token() -> str:
    """Todoist API token from env (launchd/cron pass TODOIST_API_KEY) or keychain."""
    token = os.environ.get("TODOIST_API_KEY", "").strip()
    if not token:
        try:
            token = subprocess.run(
                ["security", "find-generic-password", "-s", TODOIST_KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception as e:
            log(f"todoist token: keychain error {e}")
            return ""
    return token


def _at_labels(content: str) -> set:
    """Labels rendered inline as @tokens in a completed item's content (the
    completed API returns labels=None but appends '@label' to content)."""
    return set(re.findall(r"@(\S+)", content or ""))


def _has_points(content: str) -> bool:
    """True if the content carries a [N] (task value) or {N} (0g bonus) marker
    with N > 0 — both are legitimate points conventions (see did-fast.py)."""
    nums = re.findall(r"\[(\d+)\]", content or "") + re.findall(r"\{(\d+)\}", content or "")
    return any(int(n) > 0 for n in nums)


def _is_defer_noise(content: str) -> bool:
    """Posthoc defer-tracking records ("deferred: X [N] (M) [N] → date [N]")
    log a reschedule, not accomplished work — never count toward -1l even
    though they're posthoc and often carry a stray [N] from the before/after
    state they track."""
    return content.strip().lower().startswith("deferred:")


def _todoist_l_satisfied(target_date: dt.date, start_hour: int, end_hour: int) -> bool:
    """-1l: every real (non-habit) Todoist task completed in the block window
    carries [N] points. Empty (no real completions) = not satisfied. Recurring
    habits don't surface in the completed API; the noise to strip is the
    -1neon rituals (NON_TASK_LABELS) and posthoc defer records (_is_defer_noise)
    — a pointed posthoc *meeting* completion is real work and counts."""
    token = _todoist_token()
    if not token:
        log("-1l: no API token in env or keychain")
        return False

    tz = daytime.active_zone()
    block_start = dt.datetime(target_date.year, target_date.month, target_date.day,
                              start_hour, 0, tzinfo=tz)
    block_end = dt.datetime(target_date.year, target_date.month, target_date.day,
                            end_hour, 0, tzinfo=tz)
    since = block_start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    until = block_end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    url = f"https://api.todoist.com/api/v1/tasks/completed?since={since}&until={until}&limit=100"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            items = json.loads(resp.read()).get("items", [])
    except Exception as e:
        log(f"-1l: API error {e}")
        return False

    real = [it for it in items
            if not (_at_labels(it.get("content", "")) & NON_TASK_LABELS)
            and not _is_defer_noise(it.get("content", ""))]
    if not real:
        log(f"-1l: {start_hour:02d}-{end_hour:02d} no real task completions → fail")
        return False
    unpointed = [it.get("content", "") for it in real
                 if not _has_points(it.get("content", ""))]
    if unpointed:
        log(f"-1l: {len(unpointed)}/{len(real)} unpointed → fail: {unpointed}")
        return False
    log(f"-1l: {len(real)} real task(s) all pointed → pass")
    return True


def evaluate_and_mark_block(block_name: str, hour: int, target_date: dt.date,
                            dry_run: bool = False) -> dict:
    """Evaluate -1g, -1t, -1l for the just-ended block, write emojis, and return
    the live validation results keyed by marker. The returned dict is the
    authoritative source for scoring: a marker on the header only earns points
    if its live check passed in THIS run, so a stale marker left over from a
    prior day (e.g. a 🎯 with no goals today) cannot award phantom points."""
    live: dict = {}

    # -1g: goals were set for this block
    live[GOAL_MARKER] = _block_has_goals(block_name)
    if live[GOAL_MARKER]:
        _write_block_marker(block_name, GOAL_MARKER, dry_run=dry_run)
    else:
        log(f"score: {block_name} no goals found")

    # -1t/-1l measure the PREVIOUS block: block X's ⏱️/✅ reward having RECORDED
    # block X-1 (e.g. 戌's ⏱️ ⇔ 酉 fully recorded). See _prev_block_window for
    # the 卯/sleep-gap wraparound exception (its previous block is 亥, prior day).
    prev_start, prev_end, prev_date = _prev_block_window(hour, target_date)
    live[TOGGL_MARKER] = _toggl_covers_block(prev_date, prev_start, prev_end)
    if live[TOGGL_MARKER]:
        _write_block_marker(block_name, TOGGL_MARKER, dry_run=dry_run)
    else:
        log(f"score: {block_name} prev-block toggl coverage below threshold")

    # -1l: todoist completions during the PREVIOUS block
    live[TODOIST_MARKER] = _todoist_l_satisfied(prev_date, prev_start, prev_end)
    if live[TODOIST_MARKER]:
        _write_block_marker(block_name, TODOIST_MARKER, dry_run=dry_run)
    else:
        log(f"score: {block_name} no todoist completions")

    return live


def _marker_earned(emoji: str, line: str, live: dict | None) -> bool:
    """Decide whether a marker earns its points for a block header `line`.

    The emoji must be present on the header — that's the only requirement.
    ☀️/📧/⏱️/✅/🎯 are all trusted on header presence alone, no daemon-side
    audit strips any of them.

    🎯 was previously live-gated against `_block_has_goals` (a goal-presence
    check that could strip the marker even after a manual ritual
    completion) — removed 2026-08-11 per JM: "-1g should always give me the
    points and audit should not revoke them." Completing the 😈 -1g card is
    itself the attestation; it doesn't need separate goal text on file to
    count, matching how -1t/-1l already work below.

    ⏱️/✅ are deliberately NOT audited (⏱️'s audit added 2026-07-30, removed
    2026-08-01 per JM: "If I manually mark -1l or -1t there shouldn't be an
    audit. That should have been an audit that would happen at [the fire] to
    see if I was close enough to not need to manually check."). Completing
    -1t/-1l is a direct first-person attestation of the previous block's
    recording; the live checks (_toggl_covers_block, _todoist_l_satisfied)
    are narrow proxies that can legitimately fail on genuinely-worked blocks.
    Their role is AUTO-AWARD only: at each fire the daemon stamps ⏱️/✅ itself
    when its own check passes, sparing the manual claim — it never claws a
    stamp back. When `live` is None (e.g. a re-score with no evaluation
    pass), all markers are trusted, preserving legacy behavior."""
    if emoji not in line:
        return False
    if LOCK_MARKER in line:
        return True
    if emoji in (TODOIST_MARKER, TOGGL_MARKER, GOAL_MARKER):
        return True
    if live is not None and emoji in live and not live[emoji]:
        return False
    return True


# No marker is stripped from a header once stamped (2026-08-11: 🎯's
# goal-presence strip was removed per JM, same reasoning as _marker_earned
# above — completing a ritual card is itself the attestation). Kept as an
# explicit empty set, not deleted outright, so _strip_unearned_markers stays
# wired in if a future marker ever needs this kind of audit again.
DAEMON_OWNED_MARKERS: set[str] = set()


def score_block_from_emojis(block_name: str, live: dict | None = None) -> int:
    """Sum points from emojis on the block header, validated against `live`
    results so stale markers don't score. See `_marker_earned`. Pure read — the
    header is NOT mutated here (stripping stale markers is a separate concern;
    see `_strip_unearned_markers`), so a later re-score with different `live`
    still sees every marker."""
    if not BUILD_ORDER.exists():
        return 0
    text = BUILD_ORDER.read_text(encoding="utf-8")
    if "## -1₲" not in text:
        return 0
    section = text[text.index("## -1₲"):]
    for line in section.split("\n"):
        if line.startswith("## ") and line != "## -1₲":
            break
        if line.startswith("- ") and not line.startswith("    "):
            name = _block_line_name(line)
            if name == block_name:
                earned = [e for e, pts in SCORE_EMOJI_MAP.items()
                          if _marker_earned(e, line, live)]
                stale = [e for e in SCORE_EMOJI_MAP
                         if e in line and e not in earned]
                score = sum(SCORE_EMOJI_MAP[e] for e in earned)
                msg = f"score: {block_name} = {score} pts (from: {earned})"
                if stale:
                    msg += f" [ignored stale markers: {stale}]"
                log(msg)
                return score
    return 0


def _strip_unearned_markers(block_name: str, live: dict | None,
                            dry_run: bool = False) -> None:
    """Remove daemon-owned markers (🎯/⏱️/✅) from a block header when a
    successful live re-validation proves they weren't earned, so phantom marks
    (e.g. a stale ✅ from a prior day, or an old eager task-completion stamp)
    don't linger visually. No-op when `live is None` (a Toggl/Todoist API
    failure must never destroy a genuinely-earned mark) and never touches
    ☀️/📧 (no daemon-side validator)."""
    if not live or not BUILD_ORDER.exists():
        return
    unearned = [m for m in DAEMON_OWNED_MARKERS if m in live and not live[m]]
    if not unearned:
        return
    # Lock-protected (2026-08-02) — same read-modify-write race as
    # _write_block_marker, just on the strip side.
    with nb.build_order_lock():
        text = BUILD_ORDER.read_text(encoding="utf-8")
        if "## -1₲" not in text:
            return
        lines = text.split("\n")
        in_section = False
        for i, line in enumerate(lines):
            if line.startswith("## -1₲"):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if not in_section:
                continue
            if (line.startswith("- ") and not line.startswith("    ")
                    and _block_line_name(line) == block_name):
                if LOCK_MARKER in line:
                    return  # user-attested block: stamps are never stripped
                present = [m for m in unearned if m in line]
                if present:
                    new_line = line
                    for m in present:
                        new_line = new_line.replace(m, "")
                    new_line = re.sub(r"\s{2,}", " ", new_line).rstrip()
                    if new_line != line and not dry_run:
                        lines[i] = new_line
                        BUILD_ORDER.write_text("\n".join(lines), encoding="utf-8")
                    log(f"strip: {block_name} removed stale {present}"
                        + (" [DRY RUN]" if dry_run else ""))
                return


# --- Mode: lock-and-mark ---

def annotate_block_fired(hour: int, dry_run: bool = False) -> None:
    """Append the fired emoji to the matching 地支 block header in the build order.
    Idempotent — skips if the emoji is already on that header line."""
    branch = HOUR_TO_BRANCH_BLOCK.get(hour)
    if branch is None:
        return
    with nb.build_order_lock():
        try:
            lines = load_lines()
        except OSError as e:
            log(f"annotate: ERROR can't read build order: {e}")
            return
        target_prefix = f"- {branch}"
        for i, line in enumerate(lines):
            # Match a 地支 block header (line starts with "- <branch>" possibly followed by space + extras)
            if not line.startswith(target_prefix):
                continue
            # Header found; ensure it's a header (next char is end-of-line or whitespace)
            rest = line[len(target_prefix):]
            if rest and not rest[0].isspace():
                continue
            if DAEMON_FIRED_EMOJI in line:
                log(f"annotate: {branch} already marked")
                return
            lines[i] = line.rstrip() + " " + DAEMON_FIRED_EMOJI
            if dry_run:
                log(f"[DRY RUN] Would annotate {branch}: {lines[i]!r}")
                return
            save_lines(lines)
            log(f"annotate: marked {branch} with {DAEMON_FIRED_EMOJI}")
            return
        log(f"annotate: {branch} block header not found in build order")


def _load_block_rituals() -> dict:
    return json.loads(BLOCK_RITUALS_CONFIG.read_text(encoding="utf-8"))


def _ritual_bare_tag(content: str, marker: str, tags: list[str]) -> str | None:
    """The ritual tag a card's bare (marker-stripped) content matches, tolerating
    trailing annotations like `(15) [15]` — same whole-token comparison as
    lib/neon_blocks.ritual_card_tag() (kept as a local copy here: this module
    doesn't import neon_blocks). None if it matches no known tag.

    Exact-string bare matching (the previous behavior of both callers below)
    broke the instant a ritual card picked up ANY suffix: create_block_rituals'
    dedup check (`tag in open_bare`) stopped recognizing the card as already
    open and created a duplicate every 2h fire, while delete_block_rituals'
    earned-check (`bare in auto_emoji`) stopped recognizing an EARNED auto
    card, silently deleting it (no credit) instead of closing it (bug
    2026-07-29: "seeing a lot of extra -1n" + uniform bogus (15)[15] on every
    ritual card, which real ritual cards never carry -- their points come from
    0分!P via the block header, never from [N])."""
    bare = (content or "").replace(marker, "").strip()
    for tag in tags:
        if bare == tag or tag in bare.split():
            return tag
    return None


def _is_transient_todoist_error(e: Exception) -> bool:
    """5xx/429 and network-level failures are worth retrying (Todoist-side
    hiccup); 4xx like a bad token or malformed payload are not — retrying
    those just wastes time before failing the same way."""
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (429, 500, 502, 503, 504)
    if isinstance(e, urllib.error.URLError):
        return True  # covers socket.timeout too (URLError's reason chain)
    return False


def _todoist_write_retry(path: str, payload: dict | None, token: str, method: str = "POST",
                         attempts: int = 3, delay: float = 2.0) -> tuple[int, bytes]:
    """_todoist_write with retry on transient failures (2026-08-20: a
    Todoist-side 502/503 blip at exactly the 巳 turnover meant
    create_block_rituals' single unretried attempt silently skipped
    creating the سمش and -1g cards for the whole block -- the OTHER 3
    cards, created seconds later in the same daemon run once Todoist
    recovered, all succeeded. Nothing surfaced this to the user; the block
    scored 9/13 with no card ever existing to close for the missing 4
    points, and "I did all -1n" was literally true for what was visible."""
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _todoist_write(path, payload, token, method=method)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < attempts and _is_transient_todoist_error(e):
                time.sleep(delay)
                continue
            raise
    raise last_err  # pragma: no cover — loop always returns or raises above


def _todoist_open_rituals(token: str) -> list | None:
    """All currently-open tasks carrying the -1neon label. None on fetch
    failure — distinct from an empty list (genuinely nothing open) — so
    callers fail closed instead of treating an API hiccup as license to
    duplicate (bug 2026-07-30: a 503 here made create_block_rituals think
    nothing was open and create a full second set of cards)."""
    from urllib.parse import quote
    url = f"https://api.todoist.com/api/v1/tasks?label={quote('-1neon')}&limit=200"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    # Retry on transient failures too (same class of Todoist-side blip that
    # hit create_block_rituals' own writes on 2026-08-20) — this fetch
    # gating ALL card creation for the fire means an unretried hiccup here
    # is even more consequential: it skips creating every ritual card for
    # the block, not just some.
    attempts, delay = 3, 2.0
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return data.get("results", data) if isinstance(data, dict) else data
        except Exception as e:
            if attempt < attempts and _is_transient_todoist_error(e):
                time.sleep(delay)
                continue
            log(f"rituals: fetch open ERROR {e}")
            return None


def _todoist_write(path: str, payload: dict | None, token: str, method: str = "POST") -> tuple[int, bytes]:
    """POST/DELETE against the Todoist v1 API. Returns (HTTP status, body)."""
    url = f"https://api.todoist.com/api/v1{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


def create_block_rituals(dry_run: bool = False) -> None:
    """At a block start, create ALL 5 -1neon cards (😈-marked) for the current
    block — manual (سمش/-1g/-1ibx) and auto (-1t/-1l) alike, so the full ritual
    set is visible as tasks (user request 2026-07-05). Skips any already open so
    a catch-up/duplicate fire can't create doubles.

    Auto cards are visibility/acknowledgment only: the daemon stays the sole
    evaluator of ⏱️/✅ at the block close, where an EARNED auto card is
    completed and an unearned one deleted (see delete_block_rituals)."""
    token = _todoist_token()
    if not token:
        log("rituals: no token — cannot create cards")
        return
    cfg = _load_block_rituals()
    marker, label = cfg.get("auto_marker", "😈"), cfg["label"]
    tags = [r["tag"] for r in cfg["rituals"]]
    open_rituals = _todoist_open_rituals(token)
    if open_rituals is None:
        log("rituals: could not verify open cards — skipping creation this fire")
        return
    open_tags = {_ritual_bare_tag(t.get("content") or "", marker, tags)
                 for t in open_rituals}
    for r in cfg["rituals"]:
        tag = r["tag"]
        if tag in open_tags:
            log(f"rituals: {tag} already open — skip")
            continue
        content = f"{marker} {tag}"
        if dry_run:
            log(f"[DRY RUN] create card {content!r} @{label}")
            continue
        try:
            status, body = _todoist_write_retry("/tasks", {"content": content, "labels": [label],
                                      "due_string": "today"}, token)
            log(f"rituals: + {content}")
            # Verify the label actually persisted (bug 2026-07-30: 3 of 5
            # cards created in the same batch came back with labels=[] --
            # a Todoist-side write flake on rapid-fire creates. An
            # unlabeled card is invisible to _todoist_open_rituals'
            # label-filtered fetch, so it never gets retired by
            # delete_block_rituals and just orphans in the inbox forever,
            # each recurring fire creating another duplicate on top since
            # the dedup check can't see it either).
            try:
                created = json.loads(body)
                if label not in (created.get("labels") or []):
                    tid = created.get("id")
                    log(f"rituals: {content!r} created without {label!r} label — repairing")
                    _todoist_write(f"/tasks/{tid}", {"labels": [label]}, token)
            except Exception as e:
                log(f"rituals: label verify/repair for {content!r} ERROR {e}")
        except Exception as e:
            log(f"rituals: create {content!r} ERROR {e}")


def delete_block_rituals(dry_run: bool = False, live: dict | None = None) -> None:
    """At a block turnover, retire the just-ended block's still-open -1neon cards.

    Manual cards (سمش/-1g/-1ibx): delete — skipped means no points, and a stale
    card pollutes the list. Auto cards (-1t/-1l): if the just-ended block's
    evaluation (`live`, keyed by marker emoji) says EARNED, complete the card so
    it counts as a done task; otherwise delete it like a skipped manual card.
    With no live results (API failure), auto cards fall back to delete — never
    award a completion the evaluator didn't confirm."""
    token = _todoist_token()
    if not token:
        return
    cfg = _load_block_rituals()
    marker = cfg.get("auto_marker", "😈")
    tags = [r["tag"] for r in cfg["rituals"]]
    auto_emoji = {r["tag"]: r["emoji"] for r in cfg["rituals"]
                  if r.get("mode") == "auto"}
    open_rituals = _todoist_open_rituals(token)
    if open_rituals is None:
        log("rituals: could not verify open cards — skipping retirement this fire")
        return
    for t in open_rituals:
        tid, content = t.get("id"), t.get("content", "")
        tag = _ritual_bare_tag(content, marker, tags)
        earned = (tag in auto_emoji and live is not None
                  and bool(live.get(auto_emoji[tag])))
        verb = "close (earned)" if earned else "delete"
        if dry_run:
            log(f"[DRY RUN] {verb} leftover card {content!r}")
            continue
        try:
            if earned:
                _todoist_write(f"/tasks/{tid}/close", {}, token)
                log(f"rituals: ✓ {content} (earned at block close)")
            else:
                _todoist_write(f"/tasks/{tid}", None, token, method="DELETE")
                log(f"rituals: − {content}")
        except Exception as e:
            log(f"rituals: {verb} {content!r} ERROR {e}")


def _refresh_dtd_cache(dry_run: bool = False) -> None:
    """Rebuild task-queue.json so the new block's -1neon cards reach dtd at the
    block turn instead of on the periodic refresh daemon's next cycle (~3min) —
    the 'rituals didn't reappear at the turn of the block' bug (2026-07-03).

    The cards are created with due_string 'today', so they land in the cache's
    'today' bucket; but creating them in Todoist does not itself rebuild the
    cache, and dtd only reloads on a task-queue.json mtime bump. Mirrors the
    --refresh-cache call /0g and /0t make after mutating tasks. Foreground with a
    short timeout; a refresh failure must never break the fire."""
    if dry_run:
        log("[DRY RUN] refresh dtd cache")
        return
    try:
        subprocess.run(
            ["python3", str(DID_FAST), "--refresh-cache"],
            capture_output=True, text=True, timeout=45,
        )
        log("rituals: dtd cache refreshed")
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"rituals: cache refresh failed: {e}")


def run_block_ritual_cards(hour: int, dry_run: bool = False,
                           live: dict | None = None) -> None:
    """-1neon card lifecycle at a 2h fire. Retire the just-ended block's leftover
    cards (manual: delete; auto: close-if-earned per `live`, else delete), then
    create the starting block's full set. Order matters."""
    if HOUR_TO_BRANCH_BLOCK.get(hour):   # 06..22: a block just ended
        delete_block_rituals(dry_run=dry_run, live=live)
    if 4 <= hour <= 20:                   # 04..20: a waking block (卯..亥) starts
        create_block_rituals(dry_run=dry_run)
    # Surface the mutated card set to dtd now, not on the periodic daemon's cycle.
    _refresh_dtd_cache(dry_run=dry_run)


def run_lock_and_mark(dry_run=False, force_hour=None):
    """Score the just-ended block based on sub-habit emojis, write to -1₦ (P)."""
    now = dt.datetime.now()
    hour = force_hour if force_hour is not None else now.hour
    today = now.date()

    if hour not in BLOCK_FIRE_HOURS:
        log(f"lock-and-mark: hour {hour} is not a fire time — nothing to do")
        return

    # Heal Syncthing-conflict stamp losses BEFORE reconciling: Straylight
    # stamps rituals at completion time while this daemon rewrites the same
    # file, and the race's loser survives only as a LOCAL .sync-conflict
    # copy. reconcile_p_for_day SETs 0分!P from the header stamps, so an
    # unhealed loss becomes a permanent points loss (2026-07-29: 辰 scored
    # 7/13 — 🎯/✅ lived only in the conflict copy). Union-merge is safe:
    # the validation/strip pass below still removes stamps that don't hold.
    if not dry_run:
        try:
            import build_order_heal
            healed = build_order_heal.heal(BUILD_ORDER)
            for m in healed.get("merged", []):
                if m["added"]:
                    log(f"lock-and-mark: healed conflict stamps {m['added']} from {m['file']}")
        except Exception as e:  # noqa: BLE001 — healing must never block the fire
            log(f"lock-and-mark: heal skipped: {e}")

    block_name = HOUR_TO_BRANCH_BLOCK.get(hour)
    log(f"lock-and-mark: hour={hour:02d}, block={block_name}")

    lock_col = LOCK_AT_FIRE_HOUR.get(hour)
    if lock_col:
        neon_lock_cell_with_retry(today, lock_col, dry_run=dry_run)
    # Backstop: lock any earlier block whose own fire's lock failed all
    # retries (a still-live residual corrupts every later block's split).
    self_heal_unlocked_blocks(today, hour, dry_run=dry_run)

    # Phase 1: evaluate daemon-checkable habits and write emojis
    live = None
    if block_name:
        live = evaluate_and_mark_block(block_name, hour, today, dry_run=dry_run)

        # Phase 2: score from header emojis, validated against this run's live
        # results so stale markers from prior days don't award phantom points
        score = score_block_from_emojis(block_name, live=live)
        log(f"lock-and-mark: {block_name} scored {score}/13")

        # Phase 3: reconcile -1₦ (P) to the validated total of ALL fired blocks.
        # SET (not append) so it's idempotent and self-heals late markers (e.g. a
        # prayer logged after its block's boundary) that the old per-block append
        # dropped forever — the cause of P (53) drifting below the header (79).
        reconcile_p_for_day(today, hour, current_live=live, dry_run=dry_run)
        annotate_block_fired(hour, dry_run=dry_run)

        # Phase 4: notify with score breakdown
        notify_block_score(block_name, score, dry_run=dry_run, live=live)
    else:
        log(f"lock-and-mark: hour={hour:02d} has no block to score (day start)")

    # Toggl tag/project aggregation (same 2h cadence)
    run_toggl_sync(dry_run=dry_run)
    run_toggl_point_sync(dry_run=dry_run)

    # -1neon card lifecycle: retire the just-ended block's leftover cards
    # (auto -1t/-1l close-if-earned per `live`) and spawn the new block's full
    # set (😈-marked). Done after scoring so the just-ended block's reconcile
    # reads its emojis before its cards are retired.
    run_block_ritual_cards(hour, dry_run=dry_run, live=live)


# Countdown: notify for next N fires then disable
NOTIFY_REMAINING_FILE = Path.home() / ".cache" / "build-order-notify-remaining"


def notify_block_score(block_name: str, score: int, dry_run: bool = False,
                       live: dict | None = None) -> None:
    """Send macOS notification + email with block score breakdown. Auto-disables after 20 fires."""
    # Check remaining count
    remaining = 0
    if NOTIFY_REMAINING_FILE.exists():
        try:
            remaining = int(NOTIFY_REMAINING_FILE.read_text().strip())
        except (ValueError, OSError):
            remaining = 0
    if remaining <= 0:
        return

    # Build breakdown from emojis on block header
    earned = []
    missed = []
    if not BUILD_ORDER.exists():
        return
    text = BUILD_ORDER.read_text(encoding="utf-8")
    if "## -1₲" in text:
        section = text[text.index("## -1₲"):]
        for line in section.split("\n"):
            if line.startswith("## ") and line != "## -1₲":
                break
            if line.startswith("- ") and not line.startswith("    "):
                name = line.strip().lstrip("- ").strip()
                for m in SCORE_EMOJI_MAP:
                    name = name.replace(m, "")
                name = re.sub(r"\s*\(\d+min\)\s*$", "", name).strip()
                if name == block_name:
                    for emoji, pts in SCORE_EMOJI_MAP.items():
                        if _marker_earned(emoji, line, live):
                            earned.append(f"{emoji}+{pts}")
                        else:
                            missed.append(f"{emoji}+{pts}")
                    break

    summary = f"{block_name} {score}/13: ✓{' '.join(earned)} | ✗{' '.join(missed)}"
    log(f"notify: {summary} (remaining={remaining - 1})")

    if dry_run:
        log(f"[DRY RUN] Would notify: {summary}")
    else:
        # macOS notification
        _osascript(
            f'display notification "{summary}" with title "-1₦ Block Score"'
        )
        # Email
        send_rating_email(
            dt.date.today(), f"{score}/13",
            f"-1₦ block score: {summary}\n\nRemaining notifications: {remaining - 1}",
            dry_run=False,
        )
        # Decrement counter
        NOTIFY_REMAINING_FILE.parent.mkdir(parents=True, exist_ok=True)
        NOTIFY_REMAINING_FILE.write_text(str(remaining - 1))


# --- Email rating ---

def _get_smtp_password():
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", SMTP_KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def send_rating_email(archive_date, rating, summary, dry_run=False):
    subject = f"-1₦ {archive_date.strftime('%b %d')}: {rating}"
    body = summary

    if dry_run:
        log(f"[DRY RUN] Would email {EMAIL_TO}: {subject}")
        return

    pw = _get_smtp_password()
    if not pw:
        log(
            f"email: SKIP — no keychain entry '{SMTP_KEYCHAIN_SERVICE}'. "
            f"To enable: security add-generic-password -s {SMTP_KEYCHAIN_SERVICE} "
            f"-a {EMAIL_FROM} -w <GMAIL_APP_PASSWORD>"
        )
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(EMAIL_FROM, pw)
            s.send_message(msg)
        log(f"email: sent to {EMAIL_TO} ({subject})")
    except Exception as e:
        log(f"email: ERROR {e}")


# --- Mode: archive ---

UNCHECKED_ITEM_RE = re.compile(r"^\s{4}-\s\[\s\]\s*(.*)$")


def run_archive(dry_run=False):
    # At 03:59 local, `today` is the NEW day; we're archiving what was "yesterday".
    archive_date = dt.date.today() - dt.timedelta(days=1)

    if not BUILD_ORDER.exists():
        log("archive: ERROR build order not found — aborting")
        return

    # --- Step 0a: enrich build order with time entries, completed tasks ---
    # build-order-enrich.py populates time entries and completed tasks into
    # each 地支 block. Must run before archive so the snapshot is complete.
    if not dry_run:
        enrich_script = Path.home() / "i446-monorepo" / "scripts" / "build-order-enrich.py"
        if enrich_script.exists():
            try:
                subprocess.run([sys.executable, str(enrich_script)], check=True, timeout=60)
                log("archive: enriched build order")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                log(f"archive: WARN enrich failed: {e}")

    # --- Step 0b: ensure yesterday's d357 links are inline on time entries ---
    # link-meetings normally runs against today; here we run a final pass for
    # the archive date so the snapshot has every meeting linked to its time
    # entry (or floated under its 地支 block if no match within ±7 min).
    run_link_meetings(dry_run=dry_run, target_date=archive_date)

    # --- Step 1: write archive ---
    content = BUILD_ORDER.read_text(encoding="utf-8")
    # Strip the original frontmatter so the archive doesn't have two stacked
    # `---` blocks (which Obsidian parses as a second frontmatter and hides
    # the body content below it).
    content = re.sub(r'^---\n.*?\n---\n', '', content, count=1, flags=re.DOTALL)
    archive_dir = ARCHIVE_ROOT / str(archive_date.year) / archive_date.strftime("%Y.%m.%d")
    archive_file = archive_dir / "build-order.md"

    header = (
        "---\n"
        f"title: \"Build Order — {archive_date}\"\n"
        f"date: {archive_date}\n"
        "type: build-order-archive\n"
        "tags: [g245, archive]\n"
        "source: build-order-daemon\n"
        "---\n\n"
        f"# Build Order — {archive_date.strftime('%A, %B %d, %Y')}\n\n"
        f"Archived {dt.datetime.now().astimezone().isoformat(timespec='seconds')}.\n\n"
        "---\n\n"
    )

    if dry_run:
        log(f"[DRY RUN] Would write archive: {archive_file}")
    else:
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_file.write_text(header + content, encoding="utf-8")
        log(f"archive: wrote {archive_file}")

    # --- Step 2: defer up to 5 unchecked -1₲ items to 以后的目标 ---
    defer_result = defer_unchecked_neg1(dry_run=dry_run)

    # --- Step 3: count meetings (for the email summary) ---
    meetings = find_meetings_for_date(archive_date)

    # --- Step 4: read -1₦ (col P) for the rating ---
    if dry_run:
        rating = "(dry-run)"
    else:
        rating = neon_read_y(archive_date)

    # --- Step 6: wipe -1₲ via existing -1g-cron daily-reset (idempotent with 04:00 plist) ---
    if dry_run:
        log("[DRY RUN] Would run -1g-cron.py daily-reset")
    else:
        try:
            subprocess.run(
                [sys.executable, str(RESET_SCRIPT), "daily-reset"], check=True,
            )
            log("archive: daily-reset complete")
        except subprocess.CalledProcessError as e:
            log(f"archive: ERROR daily-reset failed: {e}")

    # --- Step 7: git-commit vault changes (prevents partial-state races with autopush) ---
    git_commit_archive(archive_date, defer_result, dry_run=dry_run)

    # --- Step 8: send rating email ---
    summary_lines = [
        f"# Build Order — {archive_date.strftime('%A, %B %d, %Y')}",
        "",
        f"Rating (-1₦): **{rating}**",
        "",
        f"- Meetings logged (d357): {len(meetings)}",
        f"- Deferred to 以后的目标: {defer_result['deferred']}",
        f"- Dropped: {defer_result['dropped']}",
        "",
        f"Archive: {archive_file}",
    ]
    if meetings:
        summary_lines.append("")
        summary_lines.append("## Meetings")
        for hour, link in meetings:
            summary_lines.append(f"- {hour:02d}:00 — {link}")
    send_rating_email(
        archive_date=archive_date,
        rating=rating,
        summary="\n".join(summary_lines),
        dry_run=dry_run,
    )


def git_commit_archive(archive_date, defer_result, dry_run=False):
    """Stage and commit archive + build-order changes in the vault repo.
    No-op (no failure) if there's nothing to commit. Doesn't push — vault-autopush handles that."""
    msg = (
        f"build-order daemon: archive {archive_date}, "
        f"defer {defer_result['deferred']}, drop {defer_result['dropped']}, wipe -1₲"
    )
    paths = [
        str(ARCHIVE_ROOT.relative_to(VAULT)),
        str(BUILD_ORDER.relative_to(VAULT)),
    ]
    if dry_run:
        log(f"[DRY RUN] Would git add {paths} && git commit -m '{msg}'")
        return
    try:
        subprocess.run(["git", "-C", str(VAULT), "add", "--", *paths],
                       check=True, capture_output=True, text=True)
        # Check if there's anything staged
        diff = subprocess.run(
            ["git", "-C", str(VAULT), "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if diff.returncode == 0:
            log("git: nothing to commit")
            return
        subprocess.run(
            ["git", "-C", str(VAULT), "commit", "-m", msg],
            check=True, capture_output=True, text=True,
        )
        log(f"git: committed ({msg})")
    except subprocess.CalledProcessError as e:
        log(f"git: ERROR {e.stderr.strip() if e.stderr else e}")


def defer_unchecked_neg1(dry_run=False):
    """Returns dict {deferred: int, dropped: int}."""
    with nb.build_order_lock():
        lines = load_lines()
        start, end = find_neg1_section(lines)
        if start < 0:
            log("defer: no -1₲ section")
            return {"deferred": 0, "dropped": 0}

        unchecked = []
        for i in range(start + 1, end):
            m = UNCHECKED_ITEM_RE.match(lines[i])
            if m and m.group(1).strip():
                unchecked.append((i, m.group(1).strip()))

        if not unchecked:
            log("defer: no unchecked items")
            return {"deferred": 0, "dropped": 0}

        keep = unchecked[:MAX_DEFERRED]
        dropped = unchecked[MAX_DEFERRED:]

        def fmt(text: str) -> str:
            return text if text.startswith("- [ ]") else f"- [ ] {text}"

        deferred_lines = [fmt(t) for _, t in keep]

        if dry_run:
            log(f"[DRY RUN] Would defer {len(keep)} to 以后的目标:")
            for dl in deferred_lines:
                log(f"  {dl}")
            if dropped:
                log(f"[DRY RUN] Would drop {len(dropped)} item(s)")
            return {"deferred": len(keep), "dropped": len(dropped)}

        later_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("### ") and LATER_HEADING in line:
                later_idx = i
                break

        if later_idx < 0:
            log("defer: WARN no 以后的目标 heading — skipping defer")
            return {"deferred": 0, "dropped": 0}

        lines[later_idx + 1:later_idx + 1] = deferred_lines
        save_lines(lines)
    log(f"defer: moved {len(keep)} to 以后的目标, dropped {len(dropped)}")
    return {"deferred": len(keep), "dropped": len(dropped)}


# --- Mode: toggl-sync (tag/project time aggregation to 0n) ---

TOGGL_API_BASE = "https://api.track.toggl.com/api/v9"

# Tag → 0n column letter
# AZ ("∑xk87") is deliberately absent: it's a live =SUM(AJ:AO) formula
# aggregating the kid/family columns, not a raw tag-total target — a "xk87":
# "AZ" entry here used to clobber that formula with a Toggl-tag-derived total.
TOGGL_TAG_COLS = {"-1": "AV", "-2": "AW", "其他人": "AS", "-3": "AX"}
# Sleep (睡觉) carries the "-3" tag but is tracked separately in column D, so it
# must be excluded from the -3/AX tag total — otherwise AX reads as ~a whole
# night of sleep (regression 2026-06-28: AX=439). Mirrors 0t-fast.SLEEP_PROJECT_ID.
SLEEP_PROJECT_ID = 108358083
# Project ID → 0n column letter
TOGGL_PROJ_COLS = {}


def _load_toggl_key() -> str:
    key = os.environ.get("TOGGL_API_KEY", "")
    if key:
        return key
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        data = json.loads(claude_json.read_text())
        env = data.get("mcpServers", {}).get("toggl_server", {}).get("env", {})
        return env.get("TOGGL_API_KEY", "")
    return ""


def _toggl_get(path: str):
    key = _load_toggl_key()
    if not key:
        raise RuntimeError("No TOGGL_API_KEY found")
    url = f"{TOGGL_API_BASE}{path}"
    creds = base64.b64encode(f"{key}:api_token".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {creds}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _entry_effective_minutes(e: dict, target_date: dt.date, now_ts: dt.datetime):
    """Minutes `e` counts toward `target_date`, or None to skip it entirely
    (duration==0, or a running entry with no start time). Clamps a still-open
    entry's start to target_date's local midnight so a stale/forgotten timer
    from an earlier day can't dump its ENTIRE elapsed time into today's total
    (regression 2026-08-11: a >1-day-old open "fall asleep" timer read as
    AV=1493min — the whole morning showing as asleep). Shared by every
    Toggl-tag aggregator (0n minute totals, 0分 point totals) so this clamp
    only has to be fixed in one place."""
    dur = e.get("duration", 0)
    if dur > 0:
        return dur // 60
    if dur < 0:
        start_str = e.get("start", "")
        if not start_str:
            return None
        start_dt = dt.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        day_start = dt.datetime.combine(
            target_date, dt.time.min, tzinfo=daytime.active_zone())
        effective_start = max(start_dt, day_start)
        return max(0, int((now_ts - effective_start).total_seconds()) // 60)
    return None


def compute_toggl_totals(target_date: dt.date) -> dict[str, int]:
    """Fetch today's Toggl entries, return {column_letter: minutes} for tags and projects."""
    start = target_date.isoformat()
    end = (target_date + dt.timedelta(days=1)).isoformat()
    entries = _toggl_get(f"/me/time_entries?start_date={start}&end_date={end}")

    col_totals: dict[str, int] = {}
    now_ts = dt.datetime.now(dt.timezone.utc)

    for e in entries:
        minutes = _entry_effective_minutes(e, target_date, now_ts)
        if minutes is None:
            continue

        # Sleep is column D, not a tag column — exclude it so its -3 tag never
        # inflates AX (regression 2026-06-28).
        if e.get("project_id") == SLEEP_PROJECT_ID:
            continue

        for tag in (e.get("tags") or []):
            if tag in TOGGL_TAG_COLS:
                col = TOGGL_TAG_COLS[tag]
                col_totals[col] = col_totals.get(col, 0) + minutes

        pid = e.get("project_id")
        if pid in TOGGL_PROJ_COLS:
            col = TOGGL_PROJ_COLS[pid]
            col_totals[col] = col_totals.get(col, 0) + minutes

    return col_totals


# --- Tag -> 0分 POINTS (not 0n minutes), 1pt/min (JM 2026-08-13: "#xk88") ---
# Different write model from TOGGL_TAG_COLS above: those SET an absolute 0n
# minute total each cycle (idempotent by construction — same total every
# time until minutes change). 0分!<col> instead ACCUMULATES via a +N append,
# shared with every other point source for that domain, so blindly
# re-appending the day's full tagged-minute count every 2h cycle would
# double-, triple-, ...-count it. Instead a per-day minute BASELINE is kept
# in TOGGL_POINT_STATE_PATH and only the delta since the last cycle is
# appended -- e.g. cycle 1 sees 12 tagged minutes and appends +12; cycle 2
# sees 20 (12 old + 8 new) and appends only +8, not +20.
TOGGL_POINT_TAG_COLS = {"xk88": "X"}  # tag -> 0分 column, 1pt/min
TOGGL_POINT_STATE_PATH = Path.home() / ".cache" / "jm" / "toggl-point-tags-state.json"


def _load_point_tag_state() -> dict:
    try:
        return json.loads(TOGGL_POINT_STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_point_tag_state(state: dict) -> None:
    TOGGL_POINT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOGGL_POINT_STATE_PATH.write_text(json.dumps(state))


def compute_toggl_point_tag_minutes(target_date: dt.date) -> dict[str, int]:
    """{tag: minutes} for today's entries carrying a TOGGL_POINT_TAG_COLS tag.
    Unlike compute_toggl_totals, this does NOT exclude the sleep project --
    none of the point tags are sleep-adjacent today, and excluding it
    unconditionally would silently misbehave if a future point tag ever were."""
    start = target_date.isoformat()
    end = (target_date + dt.timedelta(days=1)).isoformat()
    entries = _toggl_get(f"/me/time_entries?start_date={start}&end_date={end}")
    now_ts = dt.datetime.now(dt.timezone.utc)
    tag_minutes: dict[str, int] = {}
    for e in entries:
        minutes = _entry_effective_minutes(e, target_date, now_ts)
        if minutes is None:
            continue
        for tag in (e.get("tags") or []):
            if tag in TOGGL_POINT_TAG_COLS:
                tag_minutes[tag] = tag_minutes.get(tag, 0) + minutes
    return tag_minutes


def run_toggl_point_sync(dry_run=False):
    """Append +1pt per net-new tagged minute to 0分 (delta-tracked per day —
    see TOGGL_POINT_TAG_COLS docstring above)."""
    today = dt.date.today()
    today_key = today.isoformat()
    try:
        tag_minutes = compute_toggl_point_tag_minutes(today)
    except Exception as e:
        log(f"toggl-point-sync: ERROR fetching Toggl: {e}")
        return
    if not tag_minutes:
        log("toggl-point-sync: no point-tagged entries for today")
        return

    state = _load_point_tag_state()
    day_state = dict(state.get(today_key, {}))
    changed = False
    for tag, minutes in tag_minutes.items():
        prev = day_state.get(tag, 0)
        delta = minutes - prev
        if delta <= 0:
            continue
        col = TOGGL_POINT_TAG_COLS[tag]
        if dry_run:
            log(f"[DRY RUN] toggl-point-sync: would append +{delta} to 0分!{col} for #{tag}")
            continue
        try:
            result = neon_excel.append(
                "0分", col, date=f"{today.month}/{today.day}", value=f"+{delta}",
                src=f"toggl #{tag} tag (+{delta}m)")
            log(f"toggl-point-sync: #{tag} +{delta}pt -> 0分!{col} ({result})")
            day_state[tag] = minutes
            changed = True
        except Exception as e:
            log(f"toggl-point-sync: #{tag} append FAILED: {e}")
            # Don't update day_state[tag] on failure -- retry the full delta
            # next cycle rather than silently losing it.

    if changed and not dry_run:
        # Only today's entry is ever read, so drop older days rather than
        # letting the state file grow forever.
        state[today_key] = day_state
        _save_point_tag_state({today_key: day_state})


def write_toggl_totals_to_0n(col_totals: dict[str, int], target_date: dt.date,
                              dry_run: bool = False) -> str:
    """Write absolute tag/project minute totals to 0n sheet for target_date."""
    if not col_totals:
        return "nothing to write"

    set_lines = []
    for col, minutes in col_totals.items():
        set_lines.append(
            f'    set value of range ("{col}" & targetRow) of theSheet to {minutes}'
        )
    set_block = "\n".join(set_lines)
    month = target_date.month
    day = target_date.day

    script = f'''tell application "Microsoft Excel"
    set theSheet to sheet "0n" of workbook "Neon分v12.2.xlsx"
    set targetRow to 0
    repeat with r from 3 to 500
        set cellDate to value of cell 3 of row r of theSheet
        if cellDate is not missing value then
            try
                set m to (month of (cellDate as date)) as integer
                set d to day of (cellDate as date)
                if m = {month} and d = {day} then
                    set targetRow to r
                    exit repeat
                end if
            end try
        end if
    end repeat
    if targetRow = 0 then return "ERROR: date {month}/{day} not found"
{set_block}
    return "OK: toggl-sync row=" & targetRow
end tell'''

    if dry_run:
        log(f"[DRY RUN] Would write to 0n for {month}/{day}: {col_totals}")
        return "DRY_RUN"

    try:
        r = _osascript(script)
        out = (r.stdout or "").strip()
        if r.returncode != 0 or out.startswith("ERROR"):
            log(f"toggl-sync write: FAILED {out or r.stderr.strip()}")
            return "FAILED"
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"toggl-sync write: ERROR {e}")
        return "ERROR"


def run_toggl_sync(dry_run=False):
    """Compute and write Toggl tag/project totals for today to 0n."""
    today = dt.date.today()
    try:
        totals = compute_toggl_totals(today)
    except Exception as e:
        log(f"toggl-sync: ERROR fetching Toggl: {e}")
        return
    if not totals:
        log("toggl-sync: no tagged/project entries for today")
        return
    result = write_toggl_totals_to_0n(totals, today, dry_run=dry_run)
    log(f"toggl-sync: {result} — {totals}")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Build-order daemon")
    parser.add_argument("mode", choices=["link-meetings", "lock-and-mark", "archive",
                                         "toggl-sync", "toggl-point-sync", "compute-p"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hour", type=int, default=None,
                        help="(lock-and-mark/compute-p only) override current hour for testing")
    args = parser.parse_args()

    if args.mode == "link-meetings":
        run_link_meetings(dry_run=args.dry_run)
    elif args.mode == "lock-and-mark":
        run_lock_and_mark(dry_run=args.dry_run, force_hour=args.hour)
    elif args.mode == "toggl-sync":
        run_toggl_sync(dry_run=args.dry_run)
    elif args.mode == "toggl-point-sync":
        run_toggl_point_sync(dry_run=args.dry_run)
    elif args.mode == "compute-p":
        # On-demand: print today's -1₦ score from currently-stamped emojis so a
        # caller (did-fast run_ritual) can SET col P immediately. Log lines land
        # on stdout too, so emit a uniquely-prefixed line for robust parsing.
        h = args.hour if args.hour is not None else dt.datetime.now().hour
        cur = _branch_for_hour(h)
        formula, total, _parts = compute_p_formula(dt.date.today(), h, current_block=cur)
        print(f"P_RESULT\t{formula}\t{total}")
    else:
        run_archive(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
