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

MD_FILE = Path.home() / "vault/g245/-1\u20a6 , 0\u20a6 - Neon {Build Order}.md"

# The 9 Arabic time-of-day headings (order matters)
TIME_BLOCKS = [
    "\u0641\u062c\u0631",      # فجر     05-07
    "\u0634\u0631\u0648\u0642",  # شروق    07-09
    "\u0635\u0628\u0627\u062d",  # صباح    09-11
    "\u0638\u0647\u0631",      # ظهر     11-13
    "\u0639\u0635\u0631",      # عصر     13-15
    "\u0622\u0635\u064a\u0644",  # آصيل    15-17
    "\u063a\u0631\u0648\u0628",  # غروب    17-19
    "\u063a\u0633\u0642",      # غسق     19-21
    "\u0632\u0644\u0629",      # زلة     21-23
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

def run_daily_reset(dry_run: bool):
    """Reset the -1₲ section in build order to empty checkboxes."""
    if not MD_FILE.exists():
        print(f"[{LOG_PREFIX}] ERROR: {MD_FILE} not found")
        return

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

    # Replace
    new_lines = lines[:section_start] + new_section + lines[section_end:]
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
