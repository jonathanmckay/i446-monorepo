#!/usr/bin/env python3
"""
-1g-cron.py — Cron jobs for the -1g (2-hour block goals) system.

Modes:
  block-end   Remove #关键径路 label from Todoist tasks that have #-1g
              (runs every 2h at block boundaries: 07,09,11,13,15,17,19,21,23)
  daily-reset Reset the -1₲ section in build order to empty checkboxes
              (runs at 04:00 local)

Usage:
  python3 -1g-cron.py block-end [--dry-run]
  python3 -1g-cron.py daily-reset [--dry-run]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: requests library required. Install with: pip3 install --user requests")
    sys.exit(1)

# --- Constants ---

TODOIST_API_BASE = "https://api.todoist.com/api/v1"
TODOIST_0G_PROJECT_ID = "6XfvCQ3p8Gq6fhGR"
CRITICAL_PATH_LABEL = "#\u5173\u952e\u5f84\u8def"  # #关键径路
MINUS1G_LABEL = "#-1g"

MD_FILE = Path.home() / "vault/g245/5e-1/build-order.md"

# The 9 地支 (Earthly Branch) time-of-day headings (order matters).
# 卯 = 04-06: the build-order / 0分 / janus convention.
TIME_BLOCKS = [
    "卯",  # 卯  04-06
    "辰",  # 辰  06-08
    "巳",  # 巳  08-10
    "午",  # 午  10-12
    "未",  # 未  12-14
    "申",  # 申  14-16
    "酉",  # 酉  16-18
    "戌",  # 戌  18-20
    "亥",  # 亥  20-22
]

LOG_PREFIX = "-1g-cron"


# --- API Key ---

def get_api_key() -> str:
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
    print(f"[{LOG_PREFIX}] ERROR: No API key. Set TODOIST_API_KEY or add to macOS Keychain.")
    sys.exit(1)


# --- Todoist helpers ---

def get_tasks_with_label(api_key: str, label: str):
    """Fetch all active tasks that have a given label."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    all_tasks = []
    cursor = None
    while True:
        params = {"project_id": TODOIST_0G_PROJECT_ID, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(f"{TODOIST_API_BASE}/tasks", headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        tasks = data.get("results", []) if isinstance(data, dict) else data
        for t in tasks:
            if label in (t.get("labels") or []):
                all_tasks.append(t)
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not cursor:
            break
    return all_tasks


def update_task_labels(api_key: str, task_id: str, new_labels: list):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"{TODOIST_API_BASE}/tasks/{task_id}",
        headers=headers,
        json={"labels": new_labels},
    )
    resp.raise_for_status()


# --- Block-end mode ---

def run_block_end(api_key: str, dry_run: bool):
    """Remove #关键径路 from tasks that have #-1g label."""
    tasks = get_tasks_with_label(api_key, MINUS1G_LABEL)
    print(f"[{LOG_PREFIX}] block-end: found {len(tasks)} tasks with {MINUS1G_LABEL}")

    for t in tasks:
        labels = list(t.get("labels", []))
        # Remove both #关键径路 and #-1g
        new_labels = [l for l in labels if l not in (CRITICAL_PATH_LABEL, MINUS1G_LABEL)]
        if dry_run:
            print(f"  [DRY RUN] Would update '{t['content'][:50]}': {labels} -> {new_labels}")
        else:
            update_task_labels(api_key, t["id"], new_labels)
            print(f"  Updated '{t['content'][:50]}': removed {CRITICAL_PATH_LABEL} + {MINUS1G_LABEL}")

    print(f"[{LOG_PREFIX}] block-end done: {len(tasks)} tasks updated")


# --- Daily reset mode ---

def _archive_before_reset():
    """Archive yesterday's enriched build order before resetting."""
    from datetime import datetime, timedelta
    yday = datetime.now() - timedelta(days=1)
    yesterday = yday.strftime("%Y.%m.%d")
    v_logs = MD_FILE.parent / "v_logs"
    snapshot = v_logs / f"{yesterday}-build-order.md"
    if snapshot.exists():
        print(f"[{LOG_PREFIX}] archive already exists: {snapshot.name}")
        return
    if MD_FILE.exists():
        v_logs.mkdir(parents=True, exist_ok=True)
        text = MD_FILE.read_text(encoding="utf-8")
        # Back-link to the previous day's archive (navigable in Obsidian).
        try:
            import sys as _s; _s.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
            import build_order_links as _bol
            text = _bol.with_prev_day_link(text, yday.date())
        except Exception:
            pass
        snapshot.write_text(text, encoding="utf-8")
        print(f"[{LOG_PREFIX}] archived {snapshot.name}")


def _archive_0g_goals(goal_lines):
    """Reconcile the just-ended day's 0₲ goals — with their final done/undone
    state — into the SAME durable log 0g_log.py writes at set-time
    (~/vault/g245/0g-log.md).

    Consolidated 2026-09-16: this used to write a second, divergent copy at
    MD_FILE.parent/0g-log.md (g245/5e-1/) — two daemons silently maintaining
    two logs of "the same thing", diverging whenever one ran and the other
    didn't (retired copy archived to g245/z_archive/). 0g_log.py's set-time
    log usually already has today's goals (always unchecked `- [ ]`, logged
    the instant /0g ran); this reconciliation updates each matched goal's
    checkbox to its FINAL state and appends any goal 0g_log.py never saw
    (e.g. its own run failed that day) — it never creates a second file.
    Matching/merging reuses 0g_log.py's own normalization so "same goal" is
    judged identically in both places. `goal_lines` are raw markdown
    checkbox lines for the day being reset (i.e. yesterday). Idempotent:
    re-running just re-applies the same final state, never duplicates."""
    from datetime import datetime, timedelta
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_0g_log_mod", Path(__file__).parent / "0g_log.py")
    _0g_log = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_0g_log)

    day = (datetime.now() - timedelta(days=1)).strftime("%Y.%m.%d")
    log = _0g_log.LOG_PATH
    day_heading = f"## {day}"

    final = {_0g_log._goal_text(l): l.strip()
             for l in goal_lines if _0g_log._CHECKBOX.match(l)}
    if not final:
        return

    text = log.read_text(encoding="utf-8") if log.exists() else \
        _0g_log._HEADER.format(date=day.replace(".", "-"))
    lines = text.split("\n")

    start = next((i for i, l in enumerate(lines) if l.strip() == day_heading), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
        remaining = dict(final)
        for i in range(start + 1, end):
            if _0g_log._CHECKBOX.match(lines[i]):
                key = _0g_log._goal_text(lines[i])
                if key in remaining:
                    lines[i] = remaining.pop(key)
        if remaining:
            insert = end
            while insert - 1 > start and not lines[insert - 1].strip():
                insert -= 1
            lines[insert:insert] = list(remaining.values())
    else:
        section = [day_heading, ""] + list(final.values()) + [""]
        insert_at = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
        lines[insert_at:insert_at] = section

    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines), encoding="utf-8")
    print(f"[{LOG_PREFIX}] reconciled {len(goal_lines)} 0₲ goal(s) into 0g-log.md ({day})")


