#!/usr/bin/env python3
"""
-2n — Unified interrupt queue.
Runs pre-inbox cards (salah, -1l, -1g, meeting prep), then hands off to ibx0.

Card order:
  1. صلاة الشمس (salah check)
  2. -1l (daily ritual check)
  3. -1g (set 2h block goals)
  3.5. meeting prep (staged briefs)
  4. ibx0 (inbox cards — delegates to ibx0.main())
  5. suggest starting on goals
"""

import json
import os
import re
import readline  # enables line editing (backspace, arrows) in input()
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests as _requests
except ImportError:
    _requests = None

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

sys.path.insert(0, os.path.dirname(__file__))

console = Console()

TERM_COLOR = Path(__file__).parent.parent.parent / "scripts" / "term-color.sh"
BUILD_ORDER = Path.home() / "vault/g245/-1₦ , 0₦ - Neon {Build Order}.md"
MTG_BRIEFS = Path.home() / "vault/z_ibx/mtg-briefs.json"
TZ = "America/Los_Angeles"

BLOCKS = [
    ("卯", "04:00", "05:59"),
    ("辰", "06:00", "07:59"),
    ("巳", "08:00", "09:59"),
    ("午", "10:00", "11:59"),
    ("未", "12:00", "13:59"),
    ("申", "14:00", "15:59"),
    ("酉", "16:00", "17:59"),
    ("戌", "18:00", "19:59"),
    ("亥", "20:00", "21:59"),
]


def set_term_color(color):
    if TERM_COLOR.exists():
        subprocess.run(["bash", str(TERM_COLOR), color], capture_output=True)


