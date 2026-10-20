#!/usr/bin/env python3
"""Regression test for a day-wrap bug in the HHMM-annotated variable-habit
elapsed-time calc ("xk22 2350" = started at 23:50).

Bug: `now_min - start_min` had no wraparound handling, so once local time
crossed midnight after the HHMM was typed, the delta went negative and
`max(1, delta)` silently clamped it to 1 minute instead of the real elapsed
time. Found auditing DTD for international-travel hardening (2026-08-23) —
more likely to fire when a "day" is short or a TZ change shifts the clock,
but it was already live-broken for any ordinary midnight crossing.

Both call sites (trailing-number "xk22 2350" and leading-number "2350 xk22"
forms) parse identically and must both wrap correctly.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load(monkeypatch, frozen_now):
    spec = importlib.util.spec_from_file_location("df_wrap", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["df_wrap"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._daytime, "local_now", lambda: frozen_now)
    return mod


@pytest.mark.parametrize("raw", ["xk22 2350", "2350 xk22"])
def test_elapsed_wraps_across_midnight(monkeypatch, raw):
    """Started 23:50, it's now 00:15 the next local day: 25 elapsed minutes,
    not the pre-fix clamp of 1."""
    import datetime as dt
    frozen = dt.datetime(2026, 8, 24, 0, 15)
    mod = _load(monkeypatch, frozen)
    items = mod.parse_input(raw)
    assert len(items) == 1
    assert items[0].time_value == 25, items[0].time_value


@pytest.mark.parametrize("raw", ["xk22 1823", "1823 xk22"])
def test_elapsed_same_day_still_correct(monkeypatch, raw):
    """Sanity check the fix didn't disturb the ordinary same-day case."""
    import datetime as dt
    frozen = dt.datetime(2026, 8, 23, 18, 45)
    mod = _load(monkeypatch, frozen)
    items = mod.parse_input(raw)
    assert len(items) == 1
    assert items[0].time_value == 22, items[0].time_value
