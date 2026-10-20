"""Regression (2026-07-21): a running entry's end_dt freezes at the last
fetch_today, so _block_gaps swept the minutes since that fetch as untracked —
the current block flashed "empty → HH:MM" while a timer was live (user
report: team offsite running since 11:00, "1402-1424 is flashing as empty").
While STATE.current confirms a timer is running, a running entry must cover
through the sweep cutoff; with current None (confirmed idle) the stale end
stands and the gap flash is legitimate."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_gap", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_gap"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(mod, sh, sm, eh, em, running=False):
    day = dt.datetime(2026, 7, 21, tzinfo=mod.TZ)
    return {"start_dt": day.replace(hour=sh, minute=sm),
            "end_dt": day.replace(hour=eh, minute=em),
            "desc": "team offsite", "project_id": None,
            "running": running, "id": 1}


def test_running_entry_covers_through_cutoff_no_false_gap():
    mod = _load_tui()
    mod.STATE.entries_known = True
    # Running since 11:00; end_dt frozen at a 14:02 fetch; it is now 14:24.
    mod.STATE.entries = [_entry(mod, 11, 0, 14, 2, running=True)]
    mod.STATE.current = {"id": 1, "start": "2026-07-21T11:00:00-07:00"}
    cutoff = dt.datetime(2026, 7, 21, 14, 24, tzinfo=mod.TZ)
    gaps = mod._block_gaps(14, 15, cutoff)   # 未/申-style 2h window
    assert gaps == [], "no gap may flash while a timer is confirmed running"


def test_stale_end_still_flashes_when_confirmed_idle():
    mod = _load_tui()
    mod.STATE.entries_known = True
    mod.STATE.entries = [_entry(mod, 11, 0, 14, 2, running=True)]
    mod.STATE.current = None   # confirmed idle: timer was stopped after fetch
    cutoff = dt.datetime(2026, 7, 21, 14, 24, tzinfo=mod.TZ)
    gaps = mod._block_gaps(14, 15, cutoff)
    assert len(gaps) == 1 and gaps[0]["dur_min"] == 22


def test_completed_entries_unaffected():
    mod = _load_tui()
    mod.STATE.entries_known = True
    mod.STATE.entries = [_entry(mod, 14, 0, 14, 10, running=False)]
    mod.STATE.current = {"id": 2, "start": "2026-07-21T14:20:00-07:00"}
    cutoff = dt.datetime(2026, 7, 21, 14, 40, tzinfo=mod.TZ)
    gaps = mod._block_gaps(14, 15, cutoff)
    # 14:10 → 14:40 is a real 30m hole in ENTRIES (the new running timer
    # isn't in entries yet) — completed entries never stretch.
    assert len(gaps) == 1 and gaps[0]["dur_min"] == 30


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
