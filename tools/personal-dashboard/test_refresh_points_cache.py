#!/usr/bin/env python3
"""Regression tests for refresh_points_cache.py.

Covers the 2026-09-08 bug: the embedded-in-bash version of this script had
an unescaped `"` inside a Python comment that silently truncated the whole
script (no error, no output, no cache write) whenever bash's outer
double-quoting parsed it — and separately, cron-spawned runs intermittently
hit a PermissionError opening the OneDrive-hosted workbook that an
interactive SSH session's process didn't, also with no visible failure.
Both bugs manifested identically to the user: watch complications quietly
showing stale/no data with nothing in any log explaining why."""
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


class FakeSheet:
    """Duck-types just enough of openpyxl's worksheet API for iter_rows."""
    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, min_row=1, values_only=True):
        assert values_only is True
        return iter(self._rows[min_row - 1:])


class FakeWorkbook(dict):
    """A dict of sheet name -> FakeSheet, closeable like a real workbook."""
    def close(self):
        pass


def _row(n, blank=True):
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
    fen_rows = [None, None, fen_row]  # rows 1-2 are headers; data starts row 3

    hcbi_row = _row(30)
    hcbi_row[1] = today          # col B: date
    hcbi_row[20] = 1250          # col U: kcal
    hcbi_row[24] = 0             # col Y
    hcbi_row[26] = 69.857        # col AA
    hcbi_rows = [None, None, hcbi_row]

    n0_row = _row(50)
    n0_row[2] = today             # col C: date
    n0_row[41] = 24               # col AP: salat
    n0_row[42] = 83                # col AQ: o314
    n0_row[43] = 78                # col AR: 冥想
    n0_row[44] = 0                  # col AS: 其他人
    n0_rows = [None, None, None, None, n0_row]  # data starts row 5

    wb = FakeWorkbook({
        "0分": FakeSheet(fen_rows),
        "hcbi": FakeSheet(hcbi_rows),
        "0n": FakeSheet(n0_rows),
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
    ws = FakeSheet([None, None, None, None, row])

    result: dict = {}
    rpc.build_0n_days(ws, today, today - dt.timedelta(days=90), result)
    assert result["2026-09-07"]["__hcmp_min__"] == 0
    assert "__hcmp_min__" in result["2026-09-07"]


def test_load_workbook_or_die_reports_permission_error_actionably(rpc, capsys):
    """The actual 2026-09-08 failure mode: cron's process couldn't open the
    OneDrive-hosted workbook (PermissionError). This must exit loudly with
    a message pointing at Full Disk Access, not silently produce nothing."""
    def boom(*a, **kw):
        raise PermissionError(1, "Operation not permitted")

    rpc.openpyxl.load_workbook = boom
    with pytest.raises(SystemExit) as exc_info:
        rpc.load_workbook_or_die(rpc.NEON)
    message = str(exc_info.value)
    assert "Full Disk Access" in message
    assert "Operation not permitted" in message


def test_load_workbook_or_die_reraises_other_errors_actionably(rpc):
    def boom(*a, **kw):
        raise ValueError("corrupt zip")

    rpc.openpyxl.load_workbook = boom
    with pytest.raises(SystemExit) as exc_info:
        rpc.load_workbook_or_die(rpc.NEON)
    assert "ValueError" in str(exc_info.value)
    assert "corrupt zip" in str(exc_info.value)
