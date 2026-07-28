#!/usr/bin/env python3
"""quick-close.py <task_id> <cache_json> — immediate Todoist close for a
NON-recurring task at dtd ⌃⏎ time.

The dtd FIFO worker is strictly serial and each completion runs the full
did-fast pipeline (Excel writes over ssh, commonly 5-45s) with the Todoist
close LAST — so a burst of completions leaves the later cards open in
Todoist for minutes (user report 2026-07-28: "player retention still in
todoist but not in dtd ... too long of a wait"). This fires the close the
moment ⌃⏎ lands; did-fast's own close later is idempotent (its verify
treats 404/already-closed as closed).

RECURRING tasks are deliberately skipped: closing advances the recurrence,
and did-fast's later close would advance it AGAIN (the 2026-06-27
due-date-drift class); their close stays inside did-fast's guarded
pipeline. Ids not found in the session cache are also skipped — only close
what is provably a non-recurring one-off. Best-effort by design: any
failure here is silently absorbed because the pipeline close is the
reliable path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
import todoist  # noqa: E402


def cache_task(data: dict, tid: str) -> dict | None:
    for v in data.values():
        if not isinstance(v, list):
            continue
        for t in v:
            if isinstance(t, dict) and str(t.get("id")) == tid:
                return t
    return None


def should_close(data: dict, tid: str) -> bool:
    """Close only a task PRESENT in the cache and NOT recurring."""
    t = cache_task(data, tid)
    return bool(t) and not t.get("recurring")


def main() -> int:
    if len(sys.argv) < 3:
        return 0
    tid, cache_path = str(sys.argv[1]), sys.argv[2]
    try:
        data = json.loads(Path(cache_path).read_text())
    except Exception:
        return 0
    if not should_close(data, tid):
        return 0
    try:
        todoist.close_task(tid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
