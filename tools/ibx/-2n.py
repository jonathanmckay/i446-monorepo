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
BUILD_ORDER = Path.home() / "vault/g245/build-order.md"
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
    # The block name is always the first whitespace-delimited token (a single
    # 地支 character). Everything after it is marker/duration decoration
    # (☀️ 📧 ⏰ ✅ 🎯 ⏱️ … or "(134min)"), so we don't need to enumerate every
    # possible marker — just take the leading token.
    tokens = name.split()
    return tokens[0] if tokens else name


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
    except KeyboardInterrupt:
        set_term_color("black")
        raise
    except EOFError:
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
    """Parse comma-separated gap fills and create Toggl entries + optional 0分 points.

    Each segment: '[HHMM-HHMM] description [+N] [@project]'
    e.g. '900-915 wake up, 915-930 2nd hci, +30 @i9, DS:JM 1200-1230'

    Tokens (order-insensitive within each segment):
      HHMM-HHMM  — time range for Toggl entry (falls back to gap positional match)
      +N         — points to write to 0分 (column determined by @project or auto-map)
      @project   — Toggl project override AND 0分 column source
      everything else — description

    If a segment has no time prefix and gaps is provided, the corresponding
    gap's time range is used (1:1 positional match, or the single gap if
    there's only one).
    """
    cli = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
    did_fast = Path.home() / "i446-monorepo/tools/did/did-fast.py"
    # Split on commas or newlines (multiline input from prompt_card)
    segments = [s.strip() for s in re.split(r"[,\n]", response) if s.strip()]
    created = 0
    for i, seg in enumerate(segments):
        # Extract +N points token
        points = None
        pts_match = re.search(r'\+(\d+)', seg)
        if pts_match:
            points = int(pts_match.group(1))
            seg = (seg[:pts_match.start()] + seg[pts_match.end():]).strip()

        # Extract @project override
        project = None
        at_match = re.search(r'@(\S+)', seg)
        if at_match:
            project = at_match.group(1)
            seg = (seg[:at_match.start()] + seg[at_match.end():]).strip()

        # Parse time range: HHMM-HHMM or H:MM-H:MM (anywhere in segment)
        start_t = end_t = None
        m = re.search(r'(\d{3,4})-(\d{3,4})', seg)
        if m:
            raw_s, raw_e = m.group(1), m.group(2)
            start_t = f"{int(raw_s) // 100:02d}:{int(raw_s) % 100:02d}"
            end_t = f"{int(raw_e) // 100:02d}:{int(raw_e) % 100:02d}"
            rest = (seg[:m.start()] + seg[m.end():]).strip()
        else:
            m = re.search(r'(\d{1,2}:\d{2})-(\d{1,2}:\d{2})', seg)
            if m:
                start_t, end_t = m.group(1), m.group(2)
                rest = (seg[:m.start()] + seg[m.end():]).strip()
            else:
                rest = seg
                # No time prefix; use gap time range if available
                if gaps:
                    gap = gaps[i] if i < len(gaps) else (gaps[0] if len(gaps) == 1 else None)
                    if gap:
                        start_t, end_t = gap
                elif not points:
                    # No time, no points — nothing to do
                    console.print(f"  [dim yellow]skipped (bad format): {seg}[/dim yellow]")
                    continue

        desc = rest or "gap"

        # Auto-map project from description if not overridden
        if not project:
            desc_lower = desc.lower()
            project = _GAP_PROJECT_MAP.get(desc_lower)
            if not project:
                for key, proj in _GAP_PROJECT_MAP.items():
                    if key in desc_lower:
                        project = proj
                        break

        # Create Toggl entry (if we have a time range)
        if start_t and end_t:
            cmd = ["python3", str(cli), "create", desc, start_t, end_t]
            if project:
                cmd.append(project)
            try:
                subprocess.run(cmd, capture_output=True, timeout=15)
                created += 1
            except Exception as e:
                console.print(f"  [dim yellow]toggl error: {e}[/dim yellow]")

        # Write points to 0分 via did-fast.py (Step 6: variable task)
        if points and project:
            did_input = f"gap [{points}] @{project}"
            console.print(f"  [dim]+{points} → did-fast ({project})[/dim]")
            try:
                did_result = subprocess.run(
                    ["python3", str(did_fast), did_input],
                    capture_output=True, text=True, timeout=30,
                )
                if did_result.returncode == 0:
                    console.print(f"  [green]✓ +{points} → 0分 ({project})[/green]")
                else:
                    console.print(f"  [red]did-fast error: {did_result.stderr or did_result.stdout}[/red]")
            except Exception as e:
                console.print(f"  [red]did-fast error: {e}[/red]")
        elif points and not project:
            console.print(f"  [dim yellow]+{points} skipped (no @project for 0分)[/dim yellow]")

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
        source = f"[dim cyan]\\[{s['source']}][/dim cyan] " if s.get("source") else ""
        lines.append(f"  [bold]{i}.[/bold] {source}{content}{domain}")
    return "\n".join(lines)


