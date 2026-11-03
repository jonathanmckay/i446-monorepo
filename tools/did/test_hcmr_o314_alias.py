#!/usr/bin/env python3
"""Feature (2026-07-31): "/did on a hcmr time entry maps to o314 in Neon,
and the same 长o314 cumulative-during-the-day rules apply."

hcmr is treated as the same underlying activity as o314, just a different
Toggl description:
- Completing a "hcmr" 0₦ item writes into o314's own 0n column (ZERO_N_ALIASES),
  not a column of its own.
- With no value typed, AQ is set to the day's o314 + hcmr Toggl minutes
  (2026-11-03; previously hcmr read only its own entries and clobbered AQ).
- The weekly "长o314" card's 30-minute threshold sums o314 AND hcmr Toggl
  minutes together (THRESHOLD_1N["长o314"]["toggl"] = ("o314", "hcmr")).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_hcmr", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_hcmr"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    mod = _load()
    yield mod
    mod._TOGGL_TODAY = None


HEADERS = {"0n": {"o314": "AQ"}, "1n": {}}
TQ = {"0neon": [], "夜neon": [], "1neon": [], "today": []}


def _route_one(df, text, headers=HEADERS):
    items = df.parse_input(text)
    res = df.route_items(items, headers, TQ, skip_todoist=True)
    assert len(res) == 1
    return res[0]


def test_zero_n_aliases_maps_hcmr_to_o314(df):
    assert df.ZERO_N_ALIASES["hcmr"] == "o314"


def test_hcmr_completion_writes_to_o314s_column(df, monkeypatch):
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 12)
    r = _route_one(df, "hcmr")
    assert r.step == "0n"
    assert r.col_num == "AQ", "hcmr must land in o314's own 0n column, not a column of its own"


def test_o314_completion_still_writes_to_its_own_column(df, monkeypatch):
    """Sanity check the alias doesn't disturb o314 completing itself."""
    monkeypatch.setattr(df, "toggl_minutes_for", lambda name: 12)
    r = _route_one(df, "o314")
    assert r.step == "0n" and r.col_num == "AQ"


def test_hcmr_and_o314_share_one_day_total(df):
    """2026-11-03: AQ is the DAY's o314+hcmr total (lib/neon/long_bonus.py).
    A bare hcmr completion used to write only hcmr's own minutes, which
    overwrote the o314 minutes already in AQ; it now sets AQ to both."""
    df._TOGGL_TODAY = [
        {"description": "hcmr", "duration": 900},   # 15 min
        {"description": "o314", "duration": 6000},  # 100 min
    ]
    r = _route_one(df, "hcmr")
    assert r.write_value == 115
    assert not df._adds_0n(r)   # set, not add: it IS the day total


def test_threshold_1n_toggl_key_includes_both_names(df):
    assert df.THRESHOLD_1N["长o314"]["toggl"] == ("o314", "hcmr")


@pytest.mark.parametrize("text", ["长o314", "long o314"])
def test_long_o314_is_an_o314_completion(df, text):
    """2026-11-03: "/did long o314" folds into o314. It writes AQ's day total
    and the 长o314 bonus follows from AQ's change after the write
    (test_o314_long_bonus.py), so it can't double-credit the auto bonus."""
    df._TOGGL_TODAY = [
        {"description": "o314", "duration": 600},   # 10 min
        {"description": "hcmr", "duration": 1200},  # 20 min
    ]
    r = _route_one(df, text)
    assert r.step == "0n" and r.col_num == "AQ" and r.write_value == 30


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
