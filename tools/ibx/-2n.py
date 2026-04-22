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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

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
    ("فجر",   "05:00", "06:59"),
    ("شروق",  "07:00", "08:59"),
    ("صباح",  "09:00", "10:59"),
    ("ظهر",   "11:00", "12:59"),
    ("عصر",   "13:00", "14:59"),
    ("آصيل",  "15:00", "16:59"),
    ("غروب",  "17:00", "18:59"),
    ("غسق",   "19:00", "20:59"),
    ("زلة",   "21:00", "22:59"),
]


def set_term_color(color):
    if TERM_COLOR.exists():
        subprocess.run(["bash", str(TERM_COLOR), color], capture_output=True)


def get_current_block():
    """Return (index, arabic_name, start_time, end_time) for current 2h block."""
    now = datetime.now()
    hour = now.hour
    idx = max(0, min(8, (hour - 5) // 2))
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


def read_block_goals():
    """Read -1₲ section from build order. Returns {arabic_name: [goals]}."""
    if not BUILD_ORDER.exists():
        return {}
    text = BUILD_ORDER.read_text()
    if "## -1₲" not in text:
        return {}
    section = text[text.index("## -1₲"):]
    blocks = {}
    current_block = None
    for line in section.split("\n"):
        line_stripped = line.strip()
        # Check if this is a block header (e.g., "- فجر")
        if line.startswith("- ") and not line.startswith("    "):
            block_name = line_stripped.lstrip("- ").strip()
            current_block = block_name
            blocks[current_block] = []
        elif current_block and line.startswith("    - [ ]"):
            goal = line.strip().removeprefix("- [ ]").strip()
            if goal:
                blocks[current_block].append(goal)
    return blocks


def prompt_card(card_num, total, title, body, options="y/skip"):
    """Display a card and wait for user input. Returns the user's response."""
    set_term_color("red")
    panel = Panel(
        body,
        title=f"[bold]Card {card_num}/{total}: {title}[/bold]",
        border_style="red",
        padding=(1, 2),
    )
    console.print(panel)
    try:
        response = console.input(f"[bold red]({options})>[/bold red] ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        response = "skip"
    set_term_color("black")
    return response


def run_did(habit):
    """Run /did for a habit via claude CLI."""
    subprocess.run(
        ["claude", "-p", f"/did {habit}", "--allowedTools",
         "Bash,Read,Edit,Write,mcp__todoist__complete-tasks,mcp__todoist__find-tasks"],
        capture_output=True, timeout=120
    )


def run_1g(goals_text):
    """Run /-1g via claude CLI."""
    subprocess.run(
        ["claude", "-p", f"/-1g {goals_text}", "--allowedTools",
         "Bash,Read,Edit,Write,mcp__todoist__add-tasks"],
        capture_output=True, timeout=120
    )


def start_toggl(description, project=None):
    """Start a Toggl timer."""
    cli = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"
    cmd = ["python3", str(cli), "start", description]
    if project:
        cmd.append(project)
    subprocess.run(cmd, capture_output=True, timeout=10)


def main():
    console.print(Rule("[bold]-2n Interrupt Queue[/bold]", style="dim"))
    set_term_color("black")

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

    # Count cards needed
    cards_needed = []
    if not salah_done:
        cards_needed.append("salah")
    if not goals_set:
        cards_needed.append("-1g")
    for brief in mtg_briefs:
        cards_needed.append("mtg")
    cards_needed.append("ibx0")
    total_cards = len(cards_needed)

    if not cards_needed or (len(cards_needed) == 1 and cards_needed[0] == "ibx0"):
        # Only ibx0 — show block status and go straight to inbox
        if goals_set:
            console.print(f"[green]✓[/green] صلاة done  [green]✓[/green] -1g ({block_name}): {', '.join(current_goals)}")
        console.print()
    else:
        # ── Card 1: صلاة ──────────────────────────────────────────────
        if not salah_done:
            card_num += 1
            resp = prompt_card(card_num, total_cards, "صلاة", "Have you prayed?")
            if resp == "y":
                console.print("[dim]  marking ص done...[/dim]")
                run_did("ص")
                console.print("[green]  ✓ صلاة logged[/green]")

        # ── Card 2: -1g ───────────────────────────────────────────────
        if not goals_set:
            card_num += 1
            resp = prompt_card(
                card_num, total_cards, "-1g",
                f"No goals set for [bold]{block_name}[/bold] ({block_start}-{block_end}).\nType goals below, or skip.",
                options="goals/skip"
            )
            if resp and resp != "skip":
                console.print(f"[dim]  setting -1g goals...[/dim]")
                run_1g(resp)
                console.print(f"[green]  ✓ -1g → {block_name}[/green]")
                # Re-read goals
                block_goals = read_block_goals()
                current_goals = block_goals.get(block_name, [])

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
    console.print()
    console.print(Rule("[dim]Inbox[/dim]", style="dim"))

    import ibx0
    ibx0.main()

    # ── After ibx0 exits (user quit) ──────────────────────────────────
    # Show goals as a final prompt
    if current_goals:
        console.print()
        console.print(f"[bold]Goals for {block_name} ({block_start}-{block_end}):[/bold]")
        for i, g in enumerate(current_goals, 1):
            console.print(f"  {i}. {g}")
        console.print()
        try:
            resp = console.input("[dim]Start timer? (1/2/skip)>[/dim] ").strip()
            if resp.isdigit() and 1 <= int(resp) <= len(current_goals):
                goal = current_goals[int(resp) - 1]
                # Strip {N} annotations for the timer description
                import re
                desc = re.sub(r'\s*\{?\d+\}?\s*$', '', goal).strip()
                start_toggl(desc, "g245")
                console.print(f"[green]  ▶ Started: {desc} → g245[/green]")
            elif resp == "y" or resp == "":
                goal = current_goals[0]
                import re
                desc = re.sub(r'\s*\{?\d+\}?\s*$', '', goal).strip()
                start_toggl(desc, "g245")
                console.print(f"[green]  ▶ Started: {desc} → g245[/green]")
        except (KeyboardInterrupt, EOFError):
            pass

    set_term_color("blue")


if __name__ == "__main__":
    main()
