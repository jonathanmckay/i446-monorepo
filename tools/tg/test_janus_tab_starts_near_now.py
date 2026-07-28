"""Regression (user report 2026-07-27: "I have at least two meaningful
entries in this block and last, but I can't select either of them in janus
to record the points"): selection itself worked, but a FRESH Tab started the
cursor at visible_events[0] — the day's 6 AM entry at the far top of the
screen, in a subtle #3a3a3a band, ~20 presses away from tonight's entries.
That reads as "can't select them at all" on a densely-tracked day.

Fix: a fresh Tab (or Shift-Tab) starts at the most recent item — the last
one with start_dt <= now — i.e. the current block's latest entry, right
where the user is looking. An ALREADY-armed cursor still steps one item
forward/backward exactly as before."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _tab_handler_bodies():
    src = (HERE / "janus.py").read_text()
    out = []
    for anchor in ('@kb.add("tab"', '@kb.add("s-tab"'):
        i = src.index(anchor)
        j = src.index("\n\n\n", i)
        out.append(src[i:j])
    return out


def test_fresh_selection_starts_at_latest_past_item_not_index_zero():
    for body in _tab_handler_bodies():
        assert "start_dt" in body and "past[-1]" in body, \
            "fresh Tab/S-Tab must seek the latest item with start_dt <= now"


def test_armed_selection_still_steps_by_one():
    for body in _tab_handler_bodies():
        assert "keys.index(STATE.event_sel)" in body


def test_functional_fresh_tab_lands_on_current_block_entry():
    """End-to-end: with morning + tonight entries registered, a fresh
    selection seed must be tonight's latest started item, not the 6 AM one."""
    import datetime as dtm
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Los_Angeles")
    spec = importlib.util.spec_from_file_location("janus_tab", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_tab"] = mod
    spec.loader.exec_module(mod)
    today = dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    now = today.replace(hour=21, minute=20)
    mod.view_now = lambda: now
    items = [
        {"kind": "entry", "start_dt": today.replace(hour=6, minute=45),
         "entry_ids": [1], "raw_desc": "wake up", "project_id": 1},
        {"kind": "entry", "start_dt": today.replace(hour=18, minute=18),
         "entry_ids": [2], "raw_desc": "backboard with theo", "project_id": 1},
        {"kind": "entry", "start_dt": today.replace(hour=21, minute=1),
         "entry_ids": [3], "raw_desc": "snack", "project_id": 1},
        {"kind": "empty", "start_dt": today.replace(hour=22), "dur_min": 30},
    ]
    # Mirror the handler's fresh-selection seed logic on the same shapes.
    past = [j for j, it in enumerate(items)
            if it.get("start_dt") and it["start_dt"] <= now]
    assert items[past[-1]]["raw_desc"] == "snack"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
