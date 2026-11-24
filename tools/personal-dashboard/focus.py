"""focus.py: how long a stretch of work lasts before it is interrupted.

From raw Toggl entries. A *run* is consecutive entries on the same work
project with the same description, where each starts within GAP_MIN of the
previous one ending and nothing else was tracked in between. Two measures per
day:

    longest   minutes of the longest run
    deep      work minutes that sit in runs of DEEP_MIN or longer
    work      all work minutes (the denominator)

Meetings count: Toggl cannot tell a meeting from solo work, so a 60-minute
call is a 60-minute run. Read the numbers as an upper bound on solo focus.
"""
from __future__ import annotations

from datetime import datetime

WORK_CODES = ("i9", "m5x2")
GAP_MIN = 5
DEEP_MIN = 25


def _parse(stamp):
    try:
        return datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def daily_focus(entries, tz, id_to_code, work_codes=WORK_CODES,
                gap_min=GAP_MIN, deep_min=DEEP_MIN) -> dict:
    """{date_str: {"longest": m, "deep": m, "work": m}} for days with any
    work entry. `entries` are raw Toggl time entries (start, duration seconds,
    project_id, description)."""
    rows = []
    for e in entries or []:
        dur = e.get("duration") or 0
        start = _parse(e.get("start"))
        if dur <= 0 or start is None:
            continue
        start = start.astimezone(tz)
        code = id_to_code.get(e.get("project_id"))
        rows.append((start, dur, code, (e.get("description") or "").strip().lower()))
    rows.sort(key=lambda r: r[0])

    out = {}
    run = None          # [day, code, desc, end_ts, seconds]

    def close():
        if run is None:
            return
        day = out.setdefault(run[0], {"longest": 0, "deep": 0, "work": 0})
        mins = run[4] // 60
        day["work"] += mins
        day["longest"] = max(day["longest"], mins)
        if mins >= deep_min:
            day["deep"] += mins

    for start, dur, code, desc in rows:
        day = start.date().isoformat()
        end_ts = start.timestamp() + dur
        joins = (run is not None and code in work_codes and run[0] == day
                 and run[1] == code and run[2] == desc
                 and start.timestamp() - run[3] <= gap_min * 60)
        if joins:
            run[3], run[4] = end_ts, run[4] + dur
            continue
        close()
        # Any other entry, work or not, ends the run in progress.
        run = [day, code, desc, end_ts, dur] if code in work_codes else None
    close()
    return out
