#!/usr/bin/env python3
"""reject.py <task_id> [note] — JM rejects an agent pick (dtd alt-n).

Asks for a one-line note on the tty when none is given, then: logs `reject`
(with review time, for calibration), marks the receipt rejected so a later
close by JM is not counted as an accept, drops the `agent` label, comments the
note on the task, clears the ✅ claim, and deletes the review branch. The task
itself stays open: JM does it, defers it, or deletes it as usual.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "lib"))
import picks  # noqa: E402


def reject(task_id: str, note: str) -> bool:
    if picks.status(task_id) != "ready":
        print(f"{task_id}: no ready agent receipt")
        return False
    evs = picks.events()
    meta = picks._pick_meta(evs, task_id)
    picks.log("reject", task_id, **{"class": meta.get("class"), "minutes": meta.get("minutes"),
                                    "value": meta.get("value"), "note": note,
                                    "review_sec": picks.review_seconds(evs, task_id, int(time.time()))})
    picks.set_status(task_id, "rejected")
    try:
        import todoist
        t = todoist.get_task(task_id) or {}
        labels = [l for l in (t.get("labels") or []) if l != "agent"]
        todoist._request("POST", f"/tasks/{task_id}", {"labels": labels})
        todoist.add_comment(task_id, f"😈 rejected: {note or '(no note)'}")
    except Exception as e:  # noqa: BLE001
        print(f"todoist update failed: {e}")
    spec = importlib.util.spec_from_file_location("agent_claims", HERE.parent / "did" / "agent_claims.py")
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    ac.clear_task(task_id)
    br = picks.branch(task_id)
    if br:
        subprocess.run(["git", "-C", str(picks.REPO), "push", "-q", "origin", "--delete", br],
                       capture_output=True)
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: reject.py <task_id> [note]")
        return 2
    tid = sys.argv[1]
    note = " ".join(sys.argv[2:]).strip()
    if not note and sys.stdin.isatty():
        try:
            note = input("reject note (what was wrong): ").strip()
        except EOFError:
            note = ""
    return 0 if reject(tid, note) else 1


if __name__ == "__main__":
    sys.exit(main())
