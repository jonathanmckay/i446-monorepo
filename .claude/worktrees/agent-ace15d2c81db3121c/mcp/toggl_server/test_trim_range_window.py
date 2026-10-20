#!/usr/bin/env python3
"""Regression (2026-07-27): trim_range must fetch a ±1-day window.

Toggl's start_date/end_date filter is UTC-based, so a [day, day+1) local
window drops every evening-PT entry and the running timer (both land on the
next UTC day) — "2101-2115 snack" failed to split the running run entry
because trim_range literally never saw it."""
import re
from pathlib import Path

SRC = (Path(__file__).parent / "toggl_api.py").read_text()


def test_trim_range_fetches_widened_window():
    body = SRC[SRC.index("def trim_range"):]
    body = body[:body.index("\ndef ")] if "\ndef " in body[10:] else body
    assert "day - timedelta(days=1)" in body, "window must start a day early"
    assert "day + timedelta(days=2)" in body, "window must end a day late"
    assert re.search(r"start_date=day\.isoformat", body) is None, \
        "the old same-day window must be gone"
