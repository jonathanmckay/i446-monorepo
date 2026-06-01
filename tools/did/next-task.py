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
SKIPPED = Path.home() / "vault/z_ibx/skipped-today.json"

# Suffixes to strip when matching completed names against task content
STRIP_SUFFIXES = [
    " - Daily 分", " - Healthy Breakfast", " - Clean spaces",
    " - Time", " - Push commit", " - Daily Spa", " - Sublime/Neon",
    " - Charge", " - Daily Goals",
]


def _infer_cat(t: dict) -> str:
    """Infer category from labels when 'cat' field is missing (bucketed format)."""
    labels = t.get("labels", [])
    for lbl in labels:
        if lbl == "0neon":
            return "0n"
        if lbl == "1neon":
            return "1n"
        if lbl in ("夜neon",):
            return "0n"
        if lbl in ("関键路径", "关键路径", "关键径路", "#0g", "#-1g"):
            return "0g"
    return ""


def strip_task_name(content: str) -> str:
    """Strip (N), [N], {N} and known suffixes to get bare name."""
    s = content
    for suffix in STRIP_SUFFIXES:
        s = s.replace(suffix, "")
    s = re.sub(r'\s*\(\d+\)', '', s)
    s = re.sub(r'\s*\[\d+\]', '', s)
    s = re.sub(r'\s*\{\d+\}', '', s)
    return s.strip().lower()


def read_skipped_ids() -> set[str]:
    """Read today's skipped task IDs. Resets daily."""
    if not SKIPPED.exists():
        return set()
    try:
        data = json.loads(SKIPPED.read_text())
        if data.get("date") == date.today().isoformat():
            return set(data.get("ids", []))
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def skip_task(task_id: str):
    """Add a task ID to today's skip list (bottom of render order)."""
    skipped = {"date": date.today().isoformat(), "ids": []}
    if SKIPPED.exists():
        try:
            data = json.loads(SKIPPED.read_text())
            if data.get("date") == date.today().isoformat():
                skipped = data
        except (json.JSONDecodeError, OSError):
            pass
    if task_id not in skipped["ids"]:
        skipped["ids"].append(task_id)
    SKIPPED.write_text(json.dumps(skipped, indent=2))


def main():
    # Handle --skip <id> mode
    if len(sys.argv) >= 3 and sys.argv[1] == "--skip":
        skip_task(sys.argv[2])
        print(f"Skipped (moved to bottom)")
        return

    just_completed = [a.lower() for a in sys.argv[1:]]

    # Read cache
    if not CACHE.exists():
        return
    try:
        cache = json.loads(CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return

    # Cache uses bucketed keys (0neon, 1neon, 夜neon, 関键路径, today).
    # Flatten all list-valued keys into a single task list, deduping by ID.
    if "tasks" in cache:
        # Legacy flat format
        tasks = cache["tasks"]
    else:
        seen_ids: set[str] = set()
        tasks = []
        for k, v in cache.items():
            if isinstance(v, list):
                for t in v:
                    tid = t.get("id")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        tasks.append(t)
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

    skipped_ids = read_skipped_ids()
    today_str = date.today().isoformat()  # YYYY-MM-DD

    top = []
    bottom = []
    for t in tasks:
        # Filter by due date: only today or overdue
        due = t.get("due") or t.get("dueDate") or ""
        if due and due > today_str:
            continue

        # Filter out completed tasks
        bare = strip_task_name(t["content"])
        if any(c in bare or bare in c for c in completed_names if c):
            continue

        if t.get("id") in skipped_ids:
            continue  # hidden for the rest of the day
        else:
            top.append(t)

    filtered = top[:9]

    if not filtered:
        return

    # Display
    print("\nNext up:")
    max_content = max(len(t["content"]) for t in filtered)
    for i, t in enumerate(filtered, 1):
        pad = max_content - len(t["content"]) + 4
        tid = t.get("id", "")
        cat = t.get("cat") or _infer_cat(t)
        print(f"  {i}. {t['content']}{' ' * pad}{cat}  #{tid}")
    print(f"  {len(filtered) + 1}. [skip]")
    print(f"\nPick [1-{len(filtered) + 1}], or s<N> to push to bottom:")


if __name__ == "__main__":
    main()
