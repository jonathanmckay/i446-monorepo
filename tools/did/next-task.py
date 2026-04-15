#!/usr/bin/env python3
"""Display next task suggestions from the task queue cache.

Usage: python3 next-task.py <completed_habit> [extra_completed ...]

Reads ~/vault/z_ibx/task-queue.json and completed-today.json,
filters out completed tasks and tasks not due today/overdue,
prints a compact "Next up" menu.
"""

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

CACHE = Path.home() / "vault/z_ibx/task-queue.json"
COMPLETED = Path.home() / "vault/z_ibx/completed-today.json"

# Suffixes to strip when matching completed names against task content
STRIP_SUFFIXES = [
    " - Daily 分", " - Healthy Breakfast", " - Clean spaces",
    " - Time", " - Push commit", " - Daily Spa", " - Sublime/Neon",
    " - Charge", " - Daily Goals",
]


def strip_task_name(content: str) -> str:
    """Strip (N), [N], {N} and known suffixes to get bare name."""
    s = content
    for suffix in STRIP_SUFFIXES:
        s = s.replace(suffix, "")
    s = re.sub(r'\s*\(\d+\)', '', s)
    s = re.sub(r'\s*\[\d+\]', '', s)
    s = re.sub(r'\s*\{\d+\}', '', s)
    return s.strip().lower()


def main():
    just_completed = [a.lower() for a in sys.argv[1:]]

    # Read cache
    if not CACHE.exists():
        return
    try:
        cache = json.loads(CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return

    tasks = cache.get("tasks", [])
    if not tasks:
        return

    # Read completed-today
    completed_names: set[str] = set(just_completed)
    if COMPLETED.exists():
        try:
            ct = json.loads(COMPLETED.read_text())
            if ct.get("date") == date.today().isoformat():
                completed_names.update(n.lower() for n in ct.get("names", []))
        except (json.JSONDecodeError, OSError):
            pass

    today_str = date.today().isoformat()  # YYYY-MM-DD

    filtered = []
    for t in tasks:
        # Filter by due date: only today or overdue
        due = t.get("dueDate", "")
        if due and due > today_str:
            continue

        # Filter out completed tasks
        bare = strip_task_name(t["content"])
        if any(c in bare or bare in c for c in completed_names if c):
            continue

        filtered.append(t)
        if len(filtered) >= 5:
            break

    if not filtered:
        return

    # Display
    print("\nNext up:")
    max_content = max(len(t["content"]) for t in filtered)
    for i, t in enumerate(filtered, 1):
        pad = max_content - len(t["content"]) + 4
        print(f"  {i}. {t['content']}{' ' * pad}{t['cat']}")
    print(f"  {len(filtered) + 1}. [skip]")
    print(f"\nPick [1-{len(filtered) + 1}]:")


if __name__ == "__main__":
    main()