# ── Block-aware goal synthesis ─────────────────────────────────────────────
# Synthesize 3 candidate goals for the current 2h block from 4 signals:
#   [cal] meetings in-block (Google Calendar)
#   [1g]  open weekly goals for the block's domain (1g sheet)
#   [0g]  open daily goals from build order ## 0₲ section
#   [0n]  unfinished habits in today's 0n row
# Deterministic — no LLM. <2s budget; degrades silently if any source fails.

# Block 地支 → typical domain in JM's ideal week. Used to prioritize which
# weekly 1g goals to surface for the current block.
_BLOCK_DOMAIN = {
    "卯": "hci",   # 04-06 morning routine, image/personal brand
    "辰": "hcb",   # 06-08 breakfast / body
    "巳": "i9",    # 08-10 deep work
    "午": "i9",    # 10-12 meetings
    "未": "i9",    # 12-14 work block
    "申": "i9",    # 14-16 work block
    "酉": "m5x2",  # 16-18 personal ops
    "戌": "xk87",  # 18-20 family
    "亥": "hcm",   # 20-22 wind down / reflection
}


def _calendar_in_block(block_start_hhmm: str, block_end_hhmm: str):
    """Return [{title, start_hhmm, end_hhmm, duration_min}] for events that
    overlap the current 2h block today. Reads the m5c7 personal calendar via
    OAuth creds shared with mtg.py. Returns [] silently on any failure."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build as _gbuild
    except ImportError:
        return []
    creds_path = Path.home() / ".config/mtg/tokens.json"
    oauth_path = Path.home() / ".config/mtg/oauth.json"
    if not (creds_path.exists() and oauth_path.exists()):
        return []
    try:
        oauth = json.loads(oauth_path.read_text())
        tokens = json.loads(creds_path.read_text())
        creds = Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth["client_id"],
            client_secret=oauth["client_secret"],
            scopes=["https://www.googleapis.com/auth/calendar.readonly"],
        )
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        svc = _gbuild("calendar", "v3", credentials=creds)
        today = datetime.now().date()
        sh, sm = [int(x) for x in block_start_hhmm.split(":")]
        eh, em = [int(x) for x in block_end_hhmm.split(":")]
        local_start = datetime(today.year, today.month, today.day, sh, sm)
        local_end = datetime(today.year, today.month, today.day, eh, em)
        time_min = local_start.astimezone(timezone.utc).isoformat()
        time_max = local_end.astimezone(timezone.utc).isoformat()
        result = svc.events().list(
            calendarId="mckay@m5c7.com",
            timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=10,
        ).execute()
        out = []
        skip_patterns = ("focus time", "ooo", "lunch", "block", "hold")
        for ev in result.get("items", []):
            title = ev.get("summary", "(no title)")
            if any(p in title.lower() for p in skip_patterns):
                continue
            s = ev.get("start", {}).get("dateTime")
            e = ev.get("end", {}).get("dateTime")
            if not s or not e:
                continue
            try:
                sdt = datetime.fromisoformat(s).astimezone()
                edt = datetime.fromisoformat(e).astimezone()
            except ValueError:
                continue
            dur = int((edt - sdt).total_seconds() / 60)
            if dur < 15:
                continue
            out.append({
                "title": title,
                "start_hhmm": sdt.strftime("%H:%M"),
                "end_hhmm": edt.strftime("%H:%M"),
                "duration_min": dur,
            })
        return out
    except Exception:
        return []


def _open_daily_goals():
    """Parse open '## 0₲' items from build order. Returns list of
    {text, bonus, domain}. Ignores ### 以后的目标 backlog."""
    if not BUILD_ORDER.exists():
        return []
    out = []
    in_section = False
    for line in BUILD_ORDER.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("## 0₲"):
            in_section = True
            continue
        if in_section and stripped.startswith("##"):
            break
        if in_section and stripped.startswith("###"):
            break  # stop at 以后的目标 sub-section
        if in_section and stripped.startswith("- [ ]"):
            text = stripped[5:].strip()
            if not text:
                continue
            bonus_m = re.search(r'\{(\d+)\}', text)
            bonus = int(bonus_m.group(1)) if bonus_m else 0
            dom_m = re.search(r'@(\w+)', text)
            domain = dom_m.group(1) if dom_m else ""
            out.append({"text": text, "bonus": bonus, "domain": domain})
    return out


