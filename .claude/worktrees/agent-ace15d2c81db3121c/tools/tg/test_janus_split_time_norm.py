#!/usr/bin/env python3
"""Regression: the ^P split cut-point must accept plain/short/colon times and
must never crash on an out-of-range one.

Bug (2026-08-21, user report: "janus edit time returns error" / "won't allow
numbers"). The split cut-point matcher was a bare ``re.fullmatch(r"(\\d{2})(\\d{2})")``
and then called ``_hhmm_to_dt`` OUTSIDE any try:

  - a short-hour time like ``907`` (09:07) failed the 4-digit match → "not an
    HHMM time" error flash, so a natural time "won't [go] in";
  - an out-of-range 4-digit like ``2599`` PASSED the match, reached
    ``_hhmm_to_dt(... , 25, 99)`` and raised an uncaught ``ValueError`` that
    crashed the key handler.

Fix: ``_norm_hhmm`` normalizes ``HHMM`` / ``HMM`` / ``H:MM`` / ``HH:MM`` to
canonical ``HHMM`` and validates the range (00-23 / 00-59), returning None for
anything invalid so the split arm flashes a clean cancel instead of crashing.
It is deliberately lone-token only and NOT wired into the entry-retime range
grammar (which stays 4-digit to protect a description's ``1:1``).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "janus", Path(__file__).resolve().parent / "janus.py")
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_four_bare_digits_still_normalize():
    assert mod._norm_hhmm("1512") == "1512"
    assert mod._norm_hhmm("0907") == "0907"


def test_short_hour_normalizes():
    assert mod._norm_hhmm("907") == "0907"
    assert mod._norm_hhmm("930") == "0930"


def test_colon_forms_normalize():
    assert mod._norm_hhmm("9:07") == "0907"
    assert mod._norm_hhmm("15:55") == "1555"


def test_out_of_range_hour_is_rejected_not_crash():
    # The exact crash input: matched the old (\d{2})(\d{2}) but hour 25 blew up
    # datetime() outside the try. Must now be a clean None (→ "not a valid time").
    assert mod._norm_hhmm("2599") is None
    assert mod._norm_hhmm("2400") is None


def test_bad_minutes_rejected():
    assert mod._norm_hhmm("1265") is None  # minute 65
    assert mod._norm_hhmm("12:60") is None


def test_description_colon_like_1_1_is_not_a_time():
    # The design invariant the retime grammar protects, upheld here too: a
    # single-digit-minute colon token ("1:1") is not a time.
    assert mod._norm_hhmm("1:1") is None
    assert mod._norm_hhmm("carolina") is None


def test_norm_then_hhmm_to_dt_roundtrips():
    import datetime as dt
    d = dt.date(2026, 8, 21)
    got = mod._hhmm_to_dt(d, mod._norm_hhmm("9:07"))
    assert (got.hour, got.minute) == (9, 7)
