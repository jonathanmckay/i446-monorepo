"""User request 2026-07-30: starting a timer for a meeting a few minutes
early left BOTH the running entry and the calendar event on screen
('▶ Huddle: XBOX Developer' at 11:56 over 'Huddle:  XBOX Developer (30)'
at 12:00 — note the calendar copy's double space). Dedup rule: an event
whose normalized title matches an entry that overlaps it (or a running
entry started ≤15m early) is already tracked and its row is suppressed."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_dedup", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_dedup"] = mod
    spec.loader.exec_module(mod)
    return mod


def _at(hh, mm):
    return dtm.datetime.now(TZ).replace(hour=hh, minute=mm, second=0, microsecond=0)


def _event(title, sh, sm, eh, em):
    return {"title": title, "start_dt": _at(sh, sm), "end_dt": _at(eh, em)}


def _entry(desc, sh, sm, eh, em, running=False):
    return {"id": 1, "desc": desc, "project_id": 9, "running": running,
            "start_dt": _at(sh, sm), "end_dt": _at(eh, em), "tags": []}


def test_running_entry_started_early_tracks_event():
    mod = _load_tui()
    mod.STATE.entries_yday = []
    ev = _event("Huddle:  XBOX Developer", 12, 0, 12, 30)  # double space, as gcal had it
    entry = _entry("Huddle: XBOX Developer", 11, 56, 11, 56, running=True)
    assert mod._event_tracked(ev, [entry])


def test_overlapping_completed_entry_tracks_event():
    mod = _load_tui()
    mod.STATE.entries_yday = []
    ev = _event("Design Review", 12, 0, 12, 30)
    entry = _entry("design review", 12, 2, 12, 28)
    assert mod._event_tracked(ev, [entry])


def test_same_title_later_instance_not_tracked():
    """A recurring meeting later in the day must keep its row."""
    mod = _load_tui()
    mod.STATE.entries_yday = []
    ev = _event("Standup", 16, 0, 16, 30)
    morning = _entry("Standup", 9, 0, 9, 30)
    assert not mod._event_tracked(ev, [morning])


def test_different_title_overlap_not_tracked():
    """Time overlap alone must not suppress — that's _event_reclaimable's
    territory (hanger entries surfacing swallowed meetings)."""
    mod = _load_tui()
    mod.STATE.entries_yday = []
    ev = _event("Design Review", 12, 0, 12, 30)
    entry = _entry("vibing", 11, 0, 13, 0, running=True)
    assert not mod._event_tracked(ev, [entry])


def test_future_block_picks_hides_tracked_event():
    mod = _load_tui()
    ev = _event("Huddle:  XBOX Developer", 12, 0, 12, 30)
    other = _event("Lunch hold", 12, 30, 13, 0)
    mod.STATE.entries = [_entry("Huddle: XBOX Developer", 11, 56, 11, 56, running=True)]
    mod.STATE.entries_yday = []
    blk = mod.hour_to_block(12)[0]
    labels = [p["label"] for p in mod._future_block_picks(blk, [ev, other])]
    assert "Lunch hold" in labels
    assert not any("Huddle" in l for l in labels), \
        "a tracked meeting's calendar row must be suppressed"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