def _unfinished_0n_today():
    """Return list of {habit, col} for habit columns in today's 0n row that
    are blank. One AppleScript batch read. Returns [] on any error."""
    headers_path = Path.home() / ".claude/skills/did/headers.json"
    if not headers_path.exists():
        return []
    try:
        hdrs = json.loads(headers_path.read_text()).get("0n", {})
    except Exception:
        return []
    # Skip non-habit / structural columns
    skip = {"周", "日", "2026.0", "2025.0", "2027.0", "n color", "⎣∀clr", "#", "0n"}
    habits = [(name, col) for name, col in hdrs.items()
              if name.lower() not in skip and name.lower() not in {n.lower() for n in skip}]
    if not habits:
        return []
    cols_list = ",".join(str(c) for _, c in habits)
    names_list = ",".join(f'"{n}"' for n, _ in habits)
    today = datetime.now()
    script = f'''tell application "Microsoft Excel"
        set wb to workbook "Neon分v12.2.xlsx"
        set theSheet to sheet "0n" of wb
        set targetMonth to {today.month}
        set targetDay to {today.day}
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
        if todayRow = 0 then return ""
        set cols to {{{cols_list}}}
        set names to {{{names_list}}}
        set out to ""
        repeat with i from 1 to count of cols
            set v to value of cell (item i of cols) of row todayRow of theSheet
            if v is missing value or v as text = "" then
                set out to out & (item i of names) & linefeed
            end if
        end repeat
        return out
    end tell'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        return [{"habit": h.strip(), "col": ""} for h in result.stdout.splitlines() if h.strip()]
    except Exception:
        return []


def _open_weekly_1g(domain_hint: str = ""):
    """Read open weekly goals from the '1g' sheet. Returns
    [{text, domain, fen, pct_done}] sorted by domain match then 分 desc.
    'Open' = % Done (col G) is empty or < 1.0. Caps at 25 rows."""
    script = '''tell application "Microsoft Excel"
        set wb to workbook "Neon分v12.2.xlsx"
        set theSheet to sheet "1g" of wb
        set currentDomain to ""
        set out to ""
        repeat with r from 1 to 50
            set aVal to value of cell 1 of row r of theSheet
            set dVal to value of cell 4 of row r of theSheet
            set eVal to value of cell 5 of row r of theSheet
            set gVal to value of cell 7 of row r of theSheet
            if aVal is not missing value and (aVal as text) is not "" then
                set currentDomain to aVal as text
            end if
            if dVal is not missing value and (dVal as text) is not "" then
                set fen to ""
                if eVal is not missing value then set fen to eVal as text
                set pct to ""
                if gVal is not missing value then set pct to gVal as text
                set out to out & currentDomain & tab & (dVal as text) & tab & fen & tab & pct & linefeed
            end if
        end repeat
        return out
    end tell'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=12,
        )
        if result.returncode != 0:
            return []
    except Exception:
        return []
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        domain = parts[0].strip()
        text = parts[1].strip()
        if not text:
            continue
        try:
            fen = float(parts[2]) if len(parts) > 2 and parts[2].strip() else 0.0
        except ValueError:
            fen = 0.0
        try:
            pct = float(parts[3]) if len(parts) > 3 and parts[3].strip() else 0.0
        except ValueError:
            pct = 0.0
        if pct >= 1.0:
            continue  # already done
        rows.append({"text": text, "domain": domain, "fen": fen, "pct_done": pct})
    # Sort: domain match first, then by 分 desc
    hint = domain_hint.lower()
    norm = {"m5x2": "m5c7"}.get(hint, hint)
    rows.sort(key=lambda x: (
        0 if x["domain"].lower() == norm else 1,
        -x["fen"],
    ))
    return rows[:25]


