"""1s reads through excel-http (2026-11-25): 1s-survey's daily context and 0l
gate and 1s-weeks' week table no longer run AppleScript. The daemon returns
cell VALUES (long-form dates, "7.5", "1590.857142857143"), where the old reads
returned `as text` values or display text."""
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


@pytest.fixture()
def sv(monkeypatch):
    mod = _load("s1_reads", "1s-survey.py")
    monkeypatch.setattr(mod, "run_bounded", lambda *a, **k: pytest.fail("raw ix-osa must not run"))
    return mod


@pytest.fixture()
def wk(monkeypatch):
    return _load("s1_weeks_reads", "1s-weeks.py")


WEEK = [dt.date(2026, 11, 15) + dt.timedelta(days=i) for i in range(7)]


# ── 1s-survey: fetch_context ─────────────────────────────────────────────────

def _daily(rows):
    """{row: (date, {col: text})} → 0s897 cells."""
    cells = {}
    for row, (d, vals) in rows.items():
        cells[("0s897", "B", row)] = long_date(d) if isinstance(d, dt.date) else d
        for col, v in vals.items():
            cells[("0s897", col, row)] = v
    return cells


def test_fetch_context_reads_each_days_answers_by_column(sv, monkeypatch):
    fake = FakeExcel(_daily({
        320: (WEEK[0], {"E": "Sunday title", "H": "win\nsecond line", "I": "learned", "P": "8.0",
                        "Q": "5.0", "R": "6.5", "S": "proud", "T": "learn others", "F": "ignored"}),
        322: (WEEK[2], {"E": "  Tuesday  ", "P": "7.5"}),
        323: (WEEK[3], {}),                                    # row exists, survey blank
    }))
    monkeypatch.setattr(sv, "neon_excel", fake)
    ctx = sv.fetch_context(WEEK)
    assert ctx["titles"] == [(0, "Sunday title"), (2, "Tuesday")]
    assert ctx["wins"] == [(0, "win\nsecond line")]
    assert ctx["learnings"] == [(0, "learned")]
    assert ctx["ceil"] == [(0, "8.0"), (2, "7.5")]
    assert ctx["floor"] == [(0, "5.0")] and ctx["mean"] == [(0, "6.5")]
    assert ctx["proud"] == [(0, "proud")] and ctx["learn_others"] == [(0, "learn others")]
    assert fake.calls == [("0s897", "B3:T600")]


def test_fetch_context_matches_the_year(sv, monkeypatch):
    """0s897 spans two years: last year's 11/15 row is not this week's."""
    monkeypatch.setattr(sv, "neon_excel", FakeExcel(_daily({
        10: (dt.date(2025, 11, 15), {"E": "last year"}),
        320: (WEEK[0], {"E": "this year"})})))
    assert sv.fetch_context(WEEK)["titles"] == [(0, "this year")]


def test_fetch_context_is_empty_when_the_daemon_cannot_answer(sv, monkeypatch):
    monkeypatch.setattr(sv, "neon_excel", FakeExcel(error="excel-http unreachable"))
    assert sv.fetch_context(WEEK) == {k: [] for k in sv.DAILY_COLS}


def test_fetch_context_is_empty_when_no_date_comes_back_as_a_date(sv, monkeypatch):
    monkeypatch.setattr(sv, "neon_excel", FakeExcel(_daily({320: ("46341.0", {"E": "t"})})))
    assert sv.fetch_context(WEEK) == {k: [] for k in sv.DAILY_COLS}


# ── 1s-survey: the 0l gate ───────────────────────────────────────────────────

def _zero_l(marks, col="S"):
    cells = {}
    for i, d in enumerate(WEEK):
        cells[("0n", "C", 320 + i)] = long_date(d)
        if marks[i] is not None:
            cells[("0n", col, 320 + i)] = marks[i]
    return cells