def run_daily_reset(dry_run: bool):
    """Reset the -1₲ section in build order to empty checkboxes."""
    if not MD_FILE.exists():
        print(f"[{LOG_PREFIX}] ERROR: {MD_FILE} not found")
        return

    # Archive the enriched build order before wiping it
    if not dry_run:
        _archive_before_reset()

    text = MD_FILE.read_text(encoding="utf-8")
    lines = text.split("\n")

    # Find ## -1₲ section
    section_start = -1
    section_end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and "-1\u20b2" in stripped:
            section_start = i
        elif section_start >= 0 and stripped.startswith("## ") and i > section_start:
            section_end = i
            break

    if section_start < 0:
        print(f"[{LOG_PREFIX}] ERROR: No ## -1₲ section found")
        return

    # Build replacement: each time block with one empty checkbox
    new_section = [lines[section_start], ""]
    for block_name in TIME_BLOCKS:
        new_section.append(f"- {block_name}")
        new_section.append("    - [ ] ")
    new_section.append("")

    if dry_run:
        print(f"[{LOG_PREFIX}] [DRY RUN] Would replace lines {section_start}-{section_end} with reset section")
        for line in new_section:
            print(f"  {line}")
        return

    # Replace -1₲ section
    new_lines = lines[:section_start] + new_section + lines[section_end:]

    # Also reset ## 0₲ section: replace content between "## 0₲" and
    # "### 以后的目标" with three empty checkboxes.
    og_start = -1
    og_end = -1
    for i, line in enumerate(new_lines):
        stripped = line.strip()
        if stripped == "## 0₲" or stripped == "## 0\u20b2":
            og_start = i
        elif og_start >= 0 and stripped.startswith("### "):
            og_end = i
            break
    if og_start >= 0 and og_end > og_start:
        # Preserve the day's actual goals to a durable log BEFORE wiping them, so
        # the reset can't erase them without a trace. Only real (non-empty) goals.
        goal_lines = [l for l in new_lines[og_start + 1:og_end]
                      if re.match(r"^\s*- \[[ xX]\]\s*\S", l)]
        if goal_lines:
            _archive_0g_goals(goal_lines)
        og_replacement = [new_lines[og_start], "- [ ] ", "- [ ] ", "- [ ] ", ""]
        new_lines = new_lines[:og_start] + og_replacement + new_lines[og_end:]
        print(f"[{LOG_PREFIX}] daily-reset: 0₲ section reset (3 empty checkboxes)")

    # Stamp the frontmatter date so the perpetual build-order file reflects the
    # current day instead of sticking at its creation date (it was stale at
    # 2026-03-02). Only touch lines inside the leading --- frontmatter block.
    today_iso = date.today().isoformat()
    if new_lines and new_lines[0].strip() == "---":
        for i in range(1, len(new_lines)):
            if new_lines[i].strip() == "---":
                break
            if new_lines[i].startswith("date:"):
                new_lines[i] = f"date: {today_iso}"
            elif new_lines[i].startswith("updated:"):
                new_lines[i] = f"updated: {today_iso}"

    # Atomic write
    tmp = MD_FILE.with_suffix(".md.tmp")
    tmp.write_text("\n".join(new_lines), encoding="utf-8")
    tmp.rename(MD_FILE)
    print(f"[{LOG_PREFIX}] daily-reset: -1₲ section reset ({len(TIME_BLOCKS)} blocks)")


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="-1g cron jobs")
    parser.add_argument("mode", choices=["block-end", "daily-reset"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.mode == "block-end":
        api_key = get_api_key()
        run_block_end(api_key, args.dry_run)
    elif args.mode == "daily-reset":
        run_daily_reset(args.dry_run)


if __name__ == "__main__":
    main()
