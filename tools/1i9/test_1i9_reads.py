"""1i9 reads through excel-http (2026-11-25): i9_points and check_label no
longer run AppleScript. The daemon returns cell VALUES ("481.0", long-form
dates, 11.299999999999999), where the old reads returned display text."""
import datetime as dt
import importlib.util
import re
import sys
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
    spec = importlib.util.spec_from_file_location("one_i9_reads", HERE / "1i9.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["one_i9_reads"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_osa", lambda *a, **k: pytest.fail("raw ix-osa must not run"))
    monkeypatch.setattr(mod, "run_bounded", lambda *a, **k: pytest.fail("raw ix-osa must not run"))
    return mod


def _sheet(days_points):
    cells = {("0分", "B", 1): "5^0", ("0分", "R", 1): "i9"}
    for row, (d, pts) in enumerate(days_points, start=2):
        cells[("0分", "B", row)] = long_date(d) if isinstance(d, dt.date) else d
        cells[("0分", "R", row)] = pts
    return cells


def test_i9_points_sums_the_week_rows_from_date_values(m, monkeypatch):
    fake = FakeExcel(_sheet([
        (dt.date(2026, 10, 10), "999.0"),     # the Saturday before: excluded
        (dt.date(2026, 10, 11), "90.0"),
        (dt.date(2026, 10, 17), "198.0"),
        (dt.date(2026, 10, 18), "70.0"),      # the Sunday after: excluded
        ("not a date", "5.0"),
        (dt.date(2026, 10, 14), ""),          # blank day
    ]))
    monkeypatch.setattr(m, "neon_excel", fake)
    assert m.i9_points(dt.date(2026, 10, 11), dt.date(2026, 10, 17)) == 288
    assert fake.calls == [("0分", "B1:R400")]


def test_i9_points_rounds_a_fraction_as_the_sheet_shows_it(m, monkeypatch):
    """R79 = 140.5 showed as 141 in the narrow General column, so the display
    read counted 141; truncating the value would lose a point."""
    monkeypatch.setattr(m, "neon_excel", FakeExcel(_sheet([
        (dt.date(2026, 3, 22), "140.5"), (dt.date(2026, 3, 23), "416.0"),
        (dt.date(2026, 3, 24), "1.079542857143E+4")])))
    assert m.i9_points(dt.date(2026, 3, 22), dt.date(2026, 3, 28)) == 141 + 416 + 10795


def test_points_raw_keeps_the_shape_parse_points_reads(m, monkeypatch):
    monkeypatch.setattr(m, "neon_excel", FakeExcel(_sheet([(dt.date(2026, 1, 4), "100.0")])))
    assert m.points_raw(1, 3) == "1\t5^0\ti9\n2\t1/4\t100\n3\t\t\n"


def test_i9_points_raises_when_the_daemon_cannot_answer(m, monkeypatch):
    monkeypatch.setattr(m, "neon_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(RuntimeError, match="unreachable"):
        m.i9_points(dt.date(2026, 10, 11), dt.date(2026, 10, 17))


def test_i9_points_raises_when_no_date_comes_back_as_a_date(m, monkeypatch):
    """Dates returned as serials must not read as a quiet 0分."""
    monkeypatch.setattr(m, "neon_excel", FakeExcel(_sheet([("46306.0", "90.0")])))
    with pytest.raises(RuntimeError, match="no date cells"):
        m.i9_points(dt.date(2026, 10, 11), dt.date(2026, 10, 17))


@pytest.mark.parametrize("stored, shown", [
    ("11.3", "11.3"), ("11.299999999999999", "11.3"), ("10.1", "10.1"),
    ("12.0", "12"), ("", ""), ("week 3", "week 3")])
def test_check_label_returns_the_label_as_shown(m, monkeypatch, stored, shown):
    fake = FakeExcel({("i9+m5x2", "A", 48): stored})
    monkeypatch.setattr(m, "neon_excel", fake)
    assert m.check_label(48, "11.3") == shown
    assert fake.calls == [("i9+m5x2", "A48:A48")]     # read_range: no raw fallback behind it


def test_check_label_raises_when_the_daemon_cannot_answer(m, monkeypatch):
    monkeypatch.setattr(m, "neon_excel", FakeExcel(error="excel_error"))
    with pytest.raises(RuntimeError, match="excel_error"):
        m.check_label(48, "11.3")


def test_main_reports_a_failed_read_and_writes_nothing(m, monkeypatch, capsys):
    monkeypatch.setattr(m, "neon_excel", FakeExcel(error="excel-http unreachable"))
    monkeypatch.setattr(m, "i9_minutes", lambda s, e: 100)
    monkeypatch.setattr(m, "stale_pending", lambda: [])
    assert m.main(["2026-10-14", "--print-context"]) == 1
    assert "could not read the week" in capsys.readouterr().out


def test_cell_date_and_general_contract(m):
    assert m._cell_date("Wednesday, November 25, 2026 at 12:00:00 AM") == dt.date(2026, 11, 25)
    assert m._cell_date("Wednesday, November 25, 2026 at 3:04:05\u202fPM") == dt.date(2026, 11, 25)
    for not_a_date in ("46351.0", "11/25", "2026.11.25", "", "Q3"):
        assert m._cell_date(not_a_date) is None
    assert m._general("1.2345E+4") == "12345"
    for text in ("1e5", "nan", "inf", "1_000", "007", "11"):     # text cells stay text
        assert m._general(text) == text
