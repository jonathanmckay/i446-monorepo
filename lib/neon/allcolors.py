"""All-colors (0n!AG ⎣∀clr) and handicap (0n!AH #) rule — pure logic.

A day D gets all colors when
  * the core three (0t, 0l, 0g) are done (non-blank, nonzero), and
  * misses <= handicap, where misses = blank cells among the other 0n habit
    columns (D:AS minus AF/AG/AH, 词汇 and the core three) plus one virtual
    habit "1n": every 1n+ card whose row-3 weekday is D's weekday is filled
    in its week row.
An explicit 0 is N/A — never a miss, never counted as done.
AG = HHMM of the earliest moment both held. AH(D+1) = AH(D)+1 when AG(D)
is blank, else max(0, AH(D)-1). JM 2026-11-03.

No I/O here; tools/allcolors/allcolors.py fetches cells and writes results.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Optional

DONE, NA, BLANK = "done", "na", "blank"

CORE = ("0t", "0l", "0g")
LATE_N_COLOR = 3000   # 0n!AF: 0t and 0l were done, but not both on the day
# 0n columns that are not habits (bookkeeping) or are excluded by JM
# ("词汇": retired, blank every day since 2026-10).
NON_HABIT = {"n color", "⎣∀clr", "#", "词汇"}


@dataclass(frozen=True)
class Cell:
    name: str
    status: str                          # DONE / NA / BLANK
    at: Optional[dt.datetime] = None     # when it became done; None = unknown


def classify_value(raw) -> str:
    """Status of a cell from its displayed value: blank, explicit 0 (N/A) or done."""
    s = "" if raw is None else str(raw).strip()
    if s in ("", "missing value"):
        return BLANK
    try:
        return NA if float(s) == 0 else DONE
    except ValueError:
        return DONE


def norm(name: str) -> str:
    return " ".join((name or "").replace("\xa0", " ").split()).lower()


def is_habit_header(name: str) -> bool:
    n = norm(name)
    return bool(n) and n not in NON_HABIT


def caught_up(cell: Cell, prev_raw, prev_at: Optional[dt.datetime],
              cycle_first: dt.date, day: dt.date) -> Cell:
    """A blank card whose last-week cell was written in this cycle by `day`
    filled last week instead of this one (neon/week_fill.py): the card was
    done this cycle, so it is not a miss."""
    if (classify_value(prev_raw) == DONE and prev_at is not None
            and cycle_first <= prev_at.date() <= day):
        return Cell(cell.name, DONE, prev_at)
    return cell


def onen_cell(cards: Iterable[Cell]) -> Cell:
    """Collapse the day's 1n+ cards into the single virtual '1n' habit: done
    when every applicable card is done (at = the last one), N/A when all are
    N/A (or there are none), else blank."""
    cards = list(cards)
    live = [c for c in cards if c.status != NA]
    if not live:
        return Cell("1n", NA)
    if any(c.status == BLANK for c in live):
        return Cell("1n", BLANK)
    ats = [c.at for c in live]
    return Cell("1n", DONE, None if any(a is None for a in ats) else max(ats))


def evaluate(day: dt.date, core: list[Cell], habits: list[Cell], handicap: int,
             unknown: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """When day `day` reached all colors, or None. Cells with unknown
    completion time count as done at `unknown` (default 23:59 — conservative
    for a closed day; the live path passes now)."""
    fallback = unknown or dt.datetime.combine(day, dt.time(23, 59))

    def when(c: Cell) -> dt.datetime:
        return c.at or fallback

    if len(core) != len(CORE) or any(c.status != DONE for c in core):
        return None
    applicable = [c for c in habits if c.status != NA]
    done = sorted((when(c) for c in applicable if c.status == DONE))
    need = max(0, len(applicable) - max(0, handicap))
    if len(done) < need:
        return None
    times = [when(c) for c in core]
    if need:
        times.append(done[need - 1])
    return max(times)


def n_color_time(zero_t: Cell, zero_l: Cell,
                 unknown: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """When 0n!AF (N color) was earned: the first moment 0t and 0l were both
    done, or None. An unknown time counts as `unknown` (default 23:59 of the
    other cell's day, as in evaluate). JM 2026-11-06."""
    if zero_t.status != DONE or zero_l.status != DONE:
        return None
    known = [c.at for c in (zero_t, zero_l) if c.at]
    if len(known) == 2:
        return max(known)
    if unknown:
        return unknown
    return dt.datetime.combine(known[0].date(), dt.time(23, 59)) if known else None


def next_handicap(prev_ah, prev_ag) -> int:
    """AH for the day after a day with handicap `prev_ah` and AG `prev_ag`."""
    try:
        ah = int(float(str(prev_ah).strip()))
    except (TypeError, ValueError):
        ah = 0
    got = classify_value(prev_ag) == DONE
    return max(0, ah - 1) if got else ah + 1


def hhmm(t: dt.datetime) -> int:
    return t.hour * 100 + t.minute
