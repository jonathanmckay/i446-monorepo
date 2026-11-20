#!/usr/bin/env python3
"""One dtd card per day for writes the queue accepted and then could not land
(excel-http `lost_write`: an intent held or rejected on replay, after its
caller was told "queued" and closed the task). Each further loss the same day
is a comment on that card.

  queue_card.py "<detail>"

Spawned detached by the daemon; failures only reach stderr.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import os
import sys

CARD = "⚠ Neon queued write did not land"


def main() -> int:
    detail = " ".join(sys.argv[1:]).strip() or "(no detail)"
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))
    import todoist
    today = dt.date.today().isoformat()
    body = ("excel-http accepted this write as queued and then could not apply it. The task that "
            "asked for it is already closed, so the points or mark are missing from Excel and the "
            "ledger. Re-enter it (a 分 log or /did), or fix the cell and `sameday.py adopt` it:\n- "
            + detail)
    # One drain pass can lose several writes at once; each spawns this script.
    # Serialize find-then-create so they share one card instead of racing to
    # make one each. The card id is kept beside the lock because Todoist's
    # list lags a just-created task.
    state = os.path.expanduser("~/.cache/neon-rules/queue-card.txt")
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        day, _, tid = f.read().strip().partition(" ")
        if day != today or not tid:
            open_ = [t for t in todoist.find_tasks(labels=["i447"], content_contains=CARD)
                     if ((t.get("due") or {}).get("date") or "")[:10] == today]
            tid = open_[0]["id"] if open_ else todoist.create_task(
                f"{CARD} (5) [10]", labels=["i447"], due_string="today")["id"]
            f.seek(0); f.truncate(); f.write(f"{today} {tid}"); f.flush()
        todoist.add_comment(tid, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
