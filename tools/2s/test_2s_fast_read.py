"""2s-fast's Neon read through excel-http (2026-11-25): read_neon_month no
longer runs AppleScript. The scorecard write (another workbook) is untouched
and must not run here."""
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
    spec = importlib.util.spec_from_file_location("twos_fast_read", HERE / "2s-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["twos_fast_read"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: pytest.fail("raw ix-osa must not run"))
    return mod


SRC = ["AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY", "AZ", "BA", "BC"]


def _sheet(label_rows, values_row=None, values=None):
    cells = {("1分+1s", "A", r): v for r, v in label_rows.items()}
    for col, v in zip(SRC, values or []):
        cells[("1分+1s", col, values_row)] = v
    cells[("1分+1s", "BB", values_row or 1)] = "not a source column"
    return cells


def test_read_neon_month_returns_row_and_the_twelve_source_values(m, monkeypatch):
    vals = ["0.321428571429", "0.607142857143", "0.5", "3.766530612245", "116.958418367347",
            "10.775510204082", "91.25", "239.0", "82.035714285714", "366.107142857143",
            "257.892857142857", "1356.949132653061"]
    fake = FakeExcel(_sheet({44: "10.4", 45: "11.1", 46: "11.2"}, 45, vals))
    monkeypatch.setattr(m, "neon_excel", fake)
    raw = m.read_neon_month(11)
    assert raw == ["45"] + vals
    assert [m.normalize_value(v) for v in raw[1:]] == [
        "0", "1", "0", "4", "117", "11", "91", "239", "82", "366", "258", "1357"]
    assert fake.calls == [("1分+1s", "A2:A80"), ("1分+1s", "AQ45:BC45")]


def test_month_row_is_matched_as_a_float_window(m, monkeypatch):
    """M.1 is =A(n-1)+0.1, so it can be stored as 3.0999999999999996; 1.0 (the
    month block under the weeks) and 11.1 are not month 1."""
    monkeypatch.setattr(m, "neon_excel", FakeExcel(_sheet(
        {2: "1.0", 3: "week", 4: "", 9: "2.4", 10: "3.0999999999999996", 45: "11.1"}, 10, ["5.0"] * 12)))
    assert m.read_neon_month(3)[0] == "10"
    with pytest.raises(SystemExit) as e:
        m.read_neon_month(1)
    assert str(e.value) == "ERROR reading Neon on ix: ERROR: no 1.1 row in 1分+1s"


def test_blank_cells_come_back_empty_and_normalize_as_before(m, monkeypatch):
    monkeypatch.setattr(m, "neon_excel", FakeExcel(_sheet({49: "12.1"}, 49, ["0.4", ""] + ["0.0"] * 10)))
    raw = m.read_neon_month(12)
    assert raw[2] == "" and m.normalize_value(raw[2]) == m.normalize_value("missing value") == '""'


def test_read_neon_month_exits_when_the_daemon_cannot_answer(m, monkeypatch):
    monkeypatch.setattr(m, "neon_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(SystemExit) as e:
        m.read_neon_month(11)
    assert str(e.value) == "ERROR reading Neon on ix: excel-http unreachable"
