"""tools/pnl: GL mapping (mortgage interest on 800200), fund registry, debt as-of.

Regression for 2026-10-29: interest was mapped only to GL 80310, but AppFolio
posts it to 800200, so every post-cutover month booked $0 interest and fund /
portfolio cashflow and DSCR were overstated.
"""
import importlib.util
from datetime import date
from pathlib import Path

import pytest

PNL_DIR = Path(__file__).resolve().parent.parent / "tools" / "pnl"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PNL_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pf():
    return _load("pnl_fast_t", "pnl-fast.py")


@pytest.fixture
def ppf():
    return _load("portfolio_pnl_fast_t", "portfolio-pnl-fast.py")


MONTHS = ["2026-08", "2026-09"]


def _row(code, name, aug, sep):
    return {"account_code": code, "account_name": name,
            "months": [{"id": "2026-08", "value": str(aug)},
                       {"id": "2026-09", "value": str(sep)}]}


# Shape of AppFolio twelve_month_income_statement detail rows (kn47, Sep 2026).
FIXTURE_ROWS = [
    _row("40110", "Gross Potential Rent", 240000, 240000),
    _row("53003", "Property Taxes", 20000, 20000),
    _row("800200", "Mortgage - Interest Expense", 105095.84, 105003.24),
    _row("800100", "Mortgage - Principal Paydown", 15000, 15000),
    _row("80410", "Legal and Professional Services", 5912.10, 8980.00),
    _row(None, "Total Income", 240000, 240000),
    _row(None, "Total Expense", 146007.94, 148983.24),
]


def test_gl_800200_is_mortgage_interest(pf):
    assert "800200" in pf.MORTGAGE_INTEREST_CODES
    assert pf.ACCOUNT_LOOKUP["800200"] == ("below_noi", "Mortgage Interest")
    assert pf.ACCOUNT_LOOKUP["800100"] == ("below_noi", "Mortgage Principal")


def test_interest_rows_captured_and_flow_into_cashflow_and_dscr(pf):
    monthly, _, _, _ = pf.parse_api_rows(FIXTURE_ROWS, MONTHS)
    assert monthly["2026-09"]["Mortgage Interest"] == pytest.approx(105003.24)
    assert monthly["2026-09"]["Mortgage Principal"] == pytest.approx(15000)

    s = pf.compute_summaries(monthly, MONTHS, units=245)["2026-09"]
    noi = 240000 - 20000
    assert s["NOI"] == pytest.approx(noi)
    assert s["Mortgage"] == pytest.approx(120003.24)
    assert s["Cashflow"] == pytest.approx(noi - 105003.24 - 15000 - 8980.00)
    assert s["DSCR"] == pytest.approx(noi / 120003.24)


def test_unmapped_gl_is_recorded_not_silently_dropped(pf):
    pf.UNMAPPED_GL.clear()
    rows = FIXTURE_ROWS + [_row("899999", "Some New Debt GL", 0, 1234)]
    pf.parse_api_rows(rows, MONTHS)
    assert pf.UNMAPPED_GL[("899999", "Some New Debt GL")] == pytest.approx(1234)
    assert not any(c == "800200" for c, _ in pf.UNMAPPED_GL)


def test_budget_struct_maps_interest(pf):
    rows = [{"account_number": "800200", "account_name": "Mortgage - Interest Expense",
             "total_period_actual": "105003.24", "total_period_budget": "105000",
             "total_period_variance": "-3.24"}]
    b = pf.make_budget_struct(rows)
    assert b["la"]["Mortgage Interest"] == pytest.approx(105003.24)


def test_registry_unit_counts_match_live_directory(pf):
    # AppFolio unit_directory 2026-10-29, dwelling units only.
    expected = {"kn47": 245, "tc68": 108, "o155": 52, "tc34": 24, "rl16": 96,
                "rl21": 22, "w225": 36, "c313": 39, "c702": 178}
    for code, n in expected.items():
        assert pf.PROPERTIES[code]["units"] == n, code


def test_c702_in_fund_iv_and_sf21_personal(pf, ppf):
    assert pf.PROPERTIES["c702"]["fund"] == "fund-iv"
    assert pf.PROPERTIES["c702"]["ids"] == ["2431"]
    assert pf.PROPERTIES["sf21"]["fund"] == "personal"
    assert not pf.is_fund_property("sf21")
    assert pf.is_fund_property("c702")
    assert ppf.all_funds() == ["fund-0", "fund-i", "fund-ii", "fund-iii", "fund-iv"]
    assert "c702" in ppf.get_fund_properties("fund-iv")
    assert all("sf21" not in ppf.get_fund_properties(f) for f in ppf.all_funds())


def test_portfolio_shares_registry_with_pnl_fast(ppf):
    pf2 = _load("pnl_fast_t2", "pnl-fast.py")
    assert ppf.PROPERTIES == pf2.PROPERTIES
    assert ppf.SREO_VALUES == pf2.SREO_VALUES


def test_debt_as_of_is_explicit_and_flags_staleness(pf):
    assert pf.debt_as_of_note(date(2026, 6, 15)) == pf.DEBT_AS_OF
    stale = pf.debt_as_of_note(date(2026, 10, 29))
    assert pf.DEBT_AS_OF in stale and "STALE" in stale and "151 days" in stale


def test_equity_section_prints_debt_as_of(pf):
    md, _ = pf.build_equity("kn47", 25400, None, 19_050_000, 1_000_000, 0, None)
    assert f"| Debt As Of | {pf.DEBT_AS_OF} |" in md


def test_reports_base_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PNL_REPORTS_BASE", str(tmp_path))
    mod = _load("pnl_fast_env", "pnl-fast.py")
    assert mod.REPORTS_BASE == tmp_path