def get_current_block():
    """Return (index, arabic_name, start_time, end_time) for current 2h block."""
    now = datetime.now()
    hour = now.hour
    idx = max(0, min(8, (hour - 4) // 2))
    name, start, end = BLOCKS[idx]
    return idx, name, start, end


def check_neon_column(col_name):
    """Check if a 0₦ column has a value for today. Returns the value or None."""
    script = f'''
    tell application "Microsoft Excel"
        set wb to workbook "Neon分v12.2.xlsx"
        set theSheet to sheet "0n" of wb
        set targetMonth to {datetime.now().month}
        set targetDay to {datetime.now().day}
        set habitCol to 0
        repeat with c from 1 to 60
            set cellVal to value of cell c of row 1 of theSheet
            if cellVal is not missing value then
                set trimmed to do shell script "printf '%s' " & quoted form of (cellVal as text) & " | sed 's/[[:space:]]*$//'"
                if trimmed = "{col_name}" then
                    set habitCol to c
                    exit repeat
                end if
            end if
        end repeat
        if habitCol = 0 then return "NOT_FOUND"
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
        if todayRow = 0 then return "NO_ROW"
        set v to value of cell habitCol of row todayRow of theSheet
        if v is missing value then return ""
        return v as text
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15
        )
        val = result.stdout.strip()
        if val in ("", "0", "0.0", "NOT_FOUND", "NO_ROW"):
            return None
        return val
    except Exception:
        return None


PRAYER_MARKER = "☀️"
INBOX_MARKER = "📧"
TIME_MARKER = "⏰"


def _block_name_from_header(line: str) -> str:
    """Extract the clean 地支 block name from a header line, stripping any
    trailing markers (e.g. '- 申 ☀️ 📧 ⏰ (134min)' → '申')."""
    name = line.strip().lstrip("- ").strip()
    for marker in (PRAYER_MARKER, INBOX_MARKER, TIME_MARKER):
        if marker in name:
            name = name.replace(marker, "").strip()
    # Strip duration suffix like (134min)
    name = re.sub(r"\s*\(\d+min\)\s*$", "", name)
    return name


def read_block_goals():
    """Read -1₲ section from build order. Returns {block_name: [goals]} (open only)."""
    status = read_block_goals_with_status()
    return {block: [g for g, done in items if not done] for block, items in status.items()}


def read_block_goals_with_status():
    """Read -1₲ section, returning {block_name: [(text, done_bool), ...]}.

    Includes both `- [ ]` and `- [x]` checkbox lines."""
    if not BUILD_ORDER.exists():
        return {}
    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        return {}
    section = text[text.index("## -1₲"):]
    blocks = {}
    current_block = None
    for line in section.split("\n"):
        if line.startswith("## ") and current_block is not None:
            break
        if line.startswith("- ") and not line.startswith("    "):
            current_block = _block_name_from_header(line)
            blocks[current_block] = []
        elif current_block:
            m = re.match(r"^    - \[([ xX])\]\s*(.*)$", line)
            if m:
                done = m.group(1).lower() == "x"
                text_part = m.group(2).strip()
                if not text_part:
                    continue
                blocks[current_block].append((text_part, done))
    return blocks


def has_prayer_marker(block_name):
    """Check if the block header has a ☀️ prayer marker (e.g. '- 申 ☀️')."""
    if not BUILD_ORDER.exists():
        return False
    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        return False
    section = text[text.index("## -1₲"):]
    for line in section.split("\n"):
        if line.startswith("- ") and not line.startswith("    "):
            if _block_name_from_header(line) == block_name and PRAYER_MARKER in line:
                return True
    return False


def write_prayer_marker(block_name):
    """Append a ☀️ marker to the block header line (e.g. '- 申' → '- 申 ☀️').
    Idempotent: skips if the marker is already present."""
    if not BUILD_ORDER.exists():
        return
    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        return
    if has_prayer_marker(block_name):
        return
    lines = text.split("\n")
    new_lines = []
    appended = False
    for line in lines:
        if (not appended
                and line.startswith("- ")
                and not line.startswith("    ")
                and _block_name_from_header(line) == block_name):
            new_lines.append(f"{line.rstrip()} {PRAYER_MARKER}")
            appended = True
        else:
            new_lines.append(line)
    BUILD_ORDER.write_text("\n".join(new_lines))


def write_inbox_marker(block_name):
    """Append a 📧 marker to the block header line. Idempotent."""
    if not BUILD_ORDER.exists():
        return
    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        return
    if INBOX_MARKER in text[text.index("## -1₲"):]:
        # Check if this specific block already has it
        for line in text[text.index("## -1₲"):].split("\n"):
            if (line.startswith("- ") and not line.startswith("    ")
                    and _block_name_from_header(line) == block_name
                    and INBOX_MARKER in line):
                return
    lines = text.split("\n")
    new_lines = []
    appended = False
    for line in lines:
        if (not appended
                and line.startswith("- ")
                and not line.startswith("    ")
                and _block_name_from_header(line) == block_name):
            new_lines.append(f"{line.rstrip()} {INBOX_MARKER}")
            appended = True
        else:
            new_lines.append(line)
    if appended:
        BUILD_ORDER.write_text("\n".join(new_lines))


def clear_prayer_markers():
    """Strip all block header emojis (☀️📧⏰🎯⏱️✅) from -1₲. Called during daily wipe."""
    if not BUILD_ORDER.exists():
        return
    text = BUILD_ORDER.read_text()
    all_markers = (PRAYER_MARKER, INBOX_MARKER, TIME_MARKER, "🎯", "⏱️", "✅")
    if not any(m in text for m in all_markers):
        return
    lines = text.split("\n")
    new_lines = []
    in_section = False
    for line in lines:
        if line.startswith("## -1₲"):
            in_section = True
            new_lines.append(line)
            continue
        if in_section and line.startswith("## "):
            in_section = False
        if (in_section
                and line.startswith("- ")
                and not line.startswith("    ")
                and any(m in line for m in all_markers)):
            cleaned = line
            for m in all_markers:
                cleaned = cleaned.replace(m, "")
            new_lines.append(cleaned.rstrip())
        else:
            new_lines.append(line)
    BUILD_ORDER.write_text("\n".join(new_lines))


def render_block_status_panel(block_name=None, stats_line=None):
    """Build a Rich Panel summarizing the current 2h block's -1g status.

    Done goals appear struck-through. The ☀️ marker prefixes the title when
    the block's prayer is logged. Used by /inbound on the inbox-zero idle
    screen so the user can keep their goals in view.

    stats_line: optional Rich markup string appended below goals (e.g. response stats)."""
    from rich.align import Align
    if block_name is None:
        _, block_name, _, _ = get_current_block()
    block_goals = read_block_goals_with_status()
    items = block_goals.get(block_name, [])
    prayer_done = has_prayer_marker(block_name)
    prefix = f"{PRAYER_MARKER} " if prayer_done else ""
    if not items:
        body = "[dim](no goals set)[/dim]"
    else:
        lines = []
        for text, done in items:
            safe = text.replace("[", r"\[")
            if done:
                lines.append(f"[dim strike]☑ {safe}[/dim strike]")
            else:
                lines.append(f"☐ {safe}")
        body = "\n".join(lines)
    if stats_line:
        body += f"\n\n{stats_line}"
    panel = Panel(
        body,
        title=f"[bold]{prefix}-1₲ · {block_name}[/bold]",
        border_style="cyan",
        padding=(1, 2),
    )
    return Align.center(panel)


def prompt_card(card_num, total, title, body, options="y/skip", preserve_case=False, multiline=False):
    """Display a card and wait for user input. Returns the user's response.

    If multiline=True, reads lines until an empty line (or EOF) and joins with
    newlines. The first empty line submits, so users can type one goal per
    line and hit Enter twice to send."""
    set_term_color("red")
    panel = Panel(
        body,
        title=f"[bold]Card {card_num}/{total}: {title}[/bold]",
        border_style="red",
        padding=(1, 2),
    )
    console.print(panel)
    try:
        if multiline:
            console.print(f"[dim](empty line to submit)[/dim]")
            prompt = f"[bold red]({options})>[/bold red] "
            lines = []
            while True:
                try:
                    line = console.input(prompt) if not lines else console.input("[bold red]>[/bold red] ")
                except EOFError:
                    break
                if line.strip() == "":
                    break
                lines.append(line)
            raw = "\n".join(lines).strip()
        else:
            raw = console.input(f"[bold red]({options})>[/bold red] ").strip()
        response = raw if preserve_case else raw.lower()
    except (KeyboardInterrupt, EOFError):
        response = "skip"
    set_term_color("black")
    return response


def run_did(habit):
    """Run /did for a habit via claude CLI."""
    subprocess.run(
        ["claude", "-p", f"/did {habit}", "--allowedTools",
         "Skill,Bash,Read,Edit,Write,mcp__todoist__complete-tasks,mcp__todoist__find-tasks"],
        capture_output=True, timeout=120
    )


def parse_goals_text(goals_text: str) -> list[str]:
    """Split user-typed goals into a list. Accepts comma-separated, newline-
    separated, or bullet-prefixed input. Strips checkbox syntax."""
    if not goals_text:
        return []
    # Split on newlines first, then commas inside each line.
    raw_items = []
    for line in goals_text.splitlines():
        # Comma-split only when there's no bullet structure on this line.
        if line.strip().startswith(("-", "*")) or re.match(r"^\s*\d+[.)]\s", line):
            raw_items.append(line)
        else:
            raw_items.extend(line.split(","))
    goals = []
    for item in raw_items:
        g = item.strip()
        # Strip leading bullet markers
        g = re.sub(r"^([-*]|\d+[.)])\s+", "", g)
        # Strip checkbox syntax: "[ ] foo" or "[x] foo"
        g = re.sub(r"^\[[ xX]\]\s*", "", g)
        g = g.strip()
        if g:
            goals.append(g)
    return goals


def write_block_goals(block_name: str, goals: list[str]) -> bool:
    """Write goals to the build order under the given 地支 block. Returns True
    on success. Done locally in Python so that an invisible failure in the
    `claude -p /-1g` subprocess can never silently lose the user's goals."""
    if not goals or not BUILD_ORDER.exists():
        return False
    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        return False
    lines = text.split("\n")
    section_start = None
    for i, line in enumerate(lines):
        if line.strip() == "## -1₲":
            section_start = i
            break
    if section_start is None:
        return False

    # Find the target block header within the -1₲ section.
    target_idx = None
    section_end = len(lines)
    for i in range(section_start + 1, len(lines)):
        line = lines[i]
        # Next "## " heading ends the section.
        if line.startswith("## ") and i > section_start:
            section_end = i
            break
        if line.startswith("- ") and not line.startswith("    "):
            block = _block_name_from_header(line)
            if block == block_name:
                target_idx = i
                break

    if target_idx is None:
        return False

    # Find the end of this block's indented children (next non-indented line
    # or the end of section).
    block_end = section_end
    for j in range(target_idx + 1, section_end):
        if not lines[j].startswith("    ") and lines[j].strip() != "":
            block_end = j
            break
        if lines[j].startswith("- ") and not lines[j].startswith("    "):
            block_end = j
            break

    # Preserve any non-checkbox indented children (e.g. wikilinks). Prayer
    # marker now lives on the block header line, so no special handling
    # needed here — we just replace existing checkbox lines with new goals.
    preserved_other = [
        lines[k] for k in range(target_idx + 1, block_end)
        if lines[k].startswith("    ") and not re.match(r"^    - \[[ xX]\]", lines[k])
    ]
    new_block = [f"    - [ ] {g}" for g in goals] + preserved_other

    new_lines = lines[: target_idx + 1] + new_block + lines[block_end:]
    BUILD_ORDER.write_text("\n".join(new_lines))
    return True


def run_1g(goals_text):
    """Run /-1g via claude CLI synchronously (legacy; kept for callers/tests).

    The /inbound -1g card uses `spawn_1g_background` instead so the user is
    not blocked while claude syncs Todoist."""
    subprocess.run(
        ["claude", "-p", f"/-1g {goals_text}", "--allowedTools",
         "Skill,Bash,Read,Edit,Write,mcp__todoist__add-tasks"],
        capture_output=True, timeout=120
    )


def spawn_1g_background(goals_text):
    """Fire-and-forget: spawn `claude -p /-1g …` as a detached subprocess.

    The local write to the build order is already done by the -1g card before
    this is called, so this background job's only remaining responsibility is
    to create matching Todoist tasks. We use start_new_session=True so the
    child survives parent exit (the /inbound TUI may finish before claude
    does).

    stdout/stderr go to ~/.cache/inbound/1g-<unix-ts>.log for post-hoc debugging.
    Returns the Popen handle (caller may ignore)."""
    log_dir = Path.home() / ".cache" / "inbound"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"1g-{int(time.time())}.log"
    log_fh = open(log_path, "wb")
    return subprocess.Popen(
        ["claude", "-p", f"/-1g {goals_text}", "--allowedTools",
         "Skill,Bash,Read,Edit,Write,mcp__todoist__add-tasks"],
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
        close_fds=True,
    )


def _prune_stale_briefs(briefs, now=None, grace_hours=4):
    """Drop briefs whose meeting started more than `grace_hours` ago and
    persist the trimmed list. Returns the kept briefs."""
    if not briefs:
        return briefs
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=grace_hours)).isoformat()
    kept = []
    for b in briefs:
        start = b.get("start", "")
        # ISO-8601 with timezone sorts lexicographically when normalized;
        # keep brief if its start is on/after the cutoff.
        try:
            start_dt = datetime.fromisoformat(start)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if start_dt >= now - timedelta(hours=grace_hours):
                kept.append(b)
        except (ValueError, TypeError):
            # Malformed start — keep so we don't silently lose data.
            kept.append(b)
    if len(kept) != len(briefs):
        try:
            if kept:
                MTG_BRIEFS.write_text(json.dumps(kept, indent=2, ensure_ascii=False))
            else:
                MTG_BRIEFS.unlink(missing_ok=True)
        except Exception:
            pass
    return kept


def get_previous_block():
    """Return (index, name, start_time, end_time) for the previous 2h block."""
    idx, _, _, _ = get_current_block()
    prev_idx = max(0, idx - 1)
    name, start, end = BLOCKS[prev_idx]
    return prev_idx, name, start, end


def check_time_gaps(block_start, block_end, date_str=None):
    """Find gaps >5min in Toggl entries for a given time window.

    Returns list of (gap_start_HHMM, gap_end_HHMM) tuples.
    """
    cli = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
    cmd = ["python3", str(cli), "today"]
    if date_str:
        cmd = ["python3", str(cli), "date", date_str]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        lines = result.stdout.strip().split("\n")
    except Exception:
        return []

    # Parse entries: "HH:MM-HH:MM description @project (Nmin) [id:X]"
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Match "HH:MM-HH:MM" or "HH:MM-running" at start
        import re
        m = re.match(r'(\d{2}:\d{2})-(\d{2}:\d{2}|running)', line)
        if not m:
            continue
        entry_start = m.group(1)
        entry_end = m.group(2)
        if entry_end == "running":
            entry_end = datetime.now().strftime("%H:%M")
        entries.append((entry_start, entry_end))

    if not entries:
        return []

    # Filter to entries overlapping the block window
    def to_min(hhmm):
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    block_s = to_min(block_start)
    block_e = to_min(block_end) + 1  # inclusive end (e.g. 17:59 means up to 18:00)

    # Clip entries to block window and sort
    clipped = []
    for es, ee in entries:
        es_m, ee_m = to_min(es), to_min(ee)
        if ee_m <= block_s or es_m >= block_e:
            continue  # outside window
        clipped.append((max(es_m, block_s), min(ee_m, block_e)))
    clipped.sort()

    # Merge overlapping entries
    merged = []
    for s, e in clipped:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Find gaps >5min
    gaps = []
    prev_end = block_s
    for s, e in merged:
        if s - prev_end > 5:
            gap_start = f"{prev_end // 60:02d}:{prev_end % 60:02d}"
            gap_end = f"{s // 60:02d}:{s % 60:02d}"
            gaps.append((gap_start, gap_end))
        prev_end = e
    # Gap at the end of the block
    if block_e - prev_end > 5:
        gap_start = f"{prev_end // 60:02d}:{prev_end % 60:02d}"
        gap_end = f"{block_e // 60:02d}:{block_e % 60:02d}"
        gaps.append((gap_start, gap_end))

    return gaps


# ── /tg shortcode → project mapping (subset for gap fills) ───────────────
_GAP_PROJECT_MAP = {
    "wake up": "infra", "get up": "infra", "bio": "infra", "shower": "hci",
    "hci": "hci", "day hci": "hci", "day": "hci",
    "epcn": "epcn", "coffee": "epcn",
    "breakfast": "hcb", "h breakfast": "hcb", "lunch": "hcb", "snack": "hcb",
    "hiit": "hcbp", "bball": "hcbp",
    "work": "i9", "tasks": "i9", "teams": "i9", "meetings": "i9",
    "startup": "i9", "standup": "i9",
    "0l": "g245", "0g": "g245", "-1l": "g245", "-1g": "g245", "1s": "g245",
    "family time": "xk87", "math": "xk87", "read": "xk87",
    "ibx": "m5x2", "kn47 daily": "m5x2",
    "新闻": "hcmc", "news": "hcmc",
    "冥想": "hcm", "o314": "hcm", "الفاتحة": "hcm",
    "out the door": "infra",
}


def fill_time_gaps(response, gaps=None):
    """Parse comma-separated gap fills and create Toggl entries.

    Each segment: 'HHMM-HHMM description [@project]'
    e.g. '900-915 wake up, 915-930 2nd hci, 940-1005 startup @i9'

    If a segment has no time prefix and gaps is provided, the corresponding
    gap's time range is used (1:1 positional match, or the single gap if
    there's only one).
    """
    cli = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
    segments = [s.strip() for s in response.split(",") if s.strip()]
    created = 0
    for i, seg in enumerate(segments):
        # Parse: HHMM-HHMM or H:MM-H:MM then description [@project]
        m = re.match(r'(\d{3,4})-(\d{3,4})\s+(.*)', seg)
        if not m:
            m = re.match(r'(\d{1,2}:\d{2})-(\d{1,2}:\d{2})\s+(.*)', seg)
            if not m:
                # No time prefix; use gap time range if available
                if gaps:
                    gap = gaps[i] if i < len(gaps) else (gaps[0] if len(gaps) == 1 else None)
                    if gap:
                        start_t, end_t = gap  # already "HH:MM" format
                        rest = seg
                    else:
                        console.print(f"  [dim yellow]skipped (no time range): {seg}[/dim yellow]")
                        continue
                else:
                    console.print(f"  [dim yellow]skipped (bad format): {seg}[/dim yellow]")
                    continue
            else:
                start_t, end_t, rest = m.group(1), m.group(2), m.group(3).strip()
        else:
            raw_s, raw_e, rest = m.group(1), m.group(2), m.group(3).strip()
            # Normalize HHMM → HH:MM
            start_t = f"{int(raw_s) // 100:02d}:{int(raw_s) % 100:02d}"
            end_t = f"{int(raw_e) // 100:02d}:{int(raw_e) % 100:02d}"

        # Extract @project override
        project = None
        at_match = re.search(r'@(\S+)', rest)
        if at_match:
            project = at_match.group(1)
            rest = rest[:at_match.start()].strip()

        desc = rest or "gap"

        # Auto-map project from description if not overridden
        if not project:
            desc_lower = desc.lower()
            # Try exact match, then check if desc ends with a known key
            project = _GAP_PROJECT_MAP.get(desc_lower)
            if not project:
                for key, proj in _GAP_PROJECT_MAP.items():
                    if key in desc_lower:
                        project = proj
                        break

        cmd = ["python3", str(cli), "create", desc, start_t, end_t]
        if project:
            cmd.append(project)
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            created += 1
        except Exception as e:
            console.print(f"  [dim yellow]toggl error: {e}[/dim yellow]")
    return created


def start_toggl(description, project=None):
    """Start a Toggl timer."""
    cli = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
    cmd = ["python3", str(cli), "start", description]
    if project:
        cmd.append(project)
    subprocess.run(cmd, capture_output=True, timeout=10)


# ── Auto-suggest goals ────────────────────────────────────────────────────

_TODOIST_API_BASE = "https://api.todoist.com/api/v1"
_EXCLUDE_LABELS = {"#-1g", "#0g"}
_EXCLUDE_PROJECTS = {"6XfvCQ3p8Gq6fhGR"}  # 0g project

# Label → short display name for suggestion list
_LABEL_DISPLAY = {
    "i9": "i9", "m5x2": "m5x2", "xk87": "xk87", "xk88": "xk88",
    "s897": "s897", "hcm": "hcm", "hcmc": "hcmc", "hcb": "hcb",
    "hcbc": "hcb", "hci": "hci", "g245": "g245", "i447": "i447",
    "f692": "f692", "f693": "f693", "家": "家", "epcn": "epcn",
    "qz12": "qz12", "n156": "n156",
}


def _get_todoist_api_key():
    """Get Todoist API key from env or macOS Keychain."""
    key = os.environ.get("TODOIST_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "todoist-api-key", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _parse_task_points(content: str):
    """Extract [N] value and (N) time estimate from task content.
    Returns (value, time_min) or (None, None) if not parseable."""
    val_m = re.search(r'\[(\d+)\]', content)
    time_m = re.search(r'\((\d+)\)', content)
    value = int(val_m.group(1)) if val_m else None
    time_min = int(time_m.group(1)) if time_m else None
    return value, time_min


def _domain_from_labels(labels: list) -> str:
    """Pick the best domain display name from a task's labels."""
    for label in labels:
        if label in _LABEL_DISPLAY:
            return _LABEL_DISPLAY[label]
    return ""


def fetch_suggested_goals(max_results=5):
    """Fetch open Todoist tasks ranked by [N]/(N) ratio.

    Returns list of dicts: {content, value, time, ratio, domain, id}
    sorted by ratio descending. Excludes #-1g/#0g tasks and the 0g project."""
    if _requests is None:
        return []
    api_key = _get_todoist_api_key()
    if not api_key:
        return []
    headers = {"Authorization": f"Bearer {api_key}"}
    candidates = []
    cursor = None
    try:
        for _ in range(5):  # page limit safety
            params = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = _requests.get(
                f"{_TODOIST_API_BASE}/tasks", headers=headers,
                params=params, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            tasks = data.get("results", []) if isinstance(data, dict) else data
            for t in tasks:
                labels = t.get("labels") or []
                # Skip 0g project and #-1g/#0g tagged tasks
                if t.get("project_id") in _EXCLUDE_PROJECTS:
                    continue
                if _EXCLUDE_LABELS & set(labels):
                    continue
                content = t.get("content", "")
                value, time_min = _parse_task_points(content)
                if value is None:
                    continue  # no [N] = can't rank
                if time_min and time_min > 0:
                    ratio = value / time_min
                else:
                    ratio = value / 30  # no time estimate = assume 30min
                candidates.append({
                    "content": content,
                    "value": value,
                    "time": time_min,
                    "ratio": ratio,
                    "domain": _domain_from_labels(labels),
                    "id": t.get("id"),
                })
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor:
                break
    except Exception:
        return []
    candidates.sort(key=lambda x: (-x["ratio"], -(x["value"] or 0)))
    return candidates[:max_results]


def format_suggestions(suggestions):
    """Format suggestions as numbered lines for display in a card."""
    lines = []
    for i, s in enumerate(suggestions, 1):
        content = s["content"]
        if len(content) > 65:
            content = content[:62] + "..."
        domain = f"  [dim]@{s['domain']}[/dim]" if s["domain"] else ""
        lines.append(f"  [bold]{i}.[/bold] {content}{domain}")
    return "\n".join(lines)


def snapshot_build_order():
    """Archive the build order to v_logs at the END of a day, not the start.

    Called when -2n launches. If yesterday's snapshot doesn't exist, save the
    current build order as yesterday's archive (it still has enrichment data
    before the daily reset clears it). Never create today's snapshot, since
    the day is still in progress."""
    yesterday = datetime.now() - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y.%m.%d")
    v_logs = Path.home() / "vault/g245/v_logs"
    snapshot = v_logs / f"{yesterday_str}-build-order.md"
    if snapshot.exists():
        return  # already archived
    if BUILD_ORDER.exists():
        v_logs.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(BUILD_ORDER.read_text())


def main():
    console.print(Rule("[bold]-2n Interrupt Queue[/bold]", style="dim"))
    set_term_color("black")

    # Daily build order snapshot (idempotent)
    snapshot_build_order()

    card_num = 0
    total_cards = 0  # we'll count dynamically

    # ── Gather state ──────────────────────────────────────────────────────
    salah_done = check_neon_column("ص")
    # Check -1l by looking for the 0₦ column (approximate)
    # -1l doesn't have a dedicated 0₦ column, skip for now

    idx, block_name, block_start, block_end = get_current_block()
    block_goals = read_block_goals()
    current_goals = block_goals.get(block_name, [])
    goals_set = bool(current_goals) and any(g for g in current_goals)

    # Check meeting briefs
    mtg_briefs = []
    if MTG_BRIEFS.exists():
        try:
            mtg_briefs = json.loads(MTG_BRIEFS.read_text())
            if not isinstance(mtg_briefs, list):
                mtg_briefs = []
        except Exception:
            mtg_briefs = []

    # Prune stale briefs (meeting started >4h ago). Without this, mtg.py only
    # filters when staging *new* briefs, so old ones persist forever and
    # /inbound keeps prompting until the user manually acks each expired card.
    mtg_briefs = _prune_stale_briefs(mtg_briefs)

    # Check time gaps in the previous block
    prev_idx, prev_name, prev_start, prev_end = get_previous_block()
    time_gaps = []
    if idx > 0:  # skip if we're in the first block of the day
        time_gaps = check_time_gaps(prev_start, prev_end)

    # Count cards needed
    prayer_marker_exists = has_prayer_marker(block_name)
    cards_needed = []
    # The ☀️ marker is per-2h-block; the Neon ص column is per-day. Don't let
    # the daily mark suppress the per-block prompt — only check the marker.
    if not prayer_marker_exists:
        cards_needed.append("salah")
    if time_gaps:
        cards_needed.append("gaps")
    if not goals_set:
        cards_needed.append("-1g")
    for brief in mtg_briefs:
        cards_needed.append("mtg")
    cards_needed.append("ibx0")
    total_cards = len(cards_needed)

    if not cards_needed or (len(cards_needed) == 1 and cards_needed[0] == "ibx0"):
        # Only ibx0 — show block status and go straight to inbox
        salah_status = "✓" if salah_done else ("☀️" if prayer_marker_exists else "·")
        if goals_set:
            console.print(f"[green]{salah_status}[/green] صلاة  [green]✓[/green] -1g ({block_name}): {', '.join(current_goals)}")
        console.print()
    else:
        # ── Card 1: صلاة ──────────────────────────────────────────────
        if not prayer_marker_exists:
            card_num += 1
            set_term_color("red")
            panel = Panel(
                "where is the sun?",
                title=f"[bold]Card {card_num}/{total_cards}: ☀️[/bold]",
                border_style="red",
                padding=(1, 2),
            )
            console.print(panel)
            try:
                console.input("[dim]any key to continue[/dim] ")
            except (KeyboardInterrupt, EOFError):
                pass
            set_term_color("black")
            write_prayer_marker(block_name)

        # ── Card 1.5: Time gap audit ─────────────────────────────────
        if time_gaps:
            card_num += 1
            gap_lines = "  ".join(
                f"你{gs}-{ge}做了什么？" for gs, ge in time_gaps
            )
            resp = prompt_card(
                card_num, total_cards, f"⏱ Gaps · {prev_name} ({prev_start}-{prev_end})",
                gap_lines,
                options="fill/skip", preserve_case=True,
            )
            if resp and resp.lower() != "skip":
                console.print(f"[dim]  logging gaps...[/dim]")
                n = fill_time_gaps(resp, gaps=time_gaps)
                console.print(f"[green]  ✓ {n} gap(s) filled[/green]")

        # ── Card 2: -1g ───────────────────────────────────────────────
        # Re-check the current block in case it changed while earlier cards
        # were being displayed (user was slow to respond to salah/gaps).
        new_idx, new_block, new_start, new_end = get_current_block()
        if new_idx != idx:
            # Block changed during card display; skip remaining cards and
            # let the wrapper restart with fresh state for the new block.
            console.print(f"[yellow]  block changed → {new_block}, restarting...[/yellow]")
            return 0
        if not goals_set:
            card_num += 1
            # Fetch auto-suggestions from Todoist
            suggestions = fetch_suggested_goals()
            body = f"No goals set for [bold]{block_name}[/bold] ({block_start}-{block_end})."
            if suggestions:
                body += f"\n\n[cyan]Suggested (by value/min):[/cyan]\n{format_suggestions(suggestions)}"
                body += "\n\n[dim]Pick numbers (e.g. 1,3), type custom goals, or skip.[/dim]"
            else:
                body += "\n[dim]Type goals (comma-separated), or skip.[/dim]"
            resp = prompt_card(
                card_num, total_cards, "-1g",
                body,
                options="pick/goals/skip", preserve_case=True,
            )
            if resp and resp.lower() != "skip":
                # Check if response is number picks from suggestions
                goals_text = resp
                if suggestions and re.match(r'^[\d,\s]+$', resp.strip()):
                    picks = []
                    for part in resp.split(","):
                        part = part.strip()
                        if part.isdigit():
                            idx_pick = int(part)
                            if 1 <= idx_pick <= len(suggestions):
                                picks.append(suggestions[idx_pick - 1]["content"])
                    if picks:
                        goals_text = "\n".join(picks)

                # Local build-order write is fast and authoritative. Do it
                # synchronously so subsequent cards see the updated goals.
                parsed_goals = parse_goals_text(goals_text)
                wrote_locally = write_block_goals(block_name, parsed_goals)
                # Spawn claude in a detached subprocess for Todoist sync. The
                # user proceeds to the next card immediately — claude's only
                # remaining job (Todoist task creation) finishes asynchronously
                # in the background and may outlive this TUI process.
                spawn_1g_background(goals_text)
                if wrote_locally:
                    console.print(
                        f"[green]  ✓ -1g → {block_name}[/green] "
                        f"[dim](todoist syncing in background)[/dim]"
                    )
                    # Refresh in-memory goals so the post-ibx0 summary prints them.
                    current_goals = list(parsed_goals)
                else:
                    console.print(f"[red]  ⚠ failed to write goals to build order[/red]")

        # ── Card 3.5: Meeting prep ────────────────────────────────────
        remaining_briefs = []
        for brief in mtg_briefs:
            card_num += 1
            title = brief.get("title", "Meeting")
            body = brief.get("body", "(no brief)")
            resp = prompt_card(card_num, total_cards, f"📅 {title}", body, options="ack/skip")
            if resp != "ack":
                remaining_briefs.append(brief)
        # Update briefs file
        if mtg_briefs:
            if remaining_briefs:
                MTG_BRIEFS.write_text(json.dumps(remaining_briefs, indent=2))
            else:
                MTG_BRIEFS.unlink(missing_ok=True)

    # ── Card 4: ibx0 ─────────────────────────────────────────────────
    # Import and run ibx0's main loop directly — this handles all inbox
    # cards, polling, and the persistent idle state.
    # A background thread watches for 2h block changes and forces ibx0
    # to exit so -2n restarts with fresh ritual cards.
    console.print()
    console.print(Rule("[dim]Inbox[/dim]", style="dim"))

    # Mark that we reached inbox processing for this block
    write_inbox_marker(block_name)

    import ibx0

    launch_block_idx = idx  # block index when we started

    def _watch_block_change():
        """Poll every 30s; when the 地支 block changes, force ibx0 to exit."""
        while True:
            time.sleep(30)
            new_idx, new_name, _, _ = get_current_block()
            if new_idx != launch_block_idx:
                console.print(f"\n[bold yellow]── block changed → {new_name} ──[/bold yellow]")
                os._exit(0)  # exit the whole process; wrapper restarts with new ritual cards

    watcher = threading.Thread(target=_watch_block_change, daemon=True)
    watcher.start()

    ibx0.main()

    # ── Persistent idle: show goals, wait for block change ────────────
    # The watcher thread (above) will os._exit(0) on block change, which
    # triggers the wrapper to restart with fresh ritual cards. Meanwhile,
    # we idle here showing the goal panel so the user always has context.
    console.print()
    set_term_color("blue")

    # Offer to start a timer on a goal before idling
    if current_goals:
        console.print(render_block_status_panel(block_name))
        console.print()
        try:
            resp = console.input("[dim]Start timer? (1/2/skip)>[/dim] ").strip()
            if resp.isdigit() and 1 <= int(resp) <= len(current_goals):
                goal = current_goals[int(resp) - 1]
                desc = re.sub(r'\s*\{?\d+\}?\s*$', '', goal).strip()
                start_toggl(desc, "g245")
                console.print(f"[green]  ▶ Started: {desc} → g245[/green]")
            elif resp in ("y", ""):
                goal = current_goals[0]
                desc = re.sub(r'\s*\{?\d+\}?\s*$', '', goal).strip()
                start_toggl(desc, "g245")
                console.print(f"[green]  ▶ Started: {desc} → g245[/green]")
        except (KeyboardInterrupt, EOFError):
            pass

    # Idle loop: block watcher will kill us on transition. Refresh the
    # goal panel every 60s so completed goals update in real time.
    console.print()
    console.print("[dim]Idle. Waiting for next block...[/dim]")
    try:
        while True:
            console.clear()
            console.print(Rule("[dim]-2n · idle[/dim]", style="dim"))
            console.print()
            console.print(render_block_status_panel())
            console.print()
            console.print("[dim]Waiting for next block... (Ctrl+C to quit)[/dim]")
            time.sleep(60)
    except KeyboardInterrupt:
        return 2  # user quit; wrapper will not restart


if __name__ == "__main__":
    main()
