#!/usr/bin/env python3
"""卯 — forced-linear wakeup sequence (-1₦ block rituals).

Purpose: drive JM through the five -1₦ block-ritual icons the moment he wakes
up, with NO skips — each icon must be completed before the next appears
(commitment via friction). This is the "activating" counterpart to /inbound:
where /inbound is a triage queue, 卯 is a wake-up engine.

Icons / order (activating: get up → decide → commit → act → process):
  ☀️ prayer   → write_prayer_marker
  🎯 -1g goal → set the block intention (required, ≥1)
  ⏱️ time-log → start a Toggl timer (the commit-to-action keystone)
  ✓ task      → do + log one small thing right now
  📧 inbox    → hand off to ibx0 (closes the -1₦ row)

Built as a sibling to /inbound: it loads `-2n.py` and reuses its tested
primitives (card renderer, prayer markers, -1g writer, Toggl, term colors)
instead of duplicating them.

Exit codes mirror the wrapper contract:
  0 — clean exit (sequence complete / block changed)
  2 — user quit (Ctrl+C)
  * — unexpected error (wrapper auto-fix + retry)
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TWO_N_PATH = _HERE / "-2n.py"
_TOGGL_CLI = Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py"

if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _load_two_n():
    spec = importlib.util.spec_from_file_location("_two_n", _TWO_N_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {_TWO_N_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── Pure helpers (unit-tested in test_wakeup.py) ──────────────────────────


def parse_goal_response(resp, suggestions, parse_goals_text):
    """Resolve a -1g card response into a list of goals, or None if invalid.

    `resp` may be number picks against `suggestions` (e.g. "1,3") or free text.
    "skip"/empty are rejected (returns None) so the forced card re-prompts."""
    if not resp or resp.strip().lower() == "skip":
        return None
    text = resp
    if suggestions and re.match(r"^[\d,\s]+$", resp.strip()):
        picks = []
        for part in resp.split(","):
            part = part.strip()
            if part.isdigit() and 1 <= int(part) <= len(suggestions):
                picks.append(suggestions[int(part) - 1]["content"])
        if not picks:
            return None
        text = "\n".join(picks)
    parsed = parse_goals_text(text)
    return parsed or None


def resolve_task_response(resp, habits):
    """Resolve a ✓-task card response into a habit/task string, or None.

    A bare number picks from `habits`; otherwise the free text is the task.
    "skip"/empty are rejected so the forced card re-prompts."""
    if resp is None:
        return None
    r = resp.strip()
    if not r or r.lower() == "skip":
        return None
    if r.isdigit() and 1 <= int(r) <= len(habits):
        return habits[int(r) - 1]["habit"]
    return r


def resolve_timer_desc(resp, default):
    """Resolve a ⏱️ card response into a timer description, or None.

    Empty input accepts `default` (the top goal); "skip" is rejected."""
    r = (resp or "").strip()
    if r.lower() == "skip":
        return None
    desc = default if r == "" else r
    return desc or None


# ── Runtime helpers (side effects) ────────────────────────────────────────


def timer_running():
    """True if a Toggl timer is currently running."""
    try:
        r = subprocess.run(
            ["python3", str(_TOGGL_CLI), "current"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and "Running:" in r.stdout
    except Exception:
        return False


def spawn_did_background(task):
    """Fire-and-forget `claude -p /did <task>` (detached), like
    spawn_1g_background — keeps the TUI snappy while the habit/task is logged
    to 0₦ + Todoist in the background."""
    log_dir = Path.home() / ".cache" / "inbound"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_dir / f"did-{int(time.time())}.log", "wb")
    return subprocess.Popen(
        ["claude", "-p", f"/did {task}", "--allowedTools",
         "Skill,Bash,Read,Edit,Write,mcp__todoist__complete-tasks,"
         "mcp__todoist__find-tasks"],
        stdin=subprocess.DEVNULL, stdout=log_fh, stderr=log_fh,
        start_new_session=True, close_fds=True,
    )


def _forced_card(M, num, total, title, body, options, validate,
                 *, multiline=False):
    """Show a card and re-prompt until `validate(resp)` returns non-None.

    No skip: empty/invalid input loops. Ctrl+C propagates (the wrapper treats
    it as an explicit quit)."""
    while True:
        resp = M.prompt_card(num, total, title, body, options=options,
                             preserve_case=True, multiline=multiline)
        cleaned = validate(resp)
        if cleaned is not None:
            return cleaned
        M.console.print(
            "[yellow]  ↻ required — complete this to continue "
            "(Ctrl+C to abort)[/yellow]"
        )


def main():
    M = _load_two_n()
    console = M.console

    console.print(M.Rule("[bold]卯 · wakeup — -1₦ block rituals[/bold]",
                         style="dim"))
    M.set_term_color("black")
    M.snapshot_build_order()

    idx, block_name, block_start, block_end = M.get_current_block()

    # ── Gather state: which icons are already complete? ───────────────────
    prayer_done = M.has_prayer_marker(block_name)
    goals = M.read_block_goals().get(block_name, [])
    goals_set = bool(goals) and any(g for g in goals)
    timer_on = timer_running()

    forced = []
    if not prayer_done:
        forced.append("prayer")
    if not goals_set:
        forced.append("goal")
    if not timer_on:
        forced.append("timer")
    forced.append("task")          # always do one task at wakeup
    total = len(forced) + 1        # +1 for the inbox handoff

    done_bits = []
    if prayer_done:
        done_bits.append("☀️")
    if goals_set:
        done_bits.append("🎯")
    if timer_on:
        done_bits.append("⏱️")
    if done_bits:
        console.print(
            f"[green]{' '.join(done_bits)} already done for "
            f"{block_name}[/green]\n"
        )

    num = 0
    current_goals = list(goals)

    # ── 1. ☀️ صلاة — mandatory ack (you can't advance until you're up) ────
    if "prayer" in forced:
        num += 1
        M.set_term_color("red")
        console.print(M.Panel(
            "where is the sun? — get up and find it.",
            title=f"[bold]Card {num}/{total}: ☀️ صلاة[/bold]",
            border_style="red", padding=(1, 2),
        ))
        try:
            console.input("[dim]press enter once you're up[/dim] ")
        except KeyboardInterrupt:
            M.set_term_color("black")
            return 2
        except EOFError:
            pass
        M.set_term_color("black")
        M.write_prayer_marker(block_name)
        console.print("[green]  ✓ ☀️ logged[/green]\n")

    # ── 2. 🎯 -1g — set the block intention (≥1 goal required) ────────────
    if "goal" in forced:
        num += 1
        suggestions = (M.fetch_block_suggestions(block_name, block_start, block_end)
                       or M.fetch_suggested_goals(max_results=3))
        body = (f"Set your intention for [bold]{block_name}[/bold] "
                f"({block_start}-{block_end}).")
        if suggestions:
            body += (f"\n\n[cyan]Suggested for this block:[/cyan]\n"
                     f"{M.format_suggestions(suggestions)}")
            body += "\n\n[dim]Pick numbers (e.g. 1,3) or type goals. Required.[/dim]"
        else:
            body += "\n[dim]Type goals (comma-separated). Required.[/dim]"

        parsed = _forced_card(
            M, num, total, "🎯 -1g", body, "pick/goals (required)",
            lambda r: parse_goal_response(r, suggestions, M.parse_goals_text),
        )
        M.write_block_goals(block_name, parsed)
        M.spawn_1g_background("\n".join(parsed))
        current_goals = parsed
        console.print(
            f"[green]  ✓ 🎯 → {block_name}: {', '.join(parsed)}[/green] "
            f"[dim](todoist syncing)[/dim]\n"
        )

    # ── 3. ⏱️ time-log — start a timer (the commit keystone) ──────────────
    if "timer" in forced:
        num += 1
        default = ""
        if current_goals:
            default = re.sub(r"\s*\{?\d+\}?\s*$", "", current_goals[0]).strip()
        body = "Commit: start a timer now."
        if default:
            body += (f"\n\n[cyan]Enter[/cyan] = start on “{default}” → g245\n"
                     "or type a different description.")
        else:
            body += "\n\nType what you're starting now."

        def _v_timer(resp):
            desc = resolve_timer_desc(resp, default)
            if desc is None:
                return None
            M.start_toggl(desc, "g245")
            if not timer_running():   # start failed → re-prompt
                return None
            return desc

        desc = _forced_card(M, num, total, "⏱️ time-log", body,
                            "enter/desc (required)", _v_timer)
        console.print(f"[green]  ✓ ⏱️ ▶ {desc} → g245[/green]\n")

    # ── 4. ✓ task — do + log one thing right now ──────────────────────────
    num += 1
    habits = M._unfinished_0n_today()
    if habits:
        listing = "\n".join(f"  {i}. {h['habit']}"
                            for i, h in enumerate(habits, 1))
        body = ("Do ONE thing now, then log it.\n\n"
                f"[cyan]Unfinished 0n habits:[/cyan]\n{listing}\n\n"
                "[dim]Pick a number, or type a task. Required.[/dim]")
    else:
        body = ("Do ONE thing now, then log it.\n\n"
                "[dim]Type the habit/task you just did. Required.[/dim]")
    task = _forced_card(M, num, total, "✓ task", body, "pick/task (required)",
                        lambda r: resolve_task_response(r, habits))
    spawn_did_background(task)
    console.print(f"[green]  ✓ done: {task}[/green] [dim](logging)[/dim]\n")

    # ── 5. 📧 inbox — hand off to ibx0 (closes the -1₦ row) ───────────────
    num += 1
    console.print(M.Rule("[dim]📧 Inbox[/dim]", style="dim"))
    M.write_inbox_marker(block_name)
    console.print(
        f"[bold]Card {num}/{total}: 📧 — -1₦ complete. "
        f"Handing off to inbox.[/bold]\n"
    )
    M.set_term_color("blue")
    try:
        import ibx0
        ibx0.main()
    except KeyboardInterrupt:
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
