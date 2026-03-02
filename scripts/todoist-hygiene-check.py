#!/usr/bin/env python3
"""
Check Todoist tasks for missing hygiene fields.

Reports tasks missing:
- Labels (tags)
- Duration (time estimate)
- 分 estimate (N) in content
- Due date
"""

import os
import sys
import re

try:
    import requests
except ImportError:
    print("Error: requests library not found. Install with: pip install requests")
    sys.exit(1)

TODOIST_API_KEY = os.environ.get("TODOIST_API_KEY")
if not TODOIST_API_KEY:
    print("Error: TODOIST_API_KEY environment variable not set")
    sys.exit(1)

TODOIST_API_BASE = "https://api.todoist.com/rest/v2"
HEADERS = {
    "Authorization": f"Bearer {TODOIST_API_KEY}",
    "Content-Type": "application/json"
}


def get_all_tasks():
    """Get all active tasks from Todoist."""
    response = requests.get(f"{TODOIST_API_BASE}/tasks", headers=HEADERS)
    response.raise_for_status()
    return response.json()


def has_fen_estimate(content):
    """Check if task content has (N) time estimate."""
    return bool(re.search(r'\(\d+\)', content))


def check_tasks():
    """Check all tasks for missing hygiene fields."""
    tasks = get_all_tasks()

    violations = {
        "missing_labels": [],
        "missing_duration": [],
        "missing_fen": [],
        "missing_due": [],
    }

    for task in tasks:
        content = task["content"]
        task_info = f"  - {content}"

        if not task.get("labels"):
            violations["missing_labels"].append(task_info)

        if not task.get("duration"):
            violations["missing_duration"].append(task_info)

        if not has_fen_estimate(content):
            violations["missing_fen"].append(task_info)

        if not task.get("due"):
            violations["missing_due"].append(task_info)

    # Print report
    total_tasks = len(tasks)
    has_violations = False

    print(f"Checked {total_tasks} tasks\n")

    for label, desc in [
        ("missing_labels", "Missing labels (tags)"),
        ("missing_duration", "Missing duration (time)"),
        ("missing_fen", "Missing 分 estimate (N)"),
        ("missing_due", "Missing due date"),
    ]:
        count = len(violations[label])
        if count > 0:
            has_violations = True
            print(f"{desc}: {count}")
            for task_info in violations[label]:
                print(task_info)
            print()

    if not has_violations:
        print("All tasks pass hygiene checks.")

    return has_violations


if __name__ == "__main__":
    try:
        has_violations = check_tasks()
        sys.exit(1 if has_violations else 0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
