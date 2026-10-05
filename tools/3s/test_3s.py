#!/usr/bin/env python3
"""/3s (2026-10-05): 4-4-5 week quarters, default quarter, rating averaging."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("s3", Path(__file__).parent / "3s.py")
s3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s3)


@pytest.mark.parametrize("q,first,last", [
    (1, date(2026, 1, 4), date(2026, 3, 29)),
    (3, date(2026, 7, 5), date(2026, 9, 27)),   # 7.1 .. 9.5
    (4, date(2026, 10, 4), date(2026, 12, 27)),
])
def test_quarter_weeks_445(q, first, last):
    w = s3.quarter_weeks(2026, q)
    assert len(w) == 13 and w[0] == first and w[-1] == last
    assert all(b - a == timedelta(days=7) for a, b in zip(w, w[1:]))


@pytest.mark.parametrize("today,expect", [
    (date(2026, 10, 5), (2026, 3)),    # week 10.1 → last completed is Q3
    (date(2026, 10, 3), (2026, 2)),    # still inside Q3 (week 9.5)
    (date(2026, 1, 10), (2025, 4)),
])
def test_default_quarter_is_last_completed(today, expect):
    assert s3.resolve_quarter(None, today) == expect


def test_explicit_quarter():
    assert s3.resolve_quarter("2026-Q2", date(2026, 10, 5)) == (2026, 2)
    assert s3.resolve_quarter("q4", date(2026, 10, 5)) == (2026, 4)


@pytest.mark.parametrize("vals,out", [
    (["MM", "MA ", "MA", "EE", "OL", None], ("MA", 4)),      # 2.0, OL/blank skipped
    (["MA", "MM", "MM", "MM", "MM"], ("MM", 5)),             # 1.2
    (["MA", "MA", "EE", "EE"], ("MA+", 4)),                  # 2.5 → round half even=2, +.5
    (["MM", "MA", "MA"], ("MA-", 3)),                        # 1.67 → MA, -.33
    ([None, "OL"], (None, 0)),
])
def test_rating_mean(vals, out):
    assert s3.rating_mean(vals) == out
