#!/usr/bin/env python3
"""group_stall.py: raise one card when task groups have not moved in 7 days.

A task group is a Todoist parent labelled `grp` with subtasks
(lib/dtd_groups.py). Under a queue where demand always exceeds time, an
outcome nobody touched for a week is not late, it is outranked, and the fix is
a decision, not a reminder: re-value it, hand more of it to an agent, or drop
it. This script finds those outcomes and keeps ONE card listing them.

Stalled = open, not recurring, not labelled `waiting`, due today or earlier,
and nothing happened to the parent for STALL_DAYS: the later of its due date
and its `updated_at`. Paying an input rewrites the parent's [N], and a defer
moves its due date, so both reset the clock. A deferred parent is never
stalled while it is hidden.

One card, `😈 stalled groups ...`, not one per outcome: a per-outcome card
would repeat the outcome's words and steal its name match in /did. Its
description lists the outcomes with links. While the card is open each run
refreshes the list. Once JM closes it, an outcome listed on it is left alone
for another STALL_DAYS (state: ~/.local/state/jm/group-stall.json), so
"keep as is" is a valid answer.

Runs daily from Ix cron (config/ix.crontab). `--dry-run` prints and writes
nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
import daytime  # noqa: E402
import dtd_groups  # noqa: E402
import fen_split  # noqa: E402
import state_paths  # noqa: E402
import todoist  # noqa: E402

STALL_DAYS = 7
CARD_KEY = "stalled groups"
# No comma or semicolon: /did splits typed items on them.
CARD_CONTENT = "😈 stalled groups: re-value / delegate / drop (5) [5]"
CARD_LABELS = ["g245"]
STATE = state_paths.STATE_DIR / "group-stall.json"
TASK_URL = "https://app.todoist.com/app/task/{}"


def _day(stamp: str | None) -> date | None:
    try:
        return datetime.fromisoformat((stamp or "")[:10]).date()
    except ValueError:
        return None


def last_touch(task: dict) -> date | None:
    """The later of the parent's due date and its last update."""
    due = _day((task.get("due") or {}).get("date"))
    upd = _day(task.get("updated_at")) or _day(task.get("added_at"))
    days = [d for d in (due, upd) if d]
    return max(days) if days else None


def stalled(tasks: list, today: date) -> list:
    """[(task, idle days)] for group parents that have not moved, most idle
    first."""
    out = []
    for t in tasks:
        due = t.get("due") or {}
        labels = t.get("labels") or []
        if (t.get("checked") or due.get("is_recurring") or t.get("parent_id")
                or dtd_groups.GROUP_LABEL not in labels
                or dtd_groups.WAITING_LABEL in labels):
            continue
        due_day = _day(due.get("date"))
        if not due_day or due_day > today:
            continue                      # undated or deferred: not in the queue
        last = last_touch(t)
        if last and (today - last).days >= STALL_DAYS:
            out.append((t, (today - last).days))
    return sorted(out, key=lambda x: -x[1])


def describe(items: list, today: date) -> str:
    lines = [f"No input moved for {STALL_DAYS}+ days ({today.isoformat()}). For each: "
             "re-value it, hand more of it to an agent (/grp), or drop it.", ""]
    for t, idle in items:
        name = fen_split.strip_points(t.get("content") or "").strip()
        left = fen_split.total_points(t.get("content") or "")
        lines.append(f"- [{name}]({TASK_URL.format(t['id'])}): {idle} days"
                     + (f", [{left}] left" if left is not None else ""))
    return "\n".join(lines)[:15000]


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def run(today: date, *, find=None, request=None, state: dict | None = None,
        dry_run: bool = False) -> dict:
    find = find or todoist.find_tasks
    request = request or todoist._request
    state = _load_state() if state is None else state
    items = stalled(find(labels=[dtd_groups.GROUP_LABEL], limit=200), today)
    card = next((t for t in find(content_contains=CARD_KEY, limit=20)
                 if not t.get("parent_id")), None)
    out = {"stalled": [t["id"] for t, _ in items], "action": "none", "state": state}
    if card:
        desc = describe(items, today) if items else f"all clear {today.isoformat()}"
        if not dry_run:
            request("POST", f"/tasks/{card['id']}", {"description": desc})
            for t, _ in items:
                state[str(t["id"])] = today.isoformat()
        out["action"] = "updated"
        return out
    # No open card: JM closed the last one (or there never was one). Raise a
    # new card only for outcomes he has not already answered for this week.
    cutoff = today - timedelta(days=STALL_DAYS)
    fresh = [(t, idle) for t, idle in items
             if (_day(state.get(str(t["id"]))) or date.min) <= cutoff]
    if not fresh:
        return out
    if not dry_run:
        request("POST", "/tasks", {"content": CARD_CONTENT, "labels": CARD_LABELS,
                                   "due_string": "today", "description": describe(fresh, today),
                                   "duration": 5, "duration_unit": "minute"})
        for t, _ in fresh:
            state[str(t["id"])] = today.isoformat()
    out.update(action="created", raised=[t["id"] for t, _ in fresh])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    res = run(daytime.today(), dry_run=a.dry_run)
    if not a.dry_run:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(res["state"], indent=2) + "\n")
        tmp.replace(STATE)
    print(f"{datetime.now().isoformat(timespec='seconds')} group_stall: "
          f"{len(res['stalled'])} stalled, card {res['action']}"
          + (" (dry run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
