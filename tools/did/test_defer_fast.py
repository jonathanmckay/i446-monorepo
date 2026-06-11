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


# ── resolve_target ──────────────────────────────────────────────────────────

def test_resolve_target_defaults_to_tomorrow(df, monkeypatch):
    class _D(date):
        @classmethod
        def today(cls):
            return FRI
    monkeypatch.setattr(df, "date", _D)
    assert df.resolve_target(None) == "2026-06-06"
    assert df.resolve_target("") == "2026-06-06"


def test_resolve_target_bare_days(df, monkeypatch):
    """dtd ctrl-d passes the prompted day count as a bare integer."""
    class _D(date):
        @classmethod
        def today(cls):
            return FRI
    monkeypatch.setattr(df, "date", _D)
    assert df.resolve_target("3") == "2026-06-08"
    assert df.resolve_target("1") == "2026-06-06"


def test_resolve_target_iso_passthrough(df):
    assert df.resolve_target("2026-07-01") == "2026-07-01"


def test_overdue_recurring_advances_from_today(df, monkeypatch):
    """An overdue recurring parent advances from today (base = max(due, today)),
    landing strictly in the future."""
    class _D(date):
        @classmethod
        def today(cls):
            return FRI
    monkeypatch.setattr(df, "date", _D)
    monkeypatch.setattr(df, "create_task", lambda *a, **k: {"id": "stub"})
    monkeypatch.setattr(df, "close_task", lambda *_: None)
    advance = {}
    monkeypatch.setattr(df, "_api",
                        lambda method, path, body=None: advance.update(body or {}))
    task = {"id": "1", "content": "t [10]",
            "due": {"is_recurring": True, "date": "2026-05-29",
                    "string": "every Friday"}}
    out = df.handle_recurring(task, "2026-06-06", 2)
    assert out["next_recurrence"] == "2026-06-12"
    assert advance["due_date"] == "2026-06-12"


# ── default claimed points ─────────────────────────────────────────────────

def test_default_claimed_points_is_2(df):
    """Regression: default was 5; deferral should only claim 2."""
    assert df.DEFAULT_CLAIMED_POINTS == 2
    # And main() must use the constant, not a literal 5.
    src = (_HERE / "defer-fast.py").read_text()
    main_src = src[src.index("def main()"):]
    assert "DEFAULT_CLAIMED_POINTS" in main_src
    assert "else 5" not in main_src


# ── find_task disambiguation ────────────────────────────────────────────────
# Regression (2026-06-06): "defer failed: call dad" — two tasks shared the
# name and differed only in [N]; dtd's stripped query matched both and
# defer-fast bailed. find_task must prefer an exact content match.

def test_find_task_prefers_exact_match_among_duplicates(df, monkeypatch):
    tasks = [
        {"id": "1", "content": "call dad (20) [20]"},
        {"id": "2", "content": "call dad (20) [15]"},
    ]
    monkeypatch.setattr(df, "_fetch_tasks", lambda filt: tasks)
    t = df.find_task("call dad (20) [15]")
    assert t["id"] == "2"


def test_find_task_still_errors_on_true_ambiguity(df, monkeypatch):
    tasks = [
        {"id": "1", "content": "call dad (20) [20]"},
        {"id": "2", "content": "call dad (20) [15]"},
    ]
    monkeypatch.setattr(df, "_fetch_tasks", lambda filt: tasks)
    with pytest.raises(SystemExit):
        df.find_task("call dad")  # genuinely ambiguous stripped query


def test_find_task_single_substring_match_unchanged(df, monkeypatch):
    tasks = [{"id": "9", "content": "call dad (20) [20]"}]
    monkeypatch.setattr(df, "_fetch_tasks", lambda filt: tasks)
    assert df.find_task("call dad")["id"] == "9"
