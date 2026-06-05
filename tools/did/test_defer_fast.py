#!/usr/bin/env python3
"""Tests for defer-fast.py defaults.

Regression (2026-06-05): ctrl-d in dtd deferred everything to *tomorrow*
and claimed 5 points by default. New behavior: recurring tasks defer to
their next recurrence instance (staying recurring), and the default
claimed points is 2.
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "defer_fast", _HERE / "defer-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["defer_fast"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def df():
    return _load()


# ── next_instance ──────────────────────────────────────────────────────────

FRI = date(2026, 6, 5)  # a Friday


@pytest.mark.parametrize("due_string,base,expected", [
    ("every Friday", FRI, date(2026, 6, 12)),          # sunset case
    ("every day", FRI, date(2026, 6, 6)),
    ("every day at 9am", FRI, date(2026, 6, 6)),
    ("daily", FRI, date(2026, 6, 6)),
    ("every 3 days", FRI, date(2026, 6, 8)),
    ("every other day", FRI, date(2026, 6, 7)),
    ("every week", FRI, date(2026, 6, 12)),
    ("every 2 weeks", FRI, date(2026, 6, 19)),
    ("every other week", FRI, date(2026, 6, 19)),
    ("every weekday", FRI, date(2026, 6, 8)),          # Fri → Mon
    ("every mon, wed, fri", FRI, date(2026, 6, 8)),    # Fri → Mon
    ("every Tuesday", date(2026, 6, 2), date(2026, 6, 9)),
    ("every month", date(2026, 1, 31), date(2026, 2, 28)),  # day clamped
    ("every 15th", date(2026, 6, 15), date(2026, 7, 15)),
    ("every year", FRI, date(2027, 6, 5)),
    ("some unparseable thing", FRI, date(2026, 6, 12)),  # fallback +7
])
def test_next_instance(df, due_string, base, expected):
    assert df.next_instance(due_string, base) == expected


# ── default_target_date ────────────────────────────────────────────────────

def test_recurring_task_defaults_to_next_instance(df, monkeypatch):
    """A recurring weekly task defers to its next occurrence, not tomorrow."""
    class _D(date):
        @classmethod
        def today(cls):
            return FRI
    monkeypatch.setattr(df, "date", _D)
    task = {"due": {"is_recurring": True, "date": "2026-06-05",
                    "string": "every Friday"}}
    assert df.default_target_date(task) == "2026-06-12"


def test_overdue_recurring_advances_from_today(df, monkeypatch):
    """An overdue recurring task lands strictly in the future."""
    class _D(date):
        @classmethod
        def today(cls):
            return FRI
    monkeypatch.setattr(df, "date", _D)
    task = {"due": {"is_recurring": True, "date": "2026-05-29",
                    "string": "every Friday"}}
    assert df.default_target_date(task) == "2026-06-12"


def test_non_recurring_defaults_to_tomorrow(df, monkeypatch):
    class _D(date):
        @classmethod
        def today(cls):
            return FRI
    monkeypatch.setattr(df, "date", _D)
    task = {"due": {"is_recurring": False, "date": "2026-06-05"}}
    assert df.default_target_date(task) == "2026-06-06"


# ── default claimed points ─────────────────────────────────────────────────

def test_default_claimed_points_is_2(df):
    """Regression: default was 5; deferral should only claim 2."""
    assert df.DEFAULT_CLAIMED_POINTS == 2
    # And main() must use the constant, not a literal 5.
    src = (_HERE / "defer-fast.py").read_text()
    main_src = src[src.index("def main()"):]
    assert "DEFAULT_CLAIMED_POINTS" in main_src
    assert "else 5" not in main_src
