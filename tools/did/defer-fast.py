#!/usr/bin/env python3
"""defer-fast.py — Fast Todoist task deferral.

Finds a task by substring, defers it to a target date, and creates
posthoc eval stubs. Handles both recurring and non-recurring tasks.

Usage:
    python3 defer-fast.py "<task_name>" [target_date] [claimed_points]

    task_name      — substring to search for in Todoist (required)
    target_date    — ISO date YYYY-MM-DD (default: tomorrow)
    claimed_points — points for today's stub (default: 5)
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Constants (same as did-fast.py)
# ---------------------------------------------------------------------------

TODOIST_TOKEN = "7eb82f47aba8b334769351368e4e3e3284f980e5"
TODOIST_BASE = "https://api.todoist.com/api/v1"

POINTS_RE = re.compile(r"\[(\d+)\]")
DURATION_RE = re.compile(r"\((\d+)\)")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body: dict | None = None,
         timeout: float = 15.0) -> dict | None:
    """Make a Todoist API request. Returns parsed JSON or None for 204."""
    url = f"{TODOIST_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {TODOIST_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw)


def _fetch_tasks(filt: str) -> list[dict]:
    """Fetch tasks with a Todoist filter, paginating if needed."""
    tasks: list[dict] = []
    cursor = None
    for _ in range(5):
        url = f"{TODOIST_BASE}/tasks?filter={quote(filt)}&limit=200"
        if cursor:
            url += f"&cursor={cursor}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {TODOIST_TOKEN}",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        batch = raw if isinstance(raw, list) else raw.get("results", [])
        tasks.extend(batch)
        cursor = raw.get("next_cursor") if isinstance(raw, dict) else None
        if not cursor:
            break
    return tasks


# ---------------------------------------------------------------------------
# Task search
# ---------------------------------------------------------------------------

def find_task(query: str) -> dict:
    """Search for a single task matching query. Tries progressively wider filters.

    Returns the full task dict. Exits on 0 or multiple matches.
    """
    query_lower = query.lower()

    for filt in ("today | overdue", "7 days", "all"):
        try:
            tasks = _fetch_tasks(filt)
        except Exception as e:
            print(f"WARN: fetch filter '{filt}' failed: {e}", file=sys.stderr)
            continue

        matches = [t for t in tasks
                   if query_lower in t.get("content", "").lower()]

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(json.dumps({
                "error": "multiple matches",
                "matches": [{"id": m["id"], "content": m["content"]}
                            for m in matches],
            }))
            sys.exit(1)

    print(json.dumps({"error": "task not found"}))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Create task helper
# ---------------------------------------------------------------------------

def create_task(content: str, labels: list[str], project_id: str | None,
                due_date: str, priority: int | None = None) -> dict:
    """Create a Todoist task and return the response."""
    body: dict = {
        "content": content,
        "labels": labels,
        "due_date": due_date,
    }
    if project_id:
        body["project_id"] = project_id
    if priority is not None:
        body["priority"] = priority
    return _api("POST", "/tasks", body)


def close_task(task_id: str) -> None:
    """Close (complete) a Todoist task."""
    _api("POST", f"/tasks/{task_id}/close")


# ---------------------------------------------------------------------------
# Non-recurring flow
# ---------------------------------------------------------------------------

def handle_non_recurring(task: dict, target_date: str,
                         claimed_points: int) -> dict:
    """Reschedule a non-recurring task and create a posthoc eval record."""
    task_id = task["id"]
    content = task["content"]
    labels = task.get("labels", [])
    project_id = task.get("project_id")

    # 1. Reschedule original task
    _api("POST", f"/tasks/{task_id}", {"due_date": target_date})

    # 2. Parse total points
    pts_match = POINTS_RE.search(content)
    total_points = int(pts_match.group(1)) if pts_match else 0

    # 3. Create posthoc eval record (due today, immediately closed)
    today_iso = date.today().isoformat()
    posthoc_content = f"deferred: {content} \u2192 {target_date} ({claimed_points}) [0]"
    posthoc_labels = list(set(["posthoc"] + labels))

    posthoc = create_task(posthoc_content, posthoc_labels, project_id, today_iso)
    close_task(posthoc["id"])

    return {
        "task": content,
        "recurring": False,
        "target_date": target_date,
        "claimed_points": claimed_points,
        "remaining_points": total_points,
        "closed": False,
        "stubs": {"today": posthoc["id"], "future": None},
    }


# ---------------------------------------------------------------------------
# Recurring flow
# ---------------------------------------------------------------------------

def handle_recurring(task: dict, target_date: str,
                     claimed_points: int) -> dict:
    """Close a recurring task, create today stub + future stub."""
    task_id = task["id"]
    content = task["content"]
    labels = task.get("labels", [])
    project_id = task.get("project_id")
    priority = task.get("priority")

    # 1. Parse total points from [N]
    pts_match = POINTS_RE.search(content)
    total_points = int(pts_match.group(1)) if pts_match else 0
    remaining = max(0, total_points - claimed_points)

    # 2. Close the recurring task (advances recurrence)
    close_task(task_id)

    # 3. Stub A: today, completed
    today_iso = date.today().isoformat()
    stub_a_content = f"deferred: {content} ({claimed_points}) [{claimed_points}]"
    stub_a_labels = list(set(["posthoc"] + labels))
    stub_a = create_task(stub_a_content, stub_a_labels, project_id, today_iso)
    close_task(stub_a["id"])

    # 4. Stub B: future, open, with updated [N]
    stub_b_content = re.sub(r"\[\d+\]", f"[{remaining}]", content)
    if not POINTS_RE.search(stub_b_content):
        # Original had no [N]; append it
        stub_b_content += f" [{remaining}]"

    stub_b = create_task(stub_b_content, labels, project_id, target_date,
                         priority=priority)

    return {
        "task": content,
        "recurring": True,
        "target_date": target_date,
        "claimed_points": claimed_points,
        "remaining_points": remaining,
        "closed": True,
        "stubs": {"today": stub_a["id"], "future": stub_b["id"]},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("usage: defer-fast.py <task_name> [target_date] [claimed_points]",
              file=sys.stderr)
        sys.exit(1)

    task_name = sys.argv[1]
    target_date = (sys.argv[2] if len(sys.argv) > 2
                   else (date.today() + timedelta(days=1)).isoformat())
    claimed_points = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    # Find the task
    task = find_task(task_name)

    # Route by recurrence
    due = task.get("due") or {}
    is_recurring = due.get("is_recurring", False)

    try:
        if is_recurring:
            result = handle_recurring(task, target_date, claimed_points)
        else:
            result = handle_non_recurring(task, target_date, claimed_points)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
