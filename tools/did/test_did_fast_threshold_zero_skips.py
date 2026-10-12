#!/usr/bin/env python3
"""Regression (bug 2026-10-12): "long o314 has come up every day for three
days even though I'm delaying it to the next weekly instance." JM answered
dtd's minutes prompt with 0; did-fast logged "0m < 30m threshold, no credit"
and never touched the card (due Thu 10/08, every Thursday), so it reappeared
on every refresh. An explicit 0 on a 长 habit now skips the recurring card
to its next occurrence (defer-fast's delay-0 path); blank or 15m don't."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("did_fast_tz0", _HERE / "did-fast.py")
df = importlib.util.module_from_spec(_SPEC)
sys.modules["did_fast_tz0"] = df
_SPEC.loader.exec_module(df)  # type: ignore[union-attr]

TASK = {"id": "6gJPwfxQjPCxGFwG", "content": "long o314 (30) [15]",
        "due": {"date": "2026-10-08", "string": "every Thursday", "is_recurring": True}}


def _route(raw):
    return SimpleNamespace(item=df.parse_input(raw)[0])


def _patch(monkeypatch, task=TASK):
    calls = []
    monkeypatch.setattr(df._df, "find_task_by_id", lambda tid: task)
    monkeypatch.setattr(df._df, "find_task", lambda name: task)

    def fake_handle(t, target, pts, skip_copy=False):
        calls.append((t["id"], skip_copy))
        return {"target_date": "2026-10-15"}
    monkeypatch.setattr(df._df, "handle_recurring", fake_handle)
    return calls


def test_explicit_zero_skips_recurring_card_to_next_occurrence(monkeypatch):
    calls = _patch(monkeypatch)
    assert df.skip_threshold_zero(_route("long o314 0"), TASK["id"]) == "2026-10-15"
    assert calls == [(TASK["id"], True)]


def test_blank_or_partial_minutes_leave_card_open(monkeypatch):
    calls = _patch(monkeypatch)
    assert df.skip_threshold_zero(_route("long o314"), TASK["id"]) is None
    assert df.skip_threshold_zero(_route("long o314 15"), TASK["id"]) is None
    assert calls == []


def test_non_recurring_card_is_not_touched(monkeypatch):
    one_off = {**TASK, "due": {"date": "2026-10-08", "is_recurring": False}}
    calls = _patch(monkeypatch, one_off)
    assert df.skip_threshold_zero(_route("long o314 0"), TASK["id"]) is None
    assert calls == []
