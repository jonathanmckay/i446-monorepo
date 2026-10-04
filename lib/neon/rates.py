"""1n+ row-5 "expected points" cells.

Most habits hold a plain number there (points per completion). Variable
habits hold a RATE string instead: "1/m", ".5/m", "15+1/m" (base + rate per
minute). A rate is text, so a 0分 formula must never reference that cell
(bug 2026-10-03: "+'1n+'!AF5" for family broke the 0分 sum); the points are
computed from the minutes instead.
"""
from __future__ import annotations

import re

_RATE_RE = re.compile(r"^\s*(?:(\d+(?:\.\d+)?)\s*\+\s*)?(\d*\.?\d+)\s*/\s*m\s*$", re.IGNORECASE)


def parse_rate(cell) -> tuple[float, float] | None:
    """(base, per_minute) for a rate string like "15+1/m" or ".5/m"; None
    when the cell is a plain number, blank, or anything else."""
    if cell is None:
        return None
    m = _RATE_RE.match(str(cell))
    if not m:
        return None
    return float(m.group(1) or 0), float(m.group(2))


def rate_points(cell, minutes: float) -> int | None:
    """base + per_minute × minutes, rounded; None when `cell` is not a rate."""
    r = parse_rate(cell)
    if r is None:
        return None
    base, per = r
    return int(round(base + per * minutes))


# 0n habits whose minutes Neon credits at a multiple (minutes → 分). The
# multiplier lives in a sheet formula, not row 5, so it is mirrored here for
# display only (writes still go through 0n and let the sheet do the math).
#   hiit: hcbi!Y = '0n'!AE*2 + 40  (the +40 is a flat daily hcbp base)
ZERO_N_MULTIPLIERS: dict[str, int] = {"hiit": 2}


def zero_n_points(habit: str, minutes: float) -> int | None:
    """分 Neon credits for `minutes` of a multiplied 0n habit, else None."""
    mult = ZERO_N_MULTIPLIERS.get((habit or "").strip().lower())
    return None if mult is None else int(round(mult * minutes))
