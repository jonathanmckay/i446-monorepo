#!/usr/bin/env python3
"""defer-fast.py — Fast Todoist task deferral.

Finds a task by substring, defers it to a target date, and creates
posthoc eval stubs. Handles both recurring and non-recurring tasks.

Usage:
    python3 defer-fast.py "<task_name>" [days|YYYY-MM-DD] [claimed_points]

    task_name      — substring to search for in Todoist (required)
    days|date      — bare integer N → today + N days; ISO date → that absolute
                     date; omitted → today + 1 day (the default defer)
    claimed_points — points for today's posthoc stub (default: 2)

For a recurring task this defers only the current occurrence: a one-off copy
is created on the target date and the recurring parent advances to its own next
scheduled occurrence (recurrence preserved, cadence unchanged).
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

DEFAULT_CLAIMED_POINTS = 2

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _add_months(d: date, n: int) -> date:
    """Add n months, clamping the day to the target month's length."""
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    # Clamp day (e.g. Jan 31 + 1 month → Feb 28)
    for day in range(d.day, 27, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, min(d.day, 28))


def next_instance(due_string: str, current_due: date) -> date:
    """Next occurrence of a Todoist recurrence strictly after current_due.

    Parses the common patterns used in this vault (every day, every Friday,
    every N days/weeks/months, every other day/week, every weekday, weekday
    lists, monthly, yearly). Unknown patterns fall back to +7 days.
    """
    s = (due_string or "").lower()

    if "every other week" in s:
        return current_due + timedelta(days=14)
    if "every other day" in s:
        return current_due + timedelta(days=2)
    m = re.search(r"every (\d+) days?", s)
    if m:
        return current_due + timedelta(days=int(m.group(1)))
    m = re.search(r"every (\d+) weeks?", s)
    if m:
        return current_due + timedelta(weeks=int(m.group(1)))
    m = re.search(r"every (\d+) months?", s)
    if m:
        return _add_months(current_due, int(m.group(1)))
    if re.search(r"\bevery (day|morning|afternoon|evening|night)\b", s) or "daily" in s:
        return current_due + timedelta(days=1)
    if "every weekday" in s or "every workday" in s:
        d = current_due + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    # Weekday names ("every friday", "every mon, wed, fri")
    days = sorted({v for k, v in WEEKDAYS.items() if re.search(rf"\b{k}\b", s)})
    if days:
        d = current_due + timedelta(days=1)
        while d.weekday() not in days:
            d += timedelta(days=1)
        return d
    if re.search(r"\bevery week\b", s) or "weekly" in s:
        return current_due + timedelta(days=7)
    # "every month", "every 15th", "every last day"
    if "every month" in s or "monthly" in s or re.search(r"every \d+(st|nd|rd|th)", s):
        return _add_months(current_due, 1)
    if "every year" in s or "yearly" in s or "annually" in s:
        return _add_months(current_due, 12)
    return current_due + timedelta(days=7)


def resolve_target(arg: str | None) -> str:
    """Resolve the defer target to an ISO date.

    - missing/empty      → today + 1 day (the default defer)
    - bare integer "N"   → today + N days
    - ISO "YYYY-MM-DD"    → that absolute date (passed through)
    - anything else       → passed through unchanged (caller already resolved
                            natural language like "next Monday" to ISO)

    Note: for a recurring task this is the due date of the deferred one-off
    copy; the parent always advances to its own next occurrence regardless.
    """
    today = date.today()
    a = (arg or "").strip()
    if not a:
        return (today + timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{1,3}", a):
        return (today + timedelta(days=int(a))).isoformat()
    return a


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
            # Prefer an exact content match — dtd passes the full row content
            # so duplicate names differing only in annotations resolve cleanly
            exact = [m for m in matches
                     if m.get("content", "").lower().strip() == query_lower.strip()]
            if len(exact) == 1:
                return exact[0]
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
        "task_id": task_id,
        "recurring": False,
        "target_date": target_date,
        "prev_due": (task.get("due") or {}).get("date", ""),
        "prev_due_string": (task.get("due") or {}).get("string", "") or "",
        "claimed_points": claimed_points,
        "remaining_points": total_points,
        "closed": False,
        "stubs": {"today": posthoc["id"], "future": None},
    }


