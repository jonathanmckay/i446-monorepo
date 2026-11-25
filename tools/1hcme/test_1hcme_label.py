"""1hcme's week-label lookup through excel-http (2026-11-25):
thursday_for_label no longer runs AppleScript. The colour fetch
(build_fetch_script) still does: the daemon cannot read cell colours."""
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
def hm(monkeypatch):
    spec = importlib.util.spec_from_file_location("one_hcme_label", HERE / "1hcme.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["one_hcme_label"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "run_bounded", lambda *a, **k: pytest.fail("raw ix-osa must not run"))
    return mod


def _sheet(rows):
    cells = {}
    for row, (label, d, wd) in rows.items():
        cells[("0s897", "A", row)] = label
        cells[("0s897", "B", row)] = long_date(d) if isinstance(d, dt.date) else d
        cells[("0s897", "C", row)] = wd
    return cells


ROWS = {
    323: ("", dt.date(2026, 11, 18), "W"),
    324: ("11.4", dt.date(2026, 11, 19), "Th"),
    325: ("", dt.date(2026, 11, 20), "F"),
    331: ("12.1", dt.date(2026, 11, 26), "Th"),
}


def test_thursday_for_label_returns_the_rows_date(hm, monkeypatch):
    fake = FakeExcel(_sheet(ROWS))
    monkeypatch.setattr(hm, "neon_excel", fake)
    assert hm.thursday_for_label("11.4") == dt.date(2026, 11, 19)
    assert hm.thursday_for_label("12.1") == dt.date(2026, 11, 26)
    assert fake.calls[0] == ("0s897", "A2:C600")


def test_label_stored_as_a_float_still_matches(hm, monkeypatch):
    rows = dict(ROWS)
    rows[317] = ("11.299999999999999", dt.date(2026, 11, 12), "Th")
    monkeypatch.setattr(hm, "neon_excel", FakeExcel(_sheet(rows)))
    assert hm.thursday_for_label("11.3") == dt.date(2026, 11, 12)


def test_only_a_thursday_row_with_a_real_date_counts(hm, monkeypatch):
    monkeypatch.setattr(hm, "neon_excel", FakeExcel(_sheet({
        10: ("11.4", dt.date(2026, 11, 18), "W"),          # label on a non-Thursday row
        11: ("11.4", "2026.11.19", "Th"),                  # date typed as text
        12: ("11.4", dt.date(2026, 11, 19), "Th")})))
    assert hm.thursday_for_label("11.4") == dt.date(2026, 11, 19)


def test_unknown_label_exits_with_the_same_message(hm, monkeypatch):
    monkeypatch.setattr(hm, "neon_excel", FakeExcel(_sheet(ROWS)))
    with pytest.raises(SystemExit) as e:
        hm.thursday_for_label("13.1")
    assert str(e.value) == "ERROR: no Thursday row labeled 13.1 in 0s897"


def test_exits_when_the_daemon_cannot_answer(hm, monkeypatch):
    monkeypatch.setattr(hm, "neon_excel", FakeExcel(error="excel-http unreachable"))
    with pytest.raises(SystemExit) as e:
        hm.thursday_for_label("11.4")
    assert str(e.value) == "excel-http unreachable"


def test_resolve_target_by_label_is_the_card_after_the_week_reviewed(hm, monkeypatch):
    monkeypatch.setattr(hm, "neon_excel", FakeExcel(_sheet(ROWS)))
    assert hm.resolve_target("11.4", dt.date(2026, 11, 25)) == dt.date(2026, 11, 26)
