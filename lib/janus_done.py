"""How /done on a time entry earns 分 — the one rule set behind desktop janus
(tools/tg/janus.py) and mobile janus (tools/janus/mobile.py, which serves the
web UI and the iOS janus app). Change the decision here, never in one client
(JM 2026-10-28: "if I make changes to CLI the behavior stays the same across
iOS as well").

Paths (JM 2026-10-28):
  "task"     — an open Todoist task (or an inline) [N]: the task's points
  "time"     — a known variable habit or category (fen.md rates): points
               from the minutes, priced by Neon / did-fast
  "credited" — already crossed off today (e.g. in dtd) and not time-based:
               its task points are in, the entry adds nothing
  "ask"      — none of the above: ask for a total ("40") or a rate (".5/m")
Time-based beats "credited", so a repeatable habit (xk22, hiit) still
credits a second session's minutes.
"""
from __future__ import annotations

import re
from typing import Callable

TASK, TIME, CREDITED, ASK = "task", "time", "credited", "ask"


def classify(*, task_points: int | None, time_based: bool, crossed_off: bool) -> str:
    if task_points is not None:
        return TASK
    if time_based:
        return TIME
    if crossed_off:
        return CREDITED
    return ASK


def is_time_based(name: str, *, is_habit: Callable[[str], bool], df) -> bool:
    """`name` (normalized, no @code) is a 0n/1n+ habit (`is_habit`, each
    client's own header lookup) or one of did-fast's time-priced names:
    VARIABLE_0N or a VARIABLE_DOMAIN keyword (bball, walk, …), after
    did-fast's ALIASES ("hit" → hiit)."""
    name = (name or "").strip().lower()
    if not name:
        return False
    if is_habit(name):
        return True
    resolved = getattr(df, "ALIASES", {}).get(name, name)
    return (resolved in getattr(df, "VARIABLE_0N", ()) or resolved in getattr(df, "VARIABLE_DOMAIN", {})
            or (resolved != name and is_habit(resolved)))


_RATE_ANSWER_RE = re.compile(r"^(\d*\.?\d+)\s*/\s*m(?:in)?$", re.IGNORECASE)


def points_from_answer(text, minutes: int) -> int | None:
    """The points prompt's answer: "40" = 40 total, ".5/m" or "2/min" = a
    rate times the entry's minutes, blank = 0. None = unparseable."""
    text = str(text or "").strip()
    if not text:
        return 0
    m = _RATE_ANSWER_RE.match(text)
    if m:
        return int(round(float(m.group(1)) * max(int(minutes or 0), 0)))
    try:
        return int(text)
    except ValueError:
        return None