def test_read_0l_done_counts_positive_marks_only(sv, monkeypatch):
    fake = FakeExcel(_zero_l(["1.0", "20.0", None, "0.0", "x", "", "0.5"]))
    monkeypatch.setattr(sv, "neon_excel", fake)
    assert sv.read_0l_done(WEEK) == {0, 1, 6}
    assert [c[0] for c in fake.calls] == ["0n", "0n"] and fake.calls[0][1] == "C3:C500"


def test_check_week_lists_the_days_without_0l(sv, monkeypatch):
    monkeypatch.setattr(sv, "neon_excel", FakeExcel(_zero_l(["1.0"] * 5 + [None, "1.0"])))
    monkeypatch.setattr(sv, "_toggl_minutes_by_day", lambda ds: {})
    b = sv.check_week(WEEK, {"titles": [(i, "t") for i in range(7)]})
    assert b["missing_0l"] == [WEEK[5]] and b["missing_0s"] == []


def test_check_week_blocks_every_day_when_the_daemon_cannot_answer(sv, monkeypatch):
    """Same as a failed osascript before: all seven days read as missing."""
    monkeypatch.setattr(sv, "neon_excel", FakeExcel(error="read_refused"))
    monkeypatch.setattr(sv, "_toggl_minutes_by_day", lambda ds: {})
    assert sv.check_week(WEEK, {"titles": []})["missing_0l"] == WEEK
    assert sv.read_0l_done(WEEK) is None


def test_read_0l_done_fails_when_no_date_comes_back_as_a_date(sv, monkeypatch):
    monkeypatch.setattr(sv, "neon_excel", FakeExcel({("0n", "C", 320): "46341.0", ("0n", "S", 320): "1.0"}))
    assert sv.read_0l_done(WEEK) is None


# ── 1s-weeks: fetch_rows ─────────────────────────────────────────────────────

def _weeks(rows):
    cells = {}
    for row, vals in rows.items():
        for col, v in vals.items():
            cells[("1分+1s", col, row)] = v
    return cells


def test_fetch_rows_shows_numbers_as_the_sheet_does(wk, monkeypatch):
    fake = FakeExcel(_weeks({
        2: {"A": "11.2", "C": "1.113742857143E+4", "O": "1590.857142857143", "G": "246.5",
            "L": "-49.428571428571", "Y": "7.042857142857", "W": "7.85", "X": "6.0",
            "R": " Interviews ", "S": "win\nmore"},
        3: {"A": "11.299999999999999", "C": "9357.681428571428", "O": "", "G": "n/a"},
        4: {"A": ""},
        5: {"A": "1.0", "C": "5.0"},                     # the month block below the weeks
    }))
    monkeypatch.setattr(wk, "neon_excel", fake)
    rows = wk.fetch_rows()
    assert [r["week"] for r in rows] == ["11.2", "11.3"]          # stops at the first blank A
    a, b = rows
    assert a["∑分"] == 11137.0 and a["分/d"] == 1591.0
    assert a["i9"] == 247.0 and a["hcb"] == -49.0                  # half away from zero, like Excel
    assert a["avg"] == 7.0 and a["high"] == 7.9 and a["low"] == 6.0
    assert a["title"] == "Interviews" and a["win"] == "win\nmore"
    assert b["∑分"] == 9358.0 and b["分/d"] is None and b["i9"] is None
    assert fake.calls == [("1分+1s", "A2:AA400")]


def test_fetch_rows_raises_when_the_daemon_cannot_answer(wk, monkeypatch):
    monkeypatch.setattr(wk, "neon_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(RuntimeError, match="unreachable"):
        wk.fetch_rows()


def test_select_window_finds_a_label_stored_as_a_float(wk, monkeypatch):
    monkeypatch.setattr(wk, "neon_excel", FakeExcel(_weeks({
        2: {"A": "11.2", "C": "100.0"}, 3: {"A": "11.299999999999999", "C": "200.0"}})))
    target, prior = wk.select_window(wk.fetch_rows(), "11.3", 8)
    assert target["∑分"] == 200.0 and [r["week"] for r in prior] == ["11.2"]
