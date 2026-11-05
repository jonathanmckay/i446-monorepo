"""--raw path (2026-11-01, Todoist 66jC2ghQ28r6xHqc): 2s-fast.py computes
scorecard C/D/E/J (and Y when its formula is broken) from raw 0分 rows instead
of the 1分+1s M.1 formulas, several of which point at the wrong rows/columns
(AQ15 E39:E67, AR28/AR32 col F, AW36 5-week sum /4) or carry #REF! (C43).

The fixture is synthetic (no live data): a fiscal-April 0分 B:Q block shaped
exactly like the excel-http /read_range response, including the narrow NBSP
Excel puts before AM."""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures/0fen-synthetic-2026-04.json"


def _load():
    spec = importlib.util.spec_from_file_location("twos_fast_raw", HERE / "2s-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["twos_fast_raw"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod(monkeypatch):
    m = _load()
    from neon import excel as neon_excel
    fx = json.loads(FIXTURE.read_text())
    calls = []

    def fake_read_range(sheet, rng):
        calls.append((sheet, rng))
        return fx
    monkeypatch.setattr(neon_excel, "read_range", fake_read_range)
    m._calls = calls
    return m


# v173 anchors: 0分 rows of each label-month's week 1 (live-verified 2026-11-01).
@pytest.mark.parametrize("month,row,weeks", [
    (4, 93, 4), (5, 121, 4), (6, 149, 5), (7, 184, 4),
    (8, 212, 4), (9, 240, 5), (10, 275, 4)])
def test_fiscal_month_rows_match_live_anchors(month, row, weeks):
    m = _load()
    start, n = m.fiscal_month(2026, month)
    assert m.fen_row(start) == row
    assert n == weeks
    assert start.weekday() == 6  # Sunday


def test_first_row_of_year_is_row_2():
    m = _load()
    assert m.fen_row(date(2026, 1, 4)) == 2
    assert m.fen_row(date(2026, 10, 31)) == 302


def test_compute_raw_follows_excel_countif_semantics(mod):
    days, n, r0 = mod.read_fen_month(2026, 4)
    assert (n, r0, len(days)) == (4, 93, 28)
    assert mod._calls == [("0分", "B93:Q120")]
    r = mod.compute_raw(days, n)
    # C: 600 < E < 1000, both bounds exclusive; 3000 sentinel and 0 excluded
    assert r["C"] == pytest.approx(13 / 28)
    # D: E > 1 — the 3000 sentinel counts, E == 1 and text/blank do not
    assert r["D"] == pytest.approx(17 / 28)
    # E: F > 0 over F, never E (the AR28/AR32 bug counted F for com0)
    assert r["E"] == pytest.approx(10 / 28)
    # J: mean of weekly AVERAGE(Q); all-blank week 3 skipped, blanks ignored
    assert r["J"] == pytest.approx((70 + 100 + 6) / 3)
    # Y: mean of weekly SUM(D) / 7, all-blank week skipped
    assert r["Y"] == pytest.approx((7000 + 14000 + 9800) / 3 / 7)


def test_denominator_is_fixed_month_length_not_filled_days(mod):
    r = mod.compute_raw([{"E": 700, "F": 1}] + [{}] * 34, 5)
    assert r["C"] == pytest.approx(1 / 35)
    assert r["J"] is None and r["Y"] is None


def test_date_mismatch_refuses(mod):
    from neon import excel as neon_excel
    fx = json.loads(FIXTURE.read_text())
    fx["rows"][3][0] = "Monday, April 6, 2026 at 12:00:00 AM"
    neon_excel.read_range = lambda s, r: fx
    with pytest.raises(SystemExit, match="layout moved"):
        mod.read_fen_month(2026, 4)


def _run_main(mod, monkeypatch, capsys, formula_vals, extra=()):
    monkeypatch.setattr(mod, "read_neon_month", lambda month: ["15"] + formula_vals)
    monkeypatch.setattr(mod, "write_scorecard",
                        lambda *a, **k: pytest.fail("dry-run must not write"))
    monkeypatch.setattr(sys, "argv", ["2s-fast.py", "2026-04", "--dry-run", *extra])
    mod.main()
    out = capsys.readouterr().out
    return dict(l.strip().split(" ← ") for l in out.splitlines() if " ← " in l), out


# AQ..BA + BC as Excel returns them: C wrong (0.39), the rest arbitrary
FORMULA = ["0.39", "0.96", "0.75", "25", "153", "99", "128", "292", "153",
           "234", "278", "1557.27"]


def test_without_raw_formula_values_paste_through(mod, monkeypatch, capsys):
    got, _ = _run_main(mod, monkeypatch, capsys, list(FORMULA))
    assert got["C"] == "0.39" and got["J"] == "128" and got["Y"] == "1557"


def test_raw_overrides_cdej_and_keeps_readable_y(mod, monkeypatch, capsys):
    got, out = _run_main(mod, monkeypatch, capsys, list(FORMULA), ["--raw"])
    assert (got["C"], got["D"], got["E"], got["J"]) == ("0.46", "0.61", "0.36", "59")
    assert got["Y"] == "1557"  # formula Y readable: kept (may hold manual adjustments)
    assert got["F"] == "25" and got["K"] == "292"  # non-raw columns untouched
    assert "C 0.39→0.46" in out


def test_raw_fills_y_when_formula_is_ref_error(mod, monkeypatch, capsys):
    vals = list(FORMULA)
    vals[-1] = "missing value"  # BC41 renders #REF! as missing value over ix-osa
    got, out = _run_main(mod, monkeypatch, capsys, vals, ["--raw"])
    assert got["Y"] == "1467"
    assert 'Y ""→1467' in out
