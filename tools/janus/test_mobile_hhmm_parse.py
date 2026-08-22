#!/usr/bin/env python3
"""Regression: the mobile web edit/gap dialogs must accept colon-less times.

Bug (2026-08-21, user report): the edit dialog's start/end inputs are
inputmode="numeric" and prefilled "HH:MM". iOS's number pad has no colon key,
so after deleting the prefilled colon the user can't retype it — they submit a
bare "1512", which hit `_dt.time(*map(int, "1512".split(":")))` → time(1512) →
ValueError → "bad time format". A colon-less numpad entry must parse as 15:12.

Fix: `_hhmm_parts` accepts "HH:MM", "HHMM", "HMM", "H:MM"; edit_entry
normalizes both submitted times to canonical HH:MM before change-detection and
parsing; fill_gap parses through the same helper.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_mobile", HERE / "mobile.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mobile"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


@pytest.mark.parametrize("text,expected", [
    ("1512", (15, 12)),   # the exact colon-less numpad case
    ("15:12", (15, 12)),  # still accepts the prefilled colon form
    ("907", (9, 7)),      # short-hour, colon-less
    ("9:07", (9, 7)),
    ("0000", (0, 0)),
    ("2359", (23, 59)),
])
def test_hhmm_parts_accepts_colon_and_bare(text, expected):
    assert mod._hhmm_parts(text) == expected


@pytest.mark.parametrize("bad", ["", "2599", "2400", "1265", "12:60", "abcd", "1:1", "5"])
def test_hhmm_parts_rejects_invalid(bad):
    with pytest.raises(ValueError):
        mod._hhmm_parts(bad)
