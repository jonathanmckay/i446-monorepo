#!/usr/bin/env python3
"""Regression: the /1hcb weekly report above the survey (2026-10-30: "how did
I do for hcbp vs. target, calories, food groups; where did I start with the
overall ledger, where did I end?").

Fixture rows are hcbi 10/22..10/28 as Excel computed them (rows 297-303), as
a B:AA read_range returns them: strings, "" for empty cells.
"""
import importlib.util
import re
from pathlib import Path

_spec = importlib.util.spec_from_file_location("hcb1", Path(__file__).with_name("1hcb.py"))
hcb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hcb)

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _row(day, wd, groups, protein, cal, x, y, aa, b=None):
    r = [""] * 26
    r[hcb._B] = b if b is not None else day
    r[hcb._C] = wd
    for k, v in enumerate(groups):
        r[hcb._E + k] = "" if v is None else str(v)
    r[hcb._P], r[hcb._U], r[hcb._X], r[hcb._Y], r[hcb._AA] = map(str, (protein, cal, x, y, aa))
    return r


WEEK = [  # 10/22 Th .. 10/28 W
    _row("10/22", "Th", (1, 0, 1, 0, 0, 0, 0, 1, 1, 1, 2), 158, 2340, 30, 5, 30),
    _row("10/23", "F", (0, 0, 1, 3, 3, 5, 2, 0, 0, 1, 2), 114, 2130, 64.71, 34, 64.71),
    _row("10/24", "S", (2, 0, 1, 1, 1, 6, 5, 0, 1, 1, 2), 78, 1580, 112.43, 202, 52.43),
    _row("10/25", "S", (0, 1, 2, 1, 2, 3, 3, 0, 0, 1, 2), 94, 1630, 97.57, 109, 147.57),
    _row("10/26", "M", (1, None, 3, None, None, 2, 3, 3, 6, 2, 2), 83, 1700, 110.07, 0, 50.07),
    _row("10/27", "T", (1, 1, 1, 3, 3, 1, 0, 3, 0, 1, 2), 87, 2220, 38.64, 10, -81.36),
    _row("10/28", "W", (0, 1, 0, 2, 2, 1, 0, 0, 0, 1, 2), 89, 1750, 61.64, 36, 181.64),
]
FIRST = 297
# 10/29 logged, 10/30 has a text food cell (COUNTIF counts it, SUMIF skips
# it), 10/31 unlogged (-50 and hcbp 0: excluded from both counts).
TAIL = [
    _row("10/29", "Th", (0,) * 11, 72, 1790, 56.29, 30, 116.29),
    _row("10/30", "F", (0,) * 11, 82, 1340, 58.14, 0, "it"),
    _row("10/31", "S", (0,) * 11, 0, 0, -50, 0, -50),
    [""] * 26,
]
LEDGER = -1483.47


def _ledger_row(total):
    r = [""] * 26
    r[hcb._B], r[hcb._X] = "2026.0", str(total)
    return r


def _report(social=None):
    rows = WEEK + TAIL + [_ledger_row(LEDGER)]
    return [ANSI.sub("", ln) for ln in hcb.build_report(rows, FIRST, social)]


def test_hcbp_vs_45_per_day_and_days_moved():
    hcbp = next(l for l in _report() if l.startswith("hcbp"))
    assert "396分 / 315 (45/day)" in hcbp and "moved 6/7 days" in hcbp, hcbp


def test_social_hcbp_per_fiscal_week():
    hcbp = next(l for l in _report({"10.3": "86", "10.4": "20"}) if l.startswith("hcbp"))
    assert "social 10.3 86/60 10.4 20/60" in hcbp, hcbp


def test_calories_vs_1800():
    cal = next(l for l in _report() if l.startswith("cal"))
    assert "avg 1907 (7 days logged)" in cal and "over 1800 on 3" in cal, cal
    assert "protein 100g/100" in cal, cal


def test_food_groups_vs_daily_dozen():
    out = _report()
    food = next(l for l in out if l.startswith("food"))
    assert "/day vs 86" in food and "/21 per day (7 days)" in food, food
    short = next(l for l in out if "short:" in l)
    # worst ratio first: beans 5 servings / 7 days = 0.7 of 3
    assert short.split("short: ")[1].startswith("beans 0.7/3"), short
    assert "greens 1.0/2" in short and "water 2.0/3" in short, short
    hit = next(l for l in out if "hit:" in l)
    assert "spices" in hit and "vegetables" in hit, hit


def test_ledger_start_and_end_of_week_mirror_excel_countif():
    """Start = year total minus every day from the week's Thursday on; end =
    minus only the days after Wednesday. The text cell counts -86 (COUNTIF)
    but adds nothing (SUMIF), exactly as hcbi X380 does."""
    tail = (116.29 - 86) + (30 - 45) + (0 - 86)
    week = sum(hcb.ledger_contrib(r, FIRST + i) for i, r in enumerate(WEEK))
    end, start = LEDGER - tail, LEDGER - tail - week
    line = next(l for l in _report() if l.startswith("ledger"))
    assert f"{start:,.0f} → {end:,.0f}" in line, (line, start, end)
    assert f"year now {LEDGER:,.0f}" in line, line


def test_food_column_switches_to_AA_from_q3():
    r = _row("1/1", "Th", (0,) * 11, 0, 0, 100, 0, 10)
    assert hcb.ledger_contrib(r, 187) == 100 - 86
    assert hcb.ledger_contrib(r, 188) == 10 - 86


def test_report_runs_before_survey_and_never_blocks_it():
    src = Path(__file__).with_name("1hcb.py").read_text()
    main = src[src.index("def main("):]
    assert main.index("for ln in report") < main.index("1. What was your lowest weight")
    assert "report unavailable" in main
