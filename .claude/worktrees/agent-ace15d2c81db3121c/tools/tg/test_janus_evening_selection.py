"""Regression (user report 2026-07-30: "I can't select anything after 申").

render_all's event cursor spans the whole day via the reset-once + EXTEND
contract, but render_evening — the blocks after the focus band (酉/戌/亥) —
never passed track_selection=True, so its meetings were the one stretch of
the day Tab could not reach, putting them out of ⌥↵ convert and ^X delete's
reach too."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_evesel", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_evesel"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup(mod):
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.current = None
    mod.STATE.visible_events = []


def test_evening_block_events_are_selectable():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.view_now = lambda: today.replace(hour=13)
    ev = {"start_dt": today.replace(hour=19), "end_dt": today.replace(hour=19, minute=30),
          "title": "dinner sync", "calendar": "Outlook", "all_day": False,
          "transparency": "opaque"}
    mod.STATE.events = [ev]
    mod.render_evening()
    keys = [mod._sel_key(it) for it in mod.STATE.visible_events]
    assert mod._event_key(ev) in keys, \
        "a 戌 meeting must register in the event cursor's selectable set"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
