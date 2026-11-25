"""2hci's 0n read through excel-http (2026-11-25): read_0n no longer runs
AppleScript. The daemon returns cell VALUES (long-form dates, "26.0"), where
the old read returned display text ("11/25", "26", and "##" for a number too
wide for its column)."""
import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def long_date(d):
    """A real date cell as /read_range returns it (U+202F before AM)."""
    return "%s, %s %d, %d at 12:00:00\u202fAM" % (d.strftime("%A"), d.strftime("%B"), d.day, d.year)


def _n(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


class FakeExcel:
    """Stand-in for lib/neon/excel.py: read_range over a {(sheet, "B", 3): text}
    grid. Any other client call (read, write, append, ...) fails the test."""

    def __init__(self, cells=None, error=None):
        self.cells, self.error, self.calls = cells or {}, error, []

    def read_range(self, sheet, rng):
        self.calls.append((sheet, rng))
        if self.error:
            return {"ok": False, "error": self.error}
        c1, r1, c2, r2 = re.match(r"^([A-Z]+)(\d+):([A-Z]+)(\d+)$", rng).groups()
        cols = {}
        for (sh, col, row), v in self.cells.items():
            if sh == sheet:
                cols[(_n(col), row)] = v
        return {"ok": True, "row": int(r1), "rows": [
            [cols.get((c, r), "") for c in range(_n(c1), _n(c2) + 1)]
            for r in range(int(r1), int(r2) + 1)]}

    def __getattr__(self, name):
        raise AssertionError("unexpected neon.excel.%s call" % name)


@pytest.fixture()
def m(monkeypatch):
    spec = importlib.util.spec_from_file_location("hci2_read", HERE / "2hci.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hci2_read"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("raw ix-osa must not run"))
    return mod


def _zero_n(days, first="L", second="K"):
    """0n with `1st hci` in column `first`, `2nd hci` in `second`, weights in
    row 2 and {row: (date, 1st, 2nd)} below."""
    cells = {("0n", "C", 1): "2026.0", ("0n", "C", 2): "⊖分",
             ("0n", first, 1): "1st hci", ("0n", first, 2): "26.0",
             ("0n", second, 1): "2nd hci", ("0n", second, 2): "7.0"}
    for row, (d, a, b) in days.items():
        cells[("0n", "C", row)] = long_date(d) if isinstance(d, date) else d
        cells[("0n", first, row)] = a
        cells[("0n", second, row)] = b
    return cells


def test_read_0n_finds_columns_by_header_and_keys_rows_by_month_day(m, monkeypatch):
    fake = FakeExcel(_zero_n({
        5: (date(2026, 10, 26), "15.0", "1.0"),
        6: (date(2026, 10, 27), "", "0.0"),
        7: (date(2026, 10, 28), "1.0", "230.0"),
        8: ("", "9.0", "9.0"),                      # no date: skipped
        372: ("Q3", "", ""),
    }))
    monkeypatch.setattr(m, "_excel", fake)
    weights, rows = m.read_0n()
    assert weights == {"1st hci": 26.0, "2nd hci": 7.0}
    assert rows["1st hci"] == {"10/26": "15", "10/27": "", "10/28": "1", "Q3": ""}
    assert rows["2nd hci"] == {"10/26": "1", "10/27": "0", "10/28": "230", "Q3": ""}
    assert fake.calls == [("0n", "A1:CB2"), ("0n", "C5:C400"), ("0n", "L5:L400"), ("0n", "K5:K400")]


def test_read_0n_follows_moved_columns(m, monkeypatch):
    fake = FakeExcel(_zero_n({5: (date(2026, 10, 26), "15.0", "2.0")}, first="AB", second="D"))
    monkeypatch.setattr(m, "_excel", fake)
    _w, rows = m.read_0n()
    assert rows["1st hci"]["10/26"] == "15" and rows["2nd hci"]["10/26"] == "2"
    assert fake.calls[2:] == [("0n", "AB5:AB400"), ("0n", "D5:D400")]


def test_scores_from_a_daemon_read(m, monkeypatch):
    """full = non-blank non-zero, na = explicit 0, miss = blank; a value > 1 is
    minutes. 230 is a real value: the display read saw it as "##" and counted
    the day full but untimed."""
    monkeypatch.setattr(m, "_excel", FakeExcel(_zero_n({
        5: (date.today().replace(month=10, day=26), "15.0", "1.0"),
        6: (date.today().replace(month=10, day=27), "", "0.0"),
        7: (date.today().replace(month=10, day=28), "1.0", "230.0")})))
    _w, rows = m.read_0n()
    y = date.today().year
    one = m.score_habit(rows["1st hci"], date(y, 10, 26), date(y, 10, 28))
    two = m.score_habit(rows["2nd hci"], date(y, 10, 26), date(y, 10, 28))
    assert (one["full"], one["miss"], one["na"], one["minutes"]) == (2, 1, 0, 15)
    assert (two["full"], two["miss"], two["na"], two["minutes"]) == (2, 0, 1, 230)


def test_read_0n_exits_when_a_header_is_missing(m, monkeypatch):
    cells = _zero_n({5: (date(2026, 10, 26), "1.0", "1.0")})
    cells[("0n", "K", 1)] = "renamed"
    monkeypatch.setattr(m, "_excel", FakeExcel(cells))
    with pytest.raises(SystemExit) as e:
        m.read_0n()
    assert str(e.value) == "ERROR: 0n read failed: ERROR: hci headers not found in 0n row 1"


def test_read_0n_exits_when_the_daemon_cannot_answer(m, monkeypatch):
    monkeypatch.setattr(m, "_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(SystemExit) as e:
        m.read_0n()
    assert str(e.value) == "ERROR: 0n read failed: excel-http unreachable"


def test_read_0n_exits_when_no_date_comes_back_as_a_date(m, monkeypatch):
    """Serial dates would score the whole window 'not in sheet' with no error."""
    monkeypatch.setattr(m, "_excel", FakeExcel(_zero_n({5: ("46321.0", "1.0", "1.0")})))
    with pytest.raises(SystemExit, match="no date cells"):
        m.read_0n()