# ---------------------------------------------------------------------------
# Recurring flow
# ---------------------------------------------------------------------------

_ANCHOR_RE = re.compile(r"\s+(starting|start|from|beginning|begins?|since)\b.*$", re.I)


def _recurrence_pattern(due_string: str) -> str:
    """Strip any 'starting <date>' / 'from <date>' anchor off a recurrence
    string, leaving the bare cadence ('every day', 'every friday', ...).

    Used so the parent can be re-anchored to a specific next date via due_date
    without the old anchor fighting it.
    """
    return _ANCHOR_RE.sub("", due_string or "").strip()


def handle_recurring(task: dict, target_date: str,
                     claimed_points: int) -> dict:
    """Defer the *current occurrence* of a recurring task without disturbing
    the series.

    - Creates a standalone (non-recurring) one-off copy of this occurrence due
      on target_date — that's the deferred instance the user will actually do.
    - Advances the recurring parent to its next scheduled occurrence, preserving
      the recurrence pattern (passing due_string keeps is_recurring true; a bare
      due_date write would silently strip the recurrence). The parent's cadence
      is unchanged — it just sheds the occurrence the copy now carries.
    - Logs a posthoc eval record for today.
    """
    task_id = task["id"]
    content = task["content"]
    labels = task.get("labels", [])
    project_id = task.get("project_id")
    priority = task.get("priority")
    due = task.get("due") or {}

    # Parse total points from [N] (for reporting only)
    pts_match = POINTS_RE.search(content)
    total_points = int(pts_match.group(1)) if pts_match else 0

    # Next natural occurrence of the series (from today or the current due,
    # whichever is later, so an overdue parent still lands in the future).
    pattern = _recurrence_pattern(due.get("string", "")) or due.get("string", "")
    current_due = (date.fromisoformat(str(due["date"])[:10])
                   if due.get("date") else date.today())
    base = max(current_due, date.today())
    next_date = next_instance(due.get("string", ""), base).isoformat()

    # 1. One-off deferred copy of THIS occurrence (non-recurring), due target.
    copy = create_task(content, labels, project_id, target_date, priority)

    # 2. Advance the parent to its next occurrence, recurrence preserved.
    body = {"due_date": next_date}
    if pattern:
        body["due_string"] = pattern
    _api("POST", f"/tasks/{task_id}", body)

    # 3. Posthoc eval record (due today, immediately closed).
    today_iso = date.today().isoformat()
    posthoc_content = (f"deferred: {content} → {target_date} "
                       f"(recurring; next {next_date}) [0]")
    posthoc_labels = list(set(["posthoc"] + labels))
    posthoc = create_task(posthoc_content, posthoc_labels, project_id, today_iso)
    close_task(posthoc["id"])

    return {
        "task": content,
        "task_id": task_id,
        "recurring": True,
        "target_date": target_date,
        "next_recurrence": next_date,
        "prev_due": due.get("date", ""),
        "prev_due_string": due.get("string", "") or "",
        "claimed_points": claimed_points,
        "remaining_points": total_points,
        "closed": False,
        "stubs": {"today": posthoc["id"], "deferred_copy": copy["id"],
                  "future": task_id},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("usage: defer-fast.py <task_name> [days|YYYY-MM-DD] [claimed_points]",
              file=sys.stderr)
        sys.exit(1)

    task_name = sys.argv[1]
    explicit_target = sys.argv[2] if len(sys.argv) > 2 else None
    claimed_points = (int(sys.argv[3]) if len(sys.argv) > 3
                      else DEFAULT_CLAIMED_POINTS)

    # Find the task
    task = find_task(task_name)

    # Target = today+1 (default), today+N (bare integer), or an absolute date.
    target_date = resolve_target(explicit_target)

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
