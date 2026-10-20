#!/usr/bin/env python3
"""
Set default due dates and time estimates for Todoist tasks.

- Sets tasks without due dates to today
- Adds time estimates to tasks without them
"""

import os
import sys
import json
from datetime import datetime
import re

# Add the MCP Todoist client path (assuming it's available via npx)
# We'll use the Todoist API directly via requests

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

DEFAULT_DURATION = 30  # minutes
DEFAULT_FEN_ESTIMATE = 30  # 分 (minutes)


def get_all_tasks():
    """Get all active tasks from Todoist."""
    response = requests.get(f"{TODOIST_API_BASE}/tasks", headers=HEADERS)
    response.raise_for_status()
    return response.json()


def update_task(task_id, updates):
    """Update a task with the given changes."""
    response = requests.post(
        f"{TODOIST_API_BASE}/tasks/{task_id}",
        headers=HEADERS,
        json=updates
    )
    response.raise_for_status()
    return response.json()


def has_fen_estimate(content):
    """Check if task content has (N) time estimate."""
    return bool(re.search(r'\(\d+\)', content))


def add_fen_estimate(content, minutes):
    """Add (N) time estimate to task content."""
    # Add at the end if not present
    return f"{content} ({minutes})"


def process_tasks(dry_run=False):
    """Process all tasks and apply defaults."""
    tasks = get_all_tasks()

    stats = {
        "total": len(tasks),
        "updated_due_date": 0,
        "updated_duration": 0,
        "updated_fen_estimate": 0,
        "no_changes": 0
    }

    today = datetime.now().strftime("%Y-%m-%d")

    for task in tasks:
        task_id = task["id"]
        content = task["content"]
        updates = {}
        changes = []

        # Check due date
        if not task.get("due"):
            updates["due_string"] = "today"
            changes.append("due date → today")
            stats["updated_due_date"] += 1

        # Check duration
        if not task.get("duration"):
            updates["duration"] = DEFAULT_DURATION
            updates["duration_unit"] = "minute"
            changes.append(f"duration → {DEFAULT_DURATION}m")
            stats["updated_duration"] += 1

        # Check 分 estimate in content
        if not has_fen_estimate(content):
            updates["content"] = add_fen_estimate(content, DEFAULT_FEN_ESTIMATE)
            changes.append(f"分 estimate → ({DEFAULT_FEN_ESTIMATE})")
            stats["updated_fen_estimate"] += 1

        # Apply updates
        if updates:
            print(f"Task: {content}")
            print(f"  Changes: {', '.join(changes)}")

            if not dry_run:
                try:
                    update_task(task_id, updates)
                    print(f"  ✓ Updated")
                except Exception as e:
                    print(f"  ✗ Error: {e}")
            else:
                print(f"  (dry run - not applied)")
            print()
        else:
            stats["no_changes"] += 1

    # Print summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total tasks: {stats['total']}")
    print(f"Updated due dates: {stats['updated_due_date']}")
    print(f"Updated durations: {stats['updated_duration']}")
    print(f"Updated 分 estimates: {stats['updated_fen_estimate']}")
    print(f"No changes needed: {stats['no_changes']}")

    if dry_run:
        print("\n(This was a dry run - no changes were actually made)")

    return stats


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    print("Todoist Task Defaults Script")
    print("="*60)
    if dry_run:
        print("DRY RUN MODE - No changes will be made")
        print("="*60)
    print()

    try:
        process_tasks(dry_run=dry_run)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
