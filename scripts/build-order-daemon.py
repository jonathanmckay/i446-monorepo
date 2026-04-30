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

import argparse
import datetime as dt
import os
import re
import smtplib
import subprocess
import sys
from email.mime.text import MIMEText
from pathlib import Path

# --- Paths ---

VAULT = Path.home() / "vault"
BUILD_ORDER = VAULT / "g245" / "-1₦ , 0₦ - Neon {Build Order}.md"
D357_DIR = VAULT / "d357"  # canonical flat location, filenames YYYY.MM.DD-<kebab>.md
ARCHIVE_ROOT = VAULT / "g245" / "archive"
RESET_SCRIPT = Path.home() / "i446-monorepo" / "scripts" / "-1g-cron.py"

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

# Fire schedule: every 2 hours from 04 to 22. Each fire adds 12 to column P.
# No more per-block marker/points columns; just a single -1₦ total in P.
BLOCK_FIRE_HOURS = {4, 6, 8, 10, 12, 14, 16, 18, 20, 22}

# Map fire-hour → 地支 block in the build order. Used to drop a "fired" emoji on
# the block header so the user can see at a glance which blocks the daemon hit.
# 04 has no matching build-order block (build order starts at 卯=06).
HOUR_TO_BRANCH_BLOCK = {
    6:  "卯",
    8:  "辰",
    10: "巳",
    12: "午",
    14: "未",
    16: "申",
    18: "酉",
    20: "戌",
    22: "亥",
}
DAEMON_FIRED_EMOJI = "⏰"

# At each fire hour, freeze the just-ended block's column in 0分 from formula
# (`=D-SUM(prior blocks)`) to literal value, so the next block's residual
# formula starts measuring fresh. 04 and 06 have no prior block to lock today.
LOCK_AT_FIRE_HOUR = {
    8:  "G",  # 卯 ended at 08
    10: "H",  # 辰 ended at 10
    12: "I",  # 巳 ended at 12
    14: "J",  # 午 ended at 14
    16: "K",  # 未 ended at 16
    18: "L",  # 申 ended at 18
    20: "M",  # 酉 ended at 20
    22: "N",  # 戌 ended at 22
}

