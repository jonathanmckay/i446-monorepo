"""Regression tests for the eat card's last-meal lookup.

Request (2026-06-25): the /inbound eat card should show the most-recent meal
logged today (from Neon hcbi) plus its time-band glyph, e.g. "last eaten:
mocha + tacos · 午". last_meal_today() reads hcbi through the excel-http
client and returns (food, band_glyph) of the latest populated Earthly-Branch band, or None.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("_two_n", HERE / "-2n.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()


# 2026-11-25: the read moved from AppleScript (ix-osa.py) to the excel-http
# client: one date lookup on hcbi, one range read of AK:BC on that row.
from datetime import datetime

TODAY = f"{datetime.now().month}/{datetime.now().day}"
ROW = 331


class FakeExcel:
    """lib/neon/excel.py stand-in: `band_cells` = {"AT": "mocha"} on today's row."""

    def __init__(self, band_cells=None, row=ROW, range_ok=True):
        self.band_cells = band_cells or {}
        self.row = row
        self.range_ok = range_ok
        self.calls = []

    def lookup_row(self, sheet, date_str):
        self.calls.append(("lookup_row", sheet, date_str))
        return self.row if (sheet, date_str) == ("hcbi", TODAY) else None

    def read_range(self, sheet, rng):
        self.calls.append(("read_range", sheet, rng))
        if not self.range_ok:
            return {"ok": False, "error": "excel-http unreachable"}
        assert (sheet, rng) == ("hcbi", f"AK{self.row}:BC{self.row}")
        cols = [m._col_letter(n) for n in range(m._col_num("AK"), m._col_num("BC") + 1)]
        return {"ok": True, "row": self.row, "rows": [[self.band_cells.get(c, "") for c in cols]]}


def _use(monkeypatch, fake):
    monkeypatch.setattr(m, "_neon_excel", lambda: fake)
    return fake


def test_parses_glyph_and_food(monkeypatch):
    fake = _use(monkeypatch, FakeExcel({"AT": "mocha + tacos"}))
    assert m.last_meal_today() == ("mocha + tacos", "午")
    assert fake.calls == [("lookup_row", "hcbi", TODAY),
                          ("read_range", "hcbi", f"AK{ROW}:BC{ROW}")]


def test_latest_populated_band_wins(monkeypatch):
    # Kcal / protein cells between the food-name cells must not be read as food.
    _use(monkeypatch, FakeExcel({"AK": "cheese + mocha", "AL": "350.0", "AM": "20.0",
                                 "AW": "asha small tea", "AX": "120.0"}))
    assert m.last_meal_today() == ("asha small tea", "未")


def test_strips_whitespace(monkeypatch):
    _use(monkeypatch, FakeExcel({"AW": "  asha small tea \n"}))
    assert m.last_meal_today() == ("asha small tea", "未")


def test_none_when_ix_unreachable(monkeypatch):
    # lookup fails (daemon down → lookup_row None), or the range read does.
    _use(monkeypatch, FakeExcel({"AT": "x"}, row=None))
    assert m.last_meal_today() is None
    _use(monkeypatch, FakeExcel({"AT": "x"}, range_ok=False))
    assert m.last_meal_today() is None


def test_none_when_client_cannot_load(monkeypatch):
    def boom():
        raise ImportError("no neon lib")
    monkeypatch.setattr(m, "_neon_excel", boom)
    assert m.last_meal_today() is None


def test_none_when_no_meal_logged(monkeypatch):
    fake = _use(monkeypatch, FakeExcel({}))
    assert m.last_meal_today() is None
    assert [c[0] for c in fake.calls] == ["lookup_row", "read_range"]   # it did read


def test_none_on_malformed_reply(monkeypatch):
    class Short(FakeExcel):
        def read_range(self, sheet, rng):
            return {"ok": True, "rows": [["only one cell"]]}   # row shorter than AK:BC

    class NoRows(FakeExcel):
        def read_range(self, sheet, rng):
            return {"ok": True}

    for fake in (Short(), NoRows()):
        _use(monkeypatch, fake)
        assert m.last_meal_today() is None


def test_bands_ordered_earliest_to_latest():
    # The scan keeps the LAST non-empty band, so ordering must be
    # chronological for "most recent meal" to be correct.
    glyphs = [g for g, _ in m._HCBI_BANDS]
    assert glyphs == ["卯", "辰", "巳", "午", "未", "申", "戌"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
