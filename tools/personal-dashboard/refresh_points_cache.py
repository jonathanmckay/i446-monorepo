#!/usr/bin/env python3
"""refresh_points_cache — build the points JSON cache from Neon Excel.

Extracted from refresh-points-cache.sh's embedded `python3 -c "..."` block
(2026-09-08): that form is nearly untestable and, worse, an unescaped `"`
inside a Python comment silently truncated the whole script wherever bash's
outer double-quoting parsed it — no error, no output, no file write, no
alerting (found and fixed same day).

Reads via **xlwings** (live Excel automation), not openpyxl directly against
the file — a same-day follow-up fix. openpyxl's raw file open hit a
confirmed (via Ix's own TCC log: `authValue=0`, service
kTCCServiceSystemPolicyAllFiles) Full Disk Access denial for the specific
`com.apple.python3` bundle at
`/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app`
— reproducible under both cron and launchd, i.e. any non-interactively-spawned
process using that binary, regardless of scheduler. Granting FDA is a GUI-only,
per-machine step this script shouldn't depend on. `tools/personal-dashboard/
dashboard.py`'s `load_cache_data()` hit the identical wall earlier and solved
it the same way: xlwings talks to the already-running Excel.app over
AppleScript, which already has its own access to the file (it has it open) —
no separate Full Disk Access grant needed at all, for anything.

The one real cost: xlwings/AppleScript per-cell access is slow, so this
reads each sheet's needed range in ONE bulk `.range(...).value` call
(returns a full 2D array) rather than iterating cells, keeping this to 3
AppleScript round trips total regardless of history depth.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl.utils import column_index_from_string as ci

CACHE = Path(__file__).parent / ".points-cache.json"
CUTOFF_DAYS = 90
NEON_NAME_RE = r"Neon分v[\d.]+\.xlsx$"

# 0分: per-domain points + block breakdown + day grand total (col D).
# __total__ is read by neg1n's /api/day-points (quarter-circle arc complication).
FEN_COLS = {16: "-1₦", 17: "0₲", 18: "i9", 19: "m5", 20: "个", 21: "媒",
            22: "思", 23: "hcb", 24: "xk", 25: "社"}
BLOCK_COLS = {7: "卯", 8: "辰", 9: "巳", 10: "午", 11: "未", 12: "申",
              13: "酉", 14: "戌", 15: "亥"}
FEN_LAST_COL = "Z"  # covers FEN_COLS/BLOCK_COLS (max col 25) + TOTAL_COL (D)

# hcbi: calories eaten (col U) + today's hcbp+hcbc score (Y+AA), per day,
# for neg1n's /api/hcb. hcbp+hcbc was originally read as the fixed Q2+Q3
# running total (hcbi!X375+X378) but that's a year-scale figure incompatible
# with the 131 goal, which is a DAILY target — JM (2026-09-08):
# "=hcbi!AA{row}+hcbi!Y{row}" is the actual per-day figure to use.
KCAL_COL = ci("U")
HCBP_HCBC_COLS = (ci("Y"), ci("AA"))
HCBI_LAST_COL = "AC"  # covers U (21) and AA (27) with margin

# 0n: prayer count (صلاة) + hcmp minutes (o314+冥想+其他人), per day, for
# neg1n's /api/hcmp.
SALAT_COL = ci("AP")
HCMP_COLS = (ci("AQ"), ci("AR"), ci("AS"))
N0_LAST_COL = "AU"  # covers AP (42) through AS (45) with margin

# Generous row headroom past CUTOFF_DAYS — cheap (still one round trip) and
# avoids re-tuning this every time the sheets grow.
LAST_ROW = 700


def as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


class SheetView:
    """Wraps one bulk `.range(...).value` 2D array (rows starting at Excel
    row 1) with openpyxl's `iter_rows(min_row=..., values_only=True)`
    interface, so the build_* functions below don't need to know or care
    whether the data came from openpyxl or xlwings."""

    def __init__(self, rows: list[list]):
        self._rows = rows

    def iter_rows(self, min_row: int = 1, values_only: bool = True):
        assert values_only is True
        return iter(self._rows[min_row - 1:])


def _connect_workbook():
    """The live Neon workbook via xlwings — prefer an already-open instance
    (asking Excel, which has file access, rather than the filesystem, which
    this process may not) over opening a fresh one."""
    import xlwings as xw

    wb = next((b for b in xw.books if re.match(NEON_NAME_RE, b.name)), None)
    if wb is not None:
        return wb
    neon_path = Path.home() / "OneDrive/vault-excel/Neon分v12.2.xlsx"
    return xw.Book(str(neon_path))


def load_workbook_or_die():
    try:
        return _connect_workbook()
    except Exception as e:
        sys.exit(
            f"ERROR: can't connect to the live Neon workbook via xlwings: "
            f"{type(e).__name__}: {e}. Excel must be open on this Mac — "
            "this reads through the running app, not the file directly."
        )


def _sheet_view(wb, sheet_name: str, last_col: str) -> SheetView:
    ws = wb.sheets[sheet_name]
    rows = ws.range(f"A1:{last_col}{LAST_ROW}").value
    return SheetView(rows)


def build_fen_days(ws, today: date, cutoff: date, result: dict) -> None:
    for row in ws.iter_rows(min_row=3, values_only=True):
        d = as_date(row[1])
        if d is None or d <= cutoff or d > today:
            continue
        day_data = result.setdefault(d.isoformat(), {})
        for idx, label in FEN_COLS.items():
            val = row[idx - 1]
            if val is not None and isinstance(val, (int, float)) and val > 0:
                day_data[label] = int(round(float(val)))
        block_data = {}
        for idx, label in BLOCK_COLS.items():
            val = row[idx - 1]
            if val is not None and isinstance(val, (int, float)) and val > 0:
                block_data[label] = int(round(float(val)))
        if block_data:
            day_data["__block__"] = block_data
        total = row[ci("D") - 1]
        if isinstance(total, (int, float)):
            day_data["__total__"] = int(round(float(total)))


def build_hcbi_days(ws, today: date, cutoff: date, result: dict) -> None:
    for row in ws.iter_rows(min_row=3, values_only=True):
        d = as_date(row[1])
        if d is None or d <= cutoff or d > today:
            continue
        day_data = result.setdefault(d.isoformat(), {})
        kcal = row[KCAL_COL - 1]
        if isinstance(kcal, (int, float)) and kcal > 0:
            day_data["__hcb_kcal__"] = int(round(float(kcal)))
        hcbp_hcbc = sum(row[c - 1] for c in HCBP_HCBC_COLS if isinstance(row[c - 1], (int, float)))
        day_data["__hcbp_hcbc__"] = int(round(float(hcbp_hcbc)))


def build_0n_days(ws, today: date, cutoff: date, result: dict) -> None:
    for row in ws.iter_rows(min_row=5, values_only=True):
        d = as_date(row[2])
        if d is None or d <= cutoff or d > today:
            continue
        day_data = result.setdefault(d.isoformat(), {})
        salat = row[SALAT_COL - 1]
        if isinstance(salat, (int, float)):
            day_data["__salat__"] = int(round(float(salat)))
        hcmp_min = sum(row[c - 1] for c in HCMP_COLS if isinstance(row[c - 1], (int, float)))
        day_data["__hcmp_min__"] = int(round(float(hcmp_min)))  # 0 is real, not missing


def build_cache(wb, today: date | None = None) -> dict:
    today = today or date.today()
    cutoff = today - timedelta(days=CUTOFF_DAYS)
    result: dict = {}
    build_fen_days(_sheet_view(wb, "0分", FEN_LAST_COL), today, cutoff, result)
    build_hcbi_days(_sheet_view(wb, "hcbi", HCBI_LAST_COL), today, cutoff, result)
    build_0n_days(_sheet_view(wb, "0n", N0_LAST_COL), today, cutoff, result)
    return result


def main() -> int:
    wb = load_workbook_or_die()
    result = build_cache(wb)
    CACHE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"wrote {len(result)} days to cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
