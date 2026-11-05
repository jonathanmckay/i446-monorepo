"""Weekly 1n+ cards fill a blank last week before this one (JM 2026-11-05).

A same-day completion of a once-a-week card lands on last week's row when that
cell is blank; an explicit 0 is N/A and does not count as blank. A card that
missed a week therefore trails by one until it is done twice in a week. Points
stay on the completion day; only the week cell moves.

Off for past-date completions, rate cards (row 5 "1/m"), cumulative cards and
EXEMPT. A failed sheet read falls back to the natural week.

A shifted choice is remembered for MEMO_TTL, so a retry after a killed run
(dtd's queue) or a duplicate in one batch hits the same cell. Without it the
retry would see last week filled and mark this week too: `set` writes carry no
idempotency key.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import excel, rates, weeks

SHEET = "1n+"
RANGE = "B5:AL57"
FIRST_COL = 2  # B
FIRST_LABEL = "1.1"  # last year's row 12.5 is not last week
# Not once-a-week obligations: a blank week is normal for these.
EXEMPT = {"nails", "groceries", "2 hci", "1 kids nature"}
MEMO = Path.home() / ".cache/jm/1n-week-fill.json"
MEMO_TTL = 600


@dataclass(frozen=True)
class Target:
    label: str
    row: int
    open: bool      # the cell is blank, so there is something to fill
    shifted: bool   # landed on last week rather than the natural week


def _norm(name: str) -> str:
    return " ".join((name or "").replace("\xa0", " ").split()).lower()


def _col_index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


def _is_blank(raw) -> bool:
    return ("" if raw is None else str(raw).strip()) in ("", "missing value")


class Sheet:
    """Row 5 plus the week rows, read once per run."""

    def __init__(self, rows: list[list[str]]):
        self.row5 = rows[0] if rows else []
        self.weeks = {str(r[0]).strip(): (i + 5, r)
                      for i, r in enumerate(rows) if i and r and str(r[0]).strip()}

    def cell(self, label: str, col: str) -> str:
        row = self.weeks[label][1]
        i = _col_index(col) - FIRST_COL
        return row[i] if i < len(row) else ""

    def rate_card(self, col: str) -> bool:
        i = _col_index(col) - FIRST_COL
        return i < len(self.row5) and rates.parse_rate(self.row5[i]) is not None


def load() -> Optional[Sheet]:
    try:
        r = excel.read_range(SHEET, RANGE)
    except Exception:  # noqa: BLE001
        return None
    return Sheet(r["rows"]) if r.get("ok") and r.get("rows") else None


def _recall(key: str, now: float) -> Optional[str]:
    try:
        hit = json.loads(MEMO.read_text()).get(key)
    except (OSError, ValueError):
        return None
    return hit["label"] if hit and now - hit["at"] < MEMO_TTL else None


def _remember(key: str, label: str, now: float) -> None:
    try:
        memo = json.loads(MEMO.read_text())
    except (OSError, ValueError):
        memo = {}
    memo = {k: v for k, v in memo.items() if now - v["at"] < MEMO_TTL}
    memo[key] = {"label": label, "at": now}
    try:
        MEMO.parent.mkdir(parents=True, exist_ok=True)
        tmp = MEMO.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(memo))
        tmp.replace(MEMO)
    except OSError:
        pass


def resolve(name: str, col: str, d: dt.date, sheet: Optional[Sheet] = None,
            now: Optional[float] = None) -> Optional[Target]:
    """Where a completion of card `name` (1n+ column `col`) on `d` lands, or
    None when the rule does not apply and the natural week stands."""
    sheet = sheet or load()
    if sheet is None or _norm(name) in EXEMPT or sheet.rate_card(col):
        return None
    natural = weeks.habit_week_label(name, d)
    if natural not in sheet.weeks:
        return None
    now = time.time() if now is None else now
    key = f"{_norm(name)}@{d.isoformat()}"
    label = _recall(key, now)
    if label not in sheet.weeks:
        prev = weeks.prev_habit_week_label(name, d)
        label = natural
        if (natural != FIRST_LABEL and prev in sheet.weeks
                and _is_blank(sheet.cell(prev, col))):
            label = prev
            _remember(key, label, now)
    return Target(label, sheet.weeks[label][0], _is_blank(sheet.cell(label, col)),
                  label != natural)
