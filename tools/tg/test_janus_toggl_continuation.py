"""Regression: a >30m Toggl entry must draw ◇ │ continuation marks through the
half-hour slots it covers in the compact (morning / past-day) block view.

An entry renders one row at its start slot; before 2026-07-05 the slots it
flowed through drew as bare gridlines, so on a previous-day view a 3h entry
read as untracked time. gcal events and sleep spillover already got the ◇ │
continuation treatment — plain tracked entries must too."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_tcont", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_tcont"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": False, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def test_long_entry_covers_following_marks():
    """deep work 10:00→12:00 covers every half-hour mark of 午 (10-12)."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("deep work", today.replace(hour=10),
                                today.replace(hour=12))]
    cont = mod._block_toggl_cont(10, today.replace(hour=23))
    assert set(cont.keys()) == {(10, 0), (10, 30), (11, 0), (11, 30)}


def test_short_entry_covers_only_its_own_slot():
    """A 25m entry never reaches the next half-hour mark."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("standup", today.replace(hour=10),
                                today.replace(hour=10, minute=25))]
    cont = mod._block_toggl_cont(10, today.replace(hour=23))
    assert set(cont.keys()) == {(10, 0)}


def test_mid_slot_entry_covers_marks_it_flows_through():
    """10:15→11:45 covers :30, 11:00, 11:30 but not the 10:00 mark."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [_entry("deep work", today.replace(hour=10, minute=15),
                                today.replace(hour=11, minute=45))]
    cont = mod._block_toggl_cont(10, today.replace(hour=23))
    assert set(cont.keys()) == {(10, 30), (11, 0), (11, 30)}


def test_render_morning_draws_toggl_continuation():
    """Integration: on an elapsed block, a 2h entry's covered marks render the
    ◇ │ continuation instead of bare times."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        _entry("睡觉", today, today.replace(hour=6)),
        _entry("deep work", today.replace(hour=6), today.replace(hour=8), 42),
    ]
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod._read_block_emojis = lambda now=None: {}
    # Pin the detail band past 辰 so it renders as an elapsed compact block
    # regardless of the wall clock (same trick as the sleep-spillover test).
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))
    text = "".join(t for _, t in mod.render_morning())
    chen = text[text.index("辰:00"):]
    chen_block = chen[:chen.index("巳:00")] if "巳:00" in chen else chen
    assert "deep work" in chen_block
    assert "◇ │" in chen_block, f"expected toggl continuation, got:\n{chen_block}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
