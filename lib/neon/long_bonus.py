"""Long-session bonus: a day's o314 minutes earn a weekly 长o314 bonus.

JM 2026-11-03: o314 is 1分/min (the 0n sheet formula already credits that).
When the DAY's o314 total reaches 30 min, every one of the day's minutes
earns +0.5分 more (30m → +15), credited to the weekly habit 长o314: the
minutes go into its 1n+ week cell (1n+ cells hold minutes) and the points
into 0分 as a literal.

The bonus is computed from the 0n cell's value before and after a write, so
it needs no state of its own: 20m then 15m credits nothing, then 35m worth;
re-sending the same total credits 0; a correction downward credits negative.
"""
from __future__ import annotations

import math

# 0n habit → (weekly 1n+ habit header, threshold minutes, 分 per minute)
LONG_BONUS: dict[str, tuple[str, int, float]] = {
    "o314": ("长o314", 30, 0.5),
}


def _num(v) -> float:
    try:
        s = str(v).strip()
        return 0.0 if s in ("", "missing value") else float(s)
    except (TypeError, ValueError):
        return 0.0


def qualifying(minutes: float, threshold: int) -> float:
    """Minutes that earn the bonus for a day total of `minutes`."""
    return minutes if minutes >= threshold else 0.0


def _half_up(x: float) -> float:
    """Round to the nearest 0.5 (0分 takes half points), halves away from 0."""
    return math.copysign(math.floor(abs(x) * 2 + 0.5) / 2, x)


def bonus_delta(habit: str, before, after) -> tuple[float, float] | None:
    """(Δ qualifying minutes, Δ 分) for one write that moved the habit's day
    total from `before` to `after`; None when the habit has no bonus or
    nothing changed."""
    spec = LONG_BONUS.get((habit or "").strip().lower())
    if spec is None:
        return None
    _, threshold, rate = spec
    qb, qa = qualifying(_num(before), threshold), qualifying(_num(after), threshold)
    dm = qa - qb
    if dm == 0:
        return None
    return dm, _half_up(rate * qa) - _half_up(rate * qb)


def weekly_header(habit: str) -> str | None:
    spec = LONG_BONUS.get((habit or "").strip().lower())
    return spec[0] if spec else None
