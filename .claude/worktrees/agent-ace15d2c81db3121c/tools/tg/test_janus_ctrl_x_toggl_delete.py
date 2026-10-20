"""User request 2026-08-06: "for janus I want ctrl + x to apply to toggl
entries as well." ^X already deleted a selected CALENDAR event (2026-07-30,
to match dtd's delete binding); this widens it to a selected TRACKED Toggl
entry too — deleted for real via the Toggl API, with the same running-timer
refusal ^P split already has on this item kind (deleting your live timer out
from under you isn't something Ctrl+X should get to do by accident), and
full multi-id deletion for a merged contiguous-entry row (no single-timeline
ambiguity for a delete, unlike split).
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_ctrlx", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_ctrlx"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeApp:
    def create_background_task(self, coro):
        # Mirrors test_janus_split_and_tag_display.py's harness: the handler's
        # own synchronous work (optimistic STATE mutation, refusal checks,
        # flash) runs for real; the async body (the actual toggl_api.delete_entry
        # network call) is never awaited, so no real API call happens here.
        coro.close()

    def invalidate(self):
        pass


class _FakeEvent:
    app = _FakeApp()


def _binding(mod, key):
    hits = [b for b in mod.kb.bindings if b.keys == (key,)]
    assert hits, f"no binding for {key!r}"
    return hits[0]


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _entry_item(today, dur=30, ids=(1,), running=False, desc="eat"):
    return {"kind": "entry", "start_dt": today.replace(hour=12),
            "entry_ids": list(ids), "raw_desc": desc, "project_id": None,
            "dur_min": dur, "running": running}


def _setup(mod, entries):
    mod.STATE.events = []
    mod.STATE.entries = entries
    mod.STATE.entries_yday = []
    mod.STATE.visible_events = []
    mod.STATE.event_sel = None


def test_ctrl_x_refuses_running_entry():
    mod = _load_tui()
    today = _midnight()
    entries = [{"id": 1, "start_dt": today.replace(hour=12), "desc": "eat",
                "project_id": None, "running": True}]
    _setup(mod, entries)
    item = _entry_item(today, running=True)
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    _binding(mod, "c-x").handler(_FakeEvent())
    assert mod.STATE.entries == entries, "a running entry must not be removed"
    assert mod.STATE.event_sel is not None, "selection must survive a refused delete"


def test_ctrl_x_optimistically_removes_entry_from_state():
    mod = _load_tui()
    today = _midnight()
    entries = [
        {"id": 1, "start_dt": today.replace(hour=12), "desc": "eat",
         "project_id": None, "running": False},
        {"id": 2, "start_dt": today.replace(hour=13), "desc": "other",
         "project_id": None, "running": False},
    ]
    _setup(mod, entries)
    item = _entry_item(today, ids=(1,))
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    _binding(mod, "c-x").handler(_FakeEvent())
    assert [e["id"] for e in mod.STATE.entries] == [2], (
        "the selected entry is removed immediately (optimistic), the other survives")
    assert mod.STATE.event_sel is None, "selection clears after delete"


def test_ctrl_x_removes_every_id_of_a_merged_entry():
    mod = _load_tui()
    today = _midnight()
    entries = [
        {"id": 1, "start_dt": today.replace(hour=12), "desc": "eat",
         "project_id": None, "running": False},
        {"id": 2, "start_dt": today.replace(hour=12, minute=15), "desc": "eat",
         "project_id": None, "running": False},
        {"id": 3, "start_dt": today.replace(hour=13), "desc": "unrelated",
         "project_id": None, "running": False},
    ]
    _setup(mod, entries)
    item = _entry_item(today, ids=(1, 2))  # a merged contiguous-entry row
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    _binding(mod, "c-x").handler(_FakeEvent())
    assert [e["id"] for e in mod.STATE.entries] == [3], (
        "every constituent id of a merged row is removed, not just the first")


def test_ctrl_x_still_requires_a_selection_of_the_right_kind():
    """Nothing selected, or a selected gap ('kind': 'empty') — neither an
    entry nor a calendar event — must be refused, not silently no-op into an
    entry-delete or a calendar-delete."""
    mod = _load_tui()
    today = _midnight()
    entries = [{"id": 1, "start_dt": today.replace(hour=12), "desc": "eat",
                "project_id": None, "running": False}]
    _setup(mod, entries)
    mod.STATE.event_sel = None
    _binding(mod, "c-x").handler(_FakeEvent())
    assert mod.STATE.entries == entries, "no selection -> nothing deleted"

    gap = {"kind": "empty", "start_dt": today.replace(hour=14), "dur_min": 30}
    mod.STATE.visible_events = [gap]
    mod.STATE.event_sel = mod._sel_key(gap)
    _binding(mod, "c-x").handler(_FakeEvent())
    assert mod.STATE.entries == entries, "a selected gap is not a deletable kind"


def test_ctrl_x_calendar_branch_still_present_and_unconditional_on_kind():
    """Structural: the pre-existing calendar-event deletion path (kind is
    None, per the raw-gcal-event convention) must still exist, gated the
    same way as before — this widened handler must not have replaced it."""
    src = (HERE / "janus.py").read_text()
    i = src.index('@kb.add("c-x")')
    j = src.index('@kb.add("c-q")', i)
    body = src[i:j]
    assert 'item.get("kind") == "entry"' in body, "the new toggl-entry branch"
    assert 'item.get("kind") is None and item.get("end_dt")' in body, (
        "the original calendar-event branch must still be there")
    assert "toggl_api.delete_entry" in body


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