def fetch_block_suggestions(block_name: str, block_start: str, block_end: str):
    """Synthesize 3 goal candidates for the current 2h block.
    Returns list of {content, source, domain} (≤3, dedup'd, prioritized)."""
    domain_hint = _BLOCK_DOMAIN.get(block_name, "")
    suggestions = []
    seen = set()

    def _add(content, source, domain=""):
        key = content.lower().strip()
        if key in seen or not key:
            return
        seen.add(key)
        suggestions.append({"content": content, "source": source, "domain": domain})

    # 1. Calendar — top in-block meeting becomes a prep card
    cal_events = _calendar_in_block(block_start, block_end)
    if cal_events:
        ev = cal_events[0]
        _add(f"prep for {ev['title']} ({ev['start_hhmm']})", "cal", domain_hint)
        # If multiple meetings, surface the longest second
        if len(cal_events) > 1:
            ev2 = max(cal_events[1:], key=lambda e: e["duration_min"])
            _add(f"prep for {ev2['title']} ({ev2['start_hhmm']})", "cal", domain_hint)

    # 2. Weekly 1g — top open goal for this block's domain
    weekly = _open_weekly_1g(domain_hint)
    for w in weekly[:2]:
        tag = f"1g {w['domain']}" if w['domain'] else "1g"
        _add(w["text"], tag, w["domain"])
        if len(suggestions) >= 3:
            break

    # 3. Daily 0g — top {bonus} goal (prefer domain match)
    daily = _open_daily_goals()
    daily.sort(key=lambda x: (
        0 if x["domain"].lower() == domain_hint else 1,
        -x["bonus"],
    ))
    for d in daily[:2]:
        _add(d["text"], "0g", d["domain"])
        if len(suggestions) >= 3:
            break

    # 4. 0n unfinished habits — fill remaining slots
    if len(suggestions) < 3:
        for h in _unfinished_0n_today()[:5]:
            _add(h["habit"], "0n")
            if len(suggestions) >= 3:
                break

    return suggestions[:3]


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
        # After archiving yesterday, clear stale emojis for the new day
        clear_prayer_markers()


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

    # Check time gaps in all past blocks (separate card per block)
    block_gaps = []  # list of (block_name, block_start, block_end, gaps)
    if idx > 0:
        for bi in range(idx):
            bname, bstart, bend = BLOCKS[bi]
            gaps = check_time_gaps(bstart, bend)
            if gaps:
                block_gaps.append((bname, bstart, bend, gaps))

    # Count cards needed
    prayer_marker_exists = has_prayer_marker(block_name)
    cards_needed = []
    # The ☀️ marker is per-2h-block; the Neon ص column is per-day. Don't let
    # the daily mark suppress the per-block prompt — only check the marker.
    if not prayer_marker_exists:
        cards_needed.append("salah")
    for _ in block_gaps:
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
            except KeyboardInterrupt:
                set_term_color("black")
                raise
            except EOFError:
                pass
            set_term_color("black")
            write_prayer_marker(block_name)

        # ── Card 1.5: Time gap audit (one card per block) ─────────────
        for bg_name, bg_start, bg_end, bg_gaps in block_gaps:
            card_num += 1
            # Re-check gaps at DISPLAY time: state was gathered at startup,
            # and the user may have filled the window in Toggl while earlier
            # cards sat on screen (or the session idled). Only show the card
            # for gaps that still exist right now.
            bg_gaps = check_time_gaps(bg_start, bg_end)
            if not bg_gaps:
                console.print(f"[dim]  ⏱ {bg_name} ({bg_start}-{bg_end}): already filled in Toggl — skipping[/dim]")
                continue
            gap_lines = "  ".join(
                f"你{gs}-{ge}做了什么？" for gs, ge in bg_gaps
            )
            resp = prompt_card(
                card_num, total_cards, f"⏱ Gaps · {bg_name} ({bg_start}-{bg_end})",
                gap_lines,
                options="desc [HHMM-HHMM] [+N] [@proj] / skip", preserve_case=True,
                multiline=True,
            )
            if resp and resp.lower() != "skip":
                console.print(f"[dim]  logging gaps...[/dim]")
                n = fill_time_gaps(resp, gaps=bg_gaps)
                console.print(f"[green]  ✓ {n} gap(s) filled[/green]")

        # ── Card 2: -1g ───────────────────────────────────────────────
        # Re-check the current block in case it changed while earlier cards
        # were being displayed (user was slow to respond to salah/gaps).
        # A block change means the new block's ritual cards (salah first)
        # never ran — patching state in place here would skip the prayer
        # card until the next block. Exit 0 instead so the wrapper reloads
        # with a fresh card pass for the new block.
        new_idx, new_block, _, _ = get_current_block()
        if new_idx != idx:
            console.print(f"[dim]  block → {new_block} — reloading cards[/dim]")
            return 0
        if not goals_set:
            card_num += 1
            # Synthesize 3 block-aware suggestions from cal/1g/0g/0n.
            # Falls back to Todoist [N]/(N) ratio list when no signals available.
            suggestions = fetch_block_suggestions(block_name, block_start, block_end)
            if not suggestions:
                suggestions = fetch_suggested_goals(max_results=3)
            body = f"No goals set for [bold]{block_name}[/bold] ({block_start}-{block_end})."
            if suggestions:
                body += f"\n\n[cyan]Suggested for this block:[/cyan]\n{format_suggestions(suggestions)}"
                body += "\n\n[dim]Pick numbers (e.g. 1,3), type custom goals (comma-separated), or skip.[/dim]"
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
        except KeyboardInterrupt:
            return 2
        except EOFError:
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
    if len(sys.argv) > 1 and sys.argv[1] == "--debug-suggest":
        idx, block_name, block_start, block_end = get_current_block()
        print(f"Block: {block_name} ({block_start}-{block_end}) idx={idx}")
        print(f"Domain hint: {_BLOCK_DOMAIN.get(block_name, '(none)')}")
        print()
        print("== Calendar in block ==")
        for ev in _calendar_in_block(block_start, block_end):
            print(f"  {ev['start_hhmm']}-{ev['end_hhmm']} ({ev['duration_min']}m) {ev['title']}")
        print()
        print("== Open daily 0₲ ==")
        for d in _open_daily_goals():
            print(f"  bonus={d['bonus']} domain={d['domain']!r} {d['text']}")
        print()
        print("== Open weekly 1g ==")
        for w in _open_weekly_1g(_BLOCK_DOMAIN.get(block_name, "")):
            print(f"  {w['domain']:10} fen={w['fen']:.0f} pct={w['pct_done']:.2f} {w['text']}")
        print()
        print("== Unfinished 0n today ==")
        for h in _unfinished_0n_today():
            print(f"  {h['habit']}")
        print()
        print("== Synthesized (top 3) ==")
        for i, s in enumerate(fetch_block_suggestions(block_name, block_start, block_end), 1):
            print(f"  {i}. [{s['source']}] {s['content']}  @{s['domain']}")
        sys.exit(0)
    main()
