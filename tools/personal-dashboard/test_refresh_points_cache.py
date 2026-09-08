#!/usr/bin/env python3
"""Regression tests for refresh_points_cache.py.

Covers two same-day (2026-09-08) bugs with the identical user-visible
symptom — watch complications quietly showing stale/no data with nothing in
any log explaining why:

1. An unescaped `"` inside a Python comment silently truncated the whole
   embedded-in-bash script wherever bash's outer double-quoting parsed it.
   No error, no output, no cache write. (Fixed by extracting to this file.)
2. openpyxl's raw file open hit a confirmed Full Disk Access denial for the
   `com.apple.python3` bundle (Ix's own TCC log: `authValue=0`, service
   kTCCServiceSystemPolicyAllFiles) — reproducible under cron AND launchd,
   i.e. not scheduler-specific. Fixed by switching to xlwings (talks to the
   already-running Excel.app, which needs no separate FDA grant at all —
   same fix personal-dashboard/dashboard.py already uses for the identical
   wall)."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "refresh_points_cache", HERE / "refresh_points_cache.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["refresh_points_cache"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def rpc():
    return _load()


class FakeXlSheet:
    """Duck-types just enough of an xlwings Sheet for _sheet_view: a
    .range(addr) call returning an object with a .value 2D array. The addr
    itself is ignored — production always requests one fixed full-height
    range per sheet, so the fake just always returns its whole array."""
    def __init__(self, rows):
        self._rows = rows

    def range(self, addr):
        return _FakeRange(self._rows)


class _FakeRange:
    def __init__(self, rows):
        self.value = rows


class FakeWorkbook:
    """Duck-types xlwings Book: `.sheets[name]` -> FakeXlSheet."""
    def __init__(self, sheets: dict):
        self.sheets = sheets


def _row(n):
    """An n-cell row, all-None, 1-indexed access via row[i-1]."""
    return [None] * n


def test_build_cache_extracts_today_fields_correctly(rpc):
    """The actual bug's blast radius: __total__/__hcb_kcal__/__hcbp_hcbc__/
    __salat__/__hcmp_min__ must all be present and correct for today, from
    the exact same column letters the live spreadsheet uses (D, U, Y, AA,
    AP, AQ, AR, AS)."""
    today = dt.date(2026, 9, 8)

    fen_row = _row(30)
    fen_row[1] = today          # col B: date
    fen_row[17] = 20            # col R: i9
    fen_row[3] = 928            # col D: grand total (__total__)
    fen_rows = [_row(30), _row(30), fen_row]  # rows 1-2 headers; data starts row 3

    hcbi_row = _row(30)
    hcbi_row[1] = today          # col B: date
    hcbi_row[20] = 1250          # col U: kcal
    hcbi_row[24] = 0             # col Y
    hcbi_row[26] = 69.857        # col AA
    hcbi_rows = [_row(30), _row(30), hcbi_row]

    n0_row = _row(50)
    n0_row[2] = today             # col C: date
    n0_row[41] = 24               # col AP: salat
    n0_row[42] = 83                # col AQ: o314
    n0_row[43] = 78                # col AR: 冥想
    n0_row[44] = 0                  # col AS: 其他人
    n0_rows = [_row(50), _row(50), _row(50), _row(50), n0_row]  # data starts row 5

    wb = FakeWorkbook({
        "0分": FakeXlSheet(fen_rows),
        "hcbi": FakeXlSheet(hcbi_rows),
        "0n": FakeXlSheet(n0_rows),
    })

    result = rpc.build_cache(wb, today=today)
    day = result["2026-09-08"]
    assert day["__total__"] == 928
    assert day["__hcb_kcal__"] == 1250
    assert day["__hcbp_hcbc__"] == 70  # round(0 + 69.857)
    assert day["__salat__"] == 24
    assert day["__hcmp_min__"] == 161  # 83 + 78 + 0


def test_build_0n_days_writes_zero_hcmp_minutes_not_missing(rpc):
    """A day with genuinely zero hcmp minutes must still get the key written
    as 0, not omitted — omitting it is indistinguishable from 'no data yet'
    on the wear side and renders as '-' instead of '0m' (2026-09-08 bug)."""
    today = dt.date(2026, 9, 7)
    row = _row(50)
    row[2] = today
    row[41] = 83   # salat
    row[42] = 0    # o314
    row[43] = 0    # 冥想
    row[44] = 0    # 其他人
    ws = rpc.SheetView([_row(50)] * 4 + [row])

    result: dict = {}
    rpc.build_0n_days(ws, today, today - dt.timedelta(days=90), result)
    assert result["2026-09-07"]["__hcmp_min__"] == 0
    assert "__hcmp_min__" in result["2026-09-07"]


def test_load_workbook_or_die_reports_connection_failure_actionably(rpc, monkeypatch):
    """xlwings connection failures (e.g. Excel not open/running on this Mac)
    must exit loudly with a message explaining what's needed, not silently
    produce nothing — the same silent-failure shape as both real 2026-09-08
    bugs this module exists to prevent a repeat of."""
    def boom():
        raise RuntimeError("no running Excel instance")

    monkeypatch.setattr(rpc, "_connect_workbook", boom)
    with pytest.raises(SystemExit) as exc_info:
        rpc.load_workbook_or_die()
    message = str(exc_info.value)
    assert "xlwings" in message
    assert "Excel must be open" in message
    assert "no running Excel instance" in message


def test_sheet_view_min_row_matches_openpyxl_semantics(rpc):
    """SheetView.iter_rows(min_row=N) must behave exactly like openpyxl's
    (1-indexed, inclusive) so build_fen_days/build_0n_days's hardcoded
    min_row=3 / min_row=5 calls stay correct regardless of data source."""
    rows = [["r1"], ["r2"], ["r3"], ["r4"], ["r5"]]
    ws = rpc.SheetView(rows)
    assert list(ws.iter_rows(min_row=3, values_only=True)) == [["r3"], ["r4"], ["r5"]]
    assert list(ws.iter_rows(min_row=1, values_only=True)) == rows