# 地支 block → hour range (inclusive). Hours outside 06-23 clamp to nearest.
BRANCH_HOURS = [
    ("卯", 6, 7),   # 卯
    ("辰", 8, 9),   # 辰
    ("巳", 10, 11), # 巳
    ("午", 12, 13), # 午
    ("未", 14, 15), # 未
    ("申", 16, 17), # 申
    ("酉", 18, 19), # 酉
    ("戌", 20, 21), # 戌
    ("亥", 22, 23), # 亥
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
    if hour < 6:
        return BRANCH_HOURS[0][0]  # 卯
    if hour > 23:
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
    for path in sorted(D357_DIR.glob(f"{prefix}-*.md")):
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
    Tolerates trailing emoji/text after the branch char (e.g. `- 卯 ⏰`)."""
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
    set theSheet to sheet "{sheet}" of active workbook
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
    No-op if the cell is not currently a formula. Returns status string."""
    body = (
        f'set theCell to cell ("{col}" & targetRow) of theSheet\n'
        '    set f to formula of theCell\n'
        '    if f is "" then return "EMPTY"\n'
        '    if (character 1 of f) is not "=" then return "ALREADY_LOCKED " & f\n'
        '    set v to value of theCell\n'
        '    set value of theCell to v\n'
        '    return "LOCKED " & v as text\n'
    )
    script = NEON_FIND_ROW_TEMPLATE.format(
        sheet=NEON_SHEET, date_str=_date_str(target_date),
        date_col=NEON_DATE_COL, body=body,
    )
    if dry_run:
        log(f"[DRY RUN] Would lock {NEON_SHEET}!{col} for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = _osascript(script)
        out = (r.stdout or "").strip()
        if r.returncode != 0 or out.startswith("ERROR"):
            log(f"lock {col}: FAILED {out or r.stderr.strip()}")
            return "FAILED"
        log(f"lock {col}: {out}")
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"lock {col}: ERROR {e}")
        return "ERROR"


def neon_set_marker(target_date: dt.date, col: str, dry_run: bool = False) -> str:
    """Set `col` cell for target_date to 1, but only if currently empty.
    Returns status string."""
    body = (
        f'set theCell to cell ("{col}" & targetRow) of theSheet\n'
        '    set v to value of theCell\n'
        '    if v is missing value or v is "" then\n'
        '        set value of theCell to 1\n'
        '        return "SET"\n'
        '    else\n'
        '        return "ALREADY " & (v as text)\n'
        '    end if\n'
    )
    script = NEON_FIND_ROW_TEMPLATE.format(
        sheet=NEON_SHEET, date_str=_date_str(target_date),
        date_col=NEON_DATE_COL, body=body,
    )
    if dry_run:
        log(f"[DRY RUN] Would mark {NEON_SHEET}!{col} = 1 for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = _osascript(script)
        out = (r.stdout or "").strip()
        if r.returncode != 0 or out.startswith("ERROR"):
            log(f"mark {col}: FAILED {out or r.stderr.strip()}")
            return "FAILED"
        log(f"mark {col}: {out}")
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"mark {col}: ERROR {e}")
        return "ERROR"


def neon_add_12_to_y(target_date: dt.date, dry_run: bool = False) -> str:
    """Add 12 to column Y for target_date's row. If empty, set to 12."""
    body = (
        f'set yCell to range ("{NEON_NEG1_COL}" & targetRow) of theSheet\n'
        '    set oldVal to value of yCell\n'
        '    if oldVal is missing value or (oldVal as text) = "" or (oldVal as text) = "0" then\n'
        '        set value of yCell to 12\n'
        '        return "Y_SET 12"\n'
        '    else\n'
        '        set newVal to (oldVal as number) + 12\n'
        '        set value of yCell to newVal\n'
        '        return "Y_ADD " & (oldVal as text) & " + 12 = " & (newVal as text)\n'
        '    end if\n'
    )
    script = NEON_FIND_ROW_TEMPLATE.format(
        sheet=NEON_SHEET, date_str=_date_str(target_date),
        date_col=NEON_DATE_COL, body=body,
    )
    if dry_run:
        log(f"[DRY RUN] Would add 12 to Y for {_date_str(target_date)}")
        return "DRY_RUN"
    try:
        r = _osascript(script)
        out = (r.stdout or "").strip()
        if r.returncode != 0 or out.startswith("ERROR"):
            log(f"add_12_to_y: FAILED {out or r.stderr.strip()}")
            return "FAILED"
        log(f"add_12_to_y: {out}")
        # Verify write landed by reading back
        verify = neon_read_y(target_date)
        if verify == "ERROR" or verify == "" or verify == "0":
            log(f"add_12_to_y: VERIFY FAILED — wrote but read back {verify}. Excel may not be open.")
            return "VERIFY_FAILED"
        log(f"add_12_to_y: verified={verify}")
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log(f"ensure Y: ERROR {e}")
        return "ERROR"


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


# --- Mode: lock-and-mark ---

def annotate_block_fired(hour: int, dry_run: bool = False) -> None:
    """Append the fired emoji to the matching 地支 block header in the build order.
    Idempotent — skips if the emoji is already on that header line."""
    branch = HOUR_TO_BRANCH_BLOCK.get(hour)
    if branch is None:
        return
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


def run_lock_and_mark(dry_run=False, force_hour=None):
    """Add 12 to -1₦ column (P) at each 2-hour boundary."""
    now = dt.datetime.now()
    hour = force_hour if force_hour is not None else now.hour
    today = now.date()

    if hour not in BLOCK_FIRE_HOURS:
        log(f"lock-and-mark: hour {hour} is not a fire time — nothing to do")
        return

    log(f"lock-and-mark: hour={hour:02d} → +12 to {NEON_NEG1_COL}")

    lock_col = LOCK_AT_FIRE_HOUR.get(hour)
    if lock_col:
        neon_lock_cell(today, lock_col, dry_run=dry_run)

    neon_add_12_to_y(today, dry_run=dry_run)
    annotate_block_fired(hour, dry_run=dry_run)


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


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Build-order daemon")
    parser.add_argument("mode", choices=["link-meetings", "lock-and-mark", "archive"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hour", type=int, default=None,
                        help="(lock-and-mark only) override current hour for testing")
    args = parser.parse_args()

    if args.mode == "link-meetings":
        run_link_meetings(dry_run=args.dry_run)
    elif args.mode == "lock-and-mark":
        run_lock_and_mark(dry_run=args.dry_run, force_hour=args.hour)
    else:
        run_archive(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
