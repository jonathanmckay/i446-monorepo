"""hcmc reads through excel-http (2026-11-25): hcmc-survey's AO notes and
media-tag-audit's 0n minutes no longer run AppleScript. The daemon returns
cell VALUES (week label as a float, long-form dates, "30.0")."""
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


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hs():
    return _load("hcmc_survey_reads", "hcmc-survey.py")


@pytest.fixture(scope="module")
def au():
    return _load("media_tag_audit_reads", "media-tag-audit.py")


# ── hcmc-survey: read_show ───────────────────────────────────────────────────

def _notes(rows):
    cells = {}
    for row, (label, note) in rows.items():
        cells[("1分+1s", "A", row)] = label
        cells[("1分+1s", "AO", row)] = note
    return cells


def test_read_show_returns_last_and_this_weeks_notes(hs, monkeypatch):
    fake = FakeExcel(_notes({
        46: ("11.2", "• older"),
        47: ("11.299999999999999", "• last week \n• second line\n"),
        48: ("11.4", ""),
        56: ("1.0", "month block, not week 1.1")}))
    monkeypatch.setattr(hs, "neon_excel", fake)
    monkeypatch.setattr(hs, "osa", lambda s: pytest.fail("raw ix-osa must not run"))
    info = hs.read_show("11.4", "11.3")
    assert info == {"PREV": (47, "• last week \n• second line"), "CUR": (48, "")}
    assert hs.target(info, "11.4", "11.3") == ("11.4", 48, "")       # last week is filled
    assert fake.calls == [("1分+1s", "A2:A120"), ("1分+1s", "AO2:AO120")]


def test_read_show_blank_last_week_is_the_target(hs, monkeypatch):
    monkeypatch.setattr(hs, "neon_excel", FakeExcel(_notes({47: ("11.3", ""), 48: ("11.4", "")})))
    info = hs.read_show("11.4", "11.3")
    assert hs.target(info, "11.4", "11.3") == ("11.3", 47, "")


def test_read_show_without_the_week_row_has_no_cur(hs, monkeypatch):
    monkeypatch.setattr(hs, "neon_excel", FakeExcel(_notes({47: ("11.3", "x")})))
    assert hs.read_show("12.9", "12.8") == {}


def test_read_show_exits_when_the_daemon_cannot_answer(hs, monkeypatch):
    monkeypatch.setattr(hs, "neon_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(SystemExit, match="read failed: excel-http unreachable"):
        hs.read_show("11.4", "11.3")


# ── media-tag-audit: sheet_minutes ───────────────────────────────────────────

def _zero_n(rows):
    """{row: (date, {col: text})} → 0n cells. AV/AW/AX = -1/-2/-3, N/O = 新闻/词汇."""
    cells = {}
    for row, (d, vals) in rows.items():
        cells[("0n", "C", row)] = long_date(d) if isinstance(d, dt.date) else d
        for col, v in vals.items():
            cells[("0n", col, row)] = v
    return cells


def test_sheet_minutes_reads_tag_columns_and_derives_the_tagged_part_of_ax(au, monkeypatch):
    fake = FakeExcel(_zero_n({
        4: ("Ideal time", {"AV": "999.0"}),
        320: (dt.date(2026, 11, 15), {"AV": "30.0", "AW": "10.0", "AX": "56.0", "N": "20.0", "O": "6.0"}),
        321: (dt.date(2026, 11, 16), {"AV": "", "AW": "x", "AX": "5.0", "N": "10.0"}),
        322: (dt.date(2026, 11, 17), {}),
        323: (dt.date(2026, 11, 18), {"AV": "400.0"}),          # outside the range
    }))
    monkeypatch.setattr(au, "neon_excel", fake)
    got = au.sheet_minutes(dt.date(2026, 11, 15), dt.date(2026, 11, 17))
    assert got == {
        dt.date(2026, 11, 15): {"-1": 30.0, "-2": 10.0, "-3": 30.0},
        dt.date(2026, 11, 16): {"-1": 0.0, "-2": 0.0, "-3": 0.0},     # AX below N + O clamps to 0
        dt.date(2026, 11, 17): {"-1": 0.0, "-2": 0.0, "-3": 0.0},
    }
    assert fake.calls == [("0n", "C3:C500"), ("0n", "N320:AX322")]


def test_sheet_minutes_a_day_missing_from_the_sheet_is_absent(au, monkeypatch):
    fake = FakeExcel(_zero_n({320: (dt.date(2026, 11, 15), {"AV": "30.0"})}))
    monkeypatch.setattr(au, "neon_excel", fake)
    assert au.sheet_minutes(dt.date(2026, 12, 28), dt.date(2026, 12, 29)) == {}
    assert fake.calls == [("0n", "C3:C500")]


def test_sheet_minutes_exits_when_the_daemon_cannot_answer(au, monkeypatch):
    monkeypatch.setattr(au, "neon_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(SystemExit, match="ERROR: 0n read failed: excel-http unreachable"):
        au.sheet_minutes(dt.date(2026, 11, 15), dt.date(2026, 11, 17))


def test_sheet_minutes_exits_when_no_date_comes_back_as_a_date(au, monkeypatch):
    """Serial dates would read as an empty sheet, and --fix would re-append
    every tagged minute."""
    monkeypatch.setattr(au, "neon_excel", FakeExcel(_zero_n({320: ("46341.0", {"AV": "30.0"})})))
    with pytest.raises(SystemExit, match="no date cells"):
        au.sheet_minutes(dt.date(2026, 11, 15), dt.date(2026, 11, 17))
