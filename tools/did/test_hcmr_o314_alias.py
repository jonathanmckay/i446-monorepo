#!/usr/bin/env python3
"""Feature (2026-07-31): "/did on a hcmr time entry maps to o314 in Neon,
and the same 长o314 cumulative-during-the-day rules apply."

hcmr is treated as the same underlying activity as o314, just a different
Toggl description:
- Completing a "hcmr" 0₦ item writes into o314's own 0n column (ZERO_N_ALIASES),
  not a column of its own.
- hcmr's OWN Toggl minutes (when no value is typed) still come from Toggl
  entries actually named "hcmr" -- the alias only affects which Neon column
  the write lands in, not which entries the "how long did this take" lookup
  reads.
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


def test_hcmr_minutes_lookup_uses_the_unaliased_name(df):
    """The 'how long did THIS entry take' fallback must read Toggl entries
    actually named 'hcmr', not silently look up 'o314' instead -- otherwise
    an o314 entry logged earlier the same day would leak into an unrelated
    hcmr completion's minute count."""
    calls = []
    real = df.toggl_minutes_for

    def spy(name):
        calls.append(name)
        return real(name)

    df.toggl_minutes_for = spy
    df._TOGGL_TODAY = [
        {"description": "hcmr", "duration": 900},   # 15 min
        {"description": "o314", "duration": 6000},  # 100 min -- must NOT be counted
    ]
    r = _route_one(df, "hcmr")
    assert calls == ["hcmr"]
    assert r.write_value == 15


def test_threshold_1n_toggl_key_includes_both_names(df):
    assert df.THRESHOLD_1N["长o314"]["toggl"] == ("o314", "hcmr")


def test_long_o314_credits_from_hcmr_minutes_alone(df):
    """The weekly 长o314 card must earn credit purely from hcmr entries,
    since hcmr is the same underlying activity -- not require any o314
    entries to also exist that day."""
    df._TOGGL_TODAY = [{"description": "hcmr", "duration": 1800}]  # 30 min
    headers = {"0n": {}, "1n": {"长o314": "V"}}
    r = _route_one(df, "长o314", headers=headers)
    assert r.step == "1n"
    assert r.write_value == 15  # .5/m * 30


def test_long_o314_sums_o314_and_hcmr_minutes_together(df):
    df._TOGGL_TODAY = [
        {"description": "o314", "duration": 600},   # 10 min
        {"description": "hcmr", "duration": 1200},  # 20 min
    ]
    headers = {"0n": {}, "1n": {"长o314": "V"}}
    r = _route_one(df, "长o314", headers=headers)
    assert r.step == "1n" and r.write_value == 15  # .5/m * 30 total


def test_long_o314_below_threshold_still_skips_with_hcmr_only(df):
    """skip_todoist=False so the threshold is enforced right here at route
    time (matches route_items' own `explicit_time or not skip_todoist` gate
    -- with skip_todoist=True and no typed value, enforcement is deliberately
    deferred to apply_timer_minutes instead, per its own comment)."""
    df._TOGGL_TODAY = [{"description": "hcmr", "duration": 600}]  # 10 min < 30
    headers = {"0n": {}, "1n": {"长o314": "V"}}
    items = df.parse_input("长o314")
    res = df.route_items(items, headers, TQ, skip_todoist=False)
    assert len(res) == 1 and res[0].step == "skipped"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
