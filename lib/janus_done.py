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

import math
import os
import re
from typing import Callable

TASK, TIME, CREDITED, ASK = "task", "time", "credited", "ask"

# Block attribution (JM 2026-11-01): a finished entry that crosses a 地支
# block boundary credits the block it ENDED in. A per-minute entry worth more
# than SPLIT_MIN_POINTS is split at each boundary instead: one Toggl entry
# per block, same name, each block credited its own minutes. did-fast owns
# the split (infer_range_blocks / split_results_by_block); the clients only
# need to hand it the HHMM-HHMM range, and the --rate flag when the points
# came from a typed rate (".5/m"), so a rate-priced total still splits.
SPLIT_MIN_POINTS = 100

# Largest single credit taken without explicit points ([N], {N}, +N, a typed
# answer). Above it, inferred minutes-as-points is a parse slip or a forgotten
# timer: did-fast refuses it (result step "refused"), and both janus clients
# surface the refusal instead of logging. 2026-10-22: a year in task text
# ("... readjustment 2028") was credited as +2028 分.
MAX_INFERRED_POINTS = 500


# did-fast's own watchdog (default 120s, sized for dtd's worker) exits it mid-run,
# whatever the caller's timeout says. Janus runs it off the UI, so a slow Excel
# may take longer: killing a run after the 分 land leaves the rest undone
# (2026-11-05: 19分 credited, row blank, entry duplicated). The caller's timeout
# stays just above the watchdog so did-fast is the one that gives up.
DID_WATCHDOG_SECS = 300
DID_TIMEOUT_SECS = DID_WATCHDOG_SECS + 10


def did_env() -> dict:
    """Environment for a did-fast run from a janus client."""
    return {**os.environ, "DIDFAST_WATCHDOG_SECS": str(DID_WATCHDOG_SECS)}


def refusal(did_fast_output: dict | None) -> str | None:
    """The reason text when did-fast refused an item (step "refused"), else
    None. Both janus clients treat a refusal as a failed log."""
    for r in (did_fast_output or {}).get("results") or []:
        if r.get("step") == "refused":
            return r.get("reason") or f"{r.get('name', '?')}: refused"
    return None


def classify(*, task_points: int | None, time_based: bool, crossed_off: bool) -> str:
    if task_points is not None:
        return TASK
    if time_based:
        return TIME
    if crossed_off:
        return CREDITED
    return ASK


def crossed_off(completed: dict, desc: str, norm: Callable[[str], str]) -> bool:
    """`desc` was crossed off today: a completed-today name (`completed`, the
    already date-checked file) that CLOSED A TODOIST TASK, i.e. has an entry
    in its "ids" map. A name-only completion is an earlier entry's points
    log (an "ask"-priced janus /done records no task id), so a second
    same-name session still asks instead of reading as "credited" (JM
    2026-11-01: three interview prep entries, only the first earned 分)."""
    target = norm(desc or "")
    if not target:
        return False
    ids = {norm(k) for k, v in (completed.get("ids") or {}).items() if v}
    return target in ids and any(norm(n) == target for n in completed.get("names") or [])


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


def is_rate_answer(text) -> bool:
    """True when the points prompt was answered with a per-minute rate
    (".5/m", "2/min") rather than a total. Clients pass did-fast --rate for
    these so a long entry still splits per block (SPLIT_MIN_POINTS)."""
    return bool(_RATE_ANSWER_RE.match(str(text or "").strip()))


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


# ── ^G points correction (JM 2026-10-28) ──────────────────────────────────
_ANNOT_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")


def entry_blocks(start, end) -> list[str]:
    """The 地支 blocks an entry touches, in order (one per block boundary
    crossed; a zero-length entry still names its start block)."""
    import datetime as _dt
    import neon_blocks as nb
    out: list[str] = []
    t = start
    while t < end:
        b = nb.current_block(t.hour)
        if not out or out[-1] != b:
            out.append(b)
        t += _dt.timedelta(minutes=1)
    return out or [nb.current_block(start.hour)]


def entry_block(start, end) -> str:
    """The 地支 block an entry ENDED in (the block of its last minute), the
    same rule as did-fast's infer_range_blocks. JM 2026-11-01: "if they span
    a block boundary, mark the points in the block that the task ended"
    (before that, the block holding most of the minutes)."""
    return entry_blocks(start, end)[-1]


def adjust_cmd(desc: str, code: str, delta: int, start, end, past_day: bool) -> str:
    """did-fast 分-log line for a points correction: the signed difference to
    the entry's domain, noted "<desc> adj", in the entry's own block. A past
    day also carries its M/D after the block (callers add --past-ok): did-fast
    credits a past day's block too (JM 2026-11-02)."""
    note = re.sub(r"[\[\]{}()+@,;，；]|\b\d{4}-\d{4}\b", " ", _ANNOT_RE.sub(" ", desc or ""))
    note = " ".join(note.split()) or "entry"
    cmd = f"{delta:+d} {note} adj @{code} {entry_block(start, end)}"
    return cmd + (f" {start.month}/{start.day}" if past_day else "")


def did_points(result: dict) -> int:
    """分 one did-fast result credited: its 0分 points + bonus + a split's
    other columns + hcbi-routed points, else a variable 1n+ value. The sum
    janus shows after a log and records against the entry."""
    fen = result.get("0fen") or {}
    pts = (fen.get("points") or 0) + (fen.get("bonus") or 0)
    pts += sum((x.get("points") or 0) for x in (fen.get("extra") or []))
    pts += (result.get("hcbi") or {}).get("pts", 0) or 0
    if not fen and result.get("step") == "0n":
        # A per-minute 0n habit (o314: 1分/min) is credited by the sheet
        # formula, so did-fast reports no 0分 points for it; price the
        # minutes it wrote (bug 2026-11-03: a 69m o314 showed [0]).
        from neon.rates import zero_n_points
        pts += zero_n_points(result.get("name") or "", result.get("value") or 0) or 0
    if not pts and result.get("variable_1n"):
        pts = result.get("variable_value") or 0
    return int(math.floor(pts + 0.5))   # half points (0.5/m bonuses) round up


def entry_points_by_id(did_fast_output: dict | None, fallback_ids) -> dict[str, int]:
    """{Toggl id: 分} for a did-fast run on a finished entry. A ranged result
    carries the Toggl entry did-fast recreated (one per block segment), and
    its points go to that id. Points from results with no Toggl entry, like
    habit-tag extras, go to the first recreated id, or to `fallback_ids[0]`
    (the row's own entry) when nothing was recreated."""
    out: dict[str, int] = {}
    loose = 0
    for r in (did_fast_output or {}).get("results") or []:
        if r.get("step") in ("refused", "skipped"):
            continue
        pts = did_points(r)
        ids = (r.get("toggl") or {}).get("ids") or []
        if (r.get("toggl") or {}).get("ok") and ids:
            out[str(ids[0])] = out.get(str(ids[0]), 0) + pts
        else:
            loose += pts
    if loose or not out:
        target = next(iter(out), None) or (str(fallback_ids[0]) if fallback_ids else None)
        if target is not None:
            out[target] = out.get(target, 0) + loose
    return out
