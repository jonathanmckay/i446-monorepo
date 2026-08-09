"""User request 2026-08-08: "can we make the current time entry row
selectable (the one above text input), so that if I hit enter, it means
stop, if I hit opt+enter it means run /done" — followed by "let's make it so
that swiping right on the current time entry is the same as opt enter (i.e.
runs /done) on the task".

The pinned bottom-bar mirror of the running timer (render_current_bottom,
just above the input box) was previously pure static text — not in
STATE.visible_events at all, no click handler. It's now a selectable
{"kind": "current"} row, deliberately independent of the main pane's own
selectable running-entry row ({"kind": "entry", "running": True}), which
keeps its existing edit-on-Enter behavior untouched:

  - plain Enter on the selected "current" row: stop the timer (same action
    as ^S), no points.
  - opt+enter (escape, enter) on the selected "current" row, OR a swipe-right
    gesture directly on the row (MOUSE_DOWN then MOUSE_UP >= SWIPE_RIGHT_COLS
    columns to the right): /done — stop the timer AND grant its points /
    close the matching Todoist task, via the same did-fast
    HHMM-nowHHMM-range trick _finalize_recording_cmd already uses for a
    still-running entry (did-fast's MECE trim stops it as a side effect).
"""
import asyncio
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_currentrow", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_currentrow"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeApp:
    def create_background_task(self, coro):
        # Mirrors test_janus_ctrl_x_toggl_delete.py's harness: the handler's
        # own synchronous work (STATE mutation, flash) runs for real; the
        # async body (the actual subprocess/network call) is never awaited.
        coro.close()

    def invalidate(self):
        pass


class _FakeEvent:
    app = _FakeApp()


class _FakePos:
    def __init__(self, x):
        self.x = x


class _FakeMouseEvent:
    def __init__(self, mod, event_type, x):
        self.event_type = event_type
        self.position = _FakePos(x)


def _binding(mod, keys):
    hits = [b for b in mod.kb.bindings if b.keys == keys]
    assert hits, f"no binding for {keys!r}"
    return hits[0]


def _freeze_now(mod, when):
    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    mod.dt.datetime = _DT


def _setup(mod, current=None, recording=None):
    mod.STATE.current = current
    mod.STATE.recording = recording
    mod.STATE.visible_events = []
    mod.STATE.event_sel = None
    mod.STATE.current_swipe_start = None
    mod.STATE.queued_cmds = set()
    mod.STATE.work_q = None


def _current_item(mod, desc="writing code"):
    item = {"kind": "current", "raw_desc": desc}
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    return item


# ─── selection plumbing ──────────────────────────────────────────────────

def test_render_all_appends_current_row_only_when_a_timer_is_running():
    mod = _load_tui()
    _setup(mod, current={"description": "x", "start": "2026-08-08T14:00:00+00:00",
                          "project_id": None})
    mod.render_all()
    kinds = [it.get("kind") for it in mod.STATE.visible_events]
    assert kinds.count("current") == 1

    _setup(mod, current=None)
    mod.render_all()
    assert "current" not in [it.get("kind") for it in mod.STATE.visible_events]


def test_current_row_sel_key_is_a_stable_singleton():
    mod = _load_tui()
    assert mod._sel_key({"kind": "current"}) == ("current",)


def test_bottom_bar_click_handler_and_selected_highlight():
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    _setup(mod, current={"description": "x", "start": "2026-08-08T14:00:00+00:00",
                          "project_id": None})
    frags = mod.render_current_bottom()
    assert all(len(f) == 3 and f[2] is not None for f in frags), (
        "both fragments must carry a click handler")

    mod.STATE.event_sel = mod._sel_key({"kind": "current"})
    sel_frags = mod.render_current_bottom()
    assert "bg:#3a3a3a" in sel_frags[0][0]


# ─── plain Enter = stop ──────────────────────────────────────────────────

def test_enter_on_current_row_stops_and_clears_selection():
    mod = _load_tui()
    _setup(mod, current={"description": "x", "start": "2026-08-08T14:00:00+00:00",
                          "project_id": None})
    _current_item(mod)
    mod.input_buffer.text = ""
    _binding(mod, ("c-m",)).handler(_FakeEvent())
    assert mod.STATE.event_sel is None
    assert mod.STATE.flash == "stopping…"


def test_enter_on_main_pane_running_entry_still_arms_edit_not_stop():
    """The main pane's OWN selectable running-entry row (kind == "entry",
    running == True) must keep its pre-existing edit-on-Enter behavior —
    this change must not have collapsed the two into one."""
    mod = _load_tui()
    today = dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    item = {"kind": "entry", "start_dt": today.replace(hour=9), "entry_ids": [1],
            "raw_desc": "writing code", "project_id": None, "dur_min": 30,
            "running": True}
    _setup(mod)
    mod.STATE.entries = [{"id": 1, "start_dt": today.replace(hour=9), "desc": "writing code",
                           "project_id": None, "running": True, "tags": []}]
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    mod.input_buffer.text = ""
    _binding(mod, ("c-m",)).handler(_FakeEvent())
    assert mod.STATE.edit_target is not None, "running entry row must still arm an edit"
    assert mod.input_buffer.text, "edit text must be prefilled"


# ─── opt+enter = /done ───────────────────────────────────────────────────

def test_alt_enter_on_current_row_builds_and_queues_did_command():
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    _setup(mod, current={"description": "writing code",
                          "start": "2026-08-08T14:00:00+00:00", "project_id": None})
    _current_item(mod, desc="writing code")
    _binding(mod, ("escape", "c-m")).handler(_FakeEvent())
    # start 14:00 UTC -> 07:00 Pacific (PDT); now 14:30 Pacific.
    expected_cmd = "writing code 0700-1430"
    assert expected_cmd in mod.STATE.queued_cmds
    assert expected_cmd in mod.STATE.flash
    assert mod.STATE.event_sel is None


def test_alt_enter_on_current_row_refuses_a_just_started_timer():
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 0, 30, tzinfo=TZ)
    _freeze_now(mod, now)
    # now is 14:00:30 Pacific (PDT, UTC-7) = 21:00:30 UTC; start 30s earlier.
    _setup(mod, current={"description": "writing code",
                          "start": "2026-08-08T21:00:00+00:00", "project_id": None})
    _current_item(mod, desc="writing code")
    _binding(mod, ("escape", "c-m")).handler(_FakeEvent())
    assert mod.STATE.queued_cmds == set(), "under a minute in — nothing should queue"
    assert "give it a minute" in mod.STATE.flash


def test_alt_enter_on_current_row_defers_to_recording_finalize_when_it_matches():
    """If a d357 recording is live for the SAME entry, /done must route
    through the existing finalize-notes-then-grant flow instead of a second,
    plainer did-fast call for the same minutes (double-grant risk)."""
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    _setup(mod, current={"description": "writing code",
                          "start": "2026-08-08T14:00:00+00:00", "project_id": None},
           recording={"desc": "writing code", "start_dt": now})
    _current_item(mod, desc="writing code")
    _binding(mod, ("escape", "c-m")).handler(_FakeEvent())
    assert "finalize:writing code" in mod.STATE.queued_cmds
    assert "writing code 0700-1430" not in mod.STATE.queued_cmds, (
        "must not ALSO queue the plain did-fast command for the same entry"
    )


# ─── swipe right = /done ─────────────────────────────────────────────────

def test_swipe_right_past_threshold_runs_done():
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    mod.get_app = lambda: _FakeApp()
    _setup(mod, current={"description": "writing code",
                          "start": "2026-08-08T14:00:00+00:00", "project_id": None})
    key = mod._sel_key({"kind": "current"})
    click = mod._current_row_click(key)
    click(_FakeMouseEvent(mod, mod.MouseEventType.MOUSE_DOWN, 5))
    click(_FakeMouseEvent(mod, mod.MouseEventType.MOUSE_UP, 5 + mod.SWIPE_RIGHT_COLS))
    assert "writing code 0700-1430" in mod.STATE.queued_cmds
    assert mod.STATE.current_swipe_start is None, "gesture state must reset after firing"


def test_short_drag_or_plain_click_just_selects_no_done():
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    mod.get_app = lambda: _FakeApp()
    _setup(mod, current={"description": "writing code",
                          "start": "2026-08-08T14:00:00+00:00", "project_id": None})
    key = mod._sel_key({"kind": "current"})
    click = mod._current_row_click(key)
    click(_FakeMouseEvent(mod, mod.MouseEventType.MOUSE_DOWN, 5))
    click(_FakeMouseEvent(mod, mod.MouseEventType.MOUSE_UP, 5 + mod.SWIPE_RIGHT_COLS - 1))
    assert mod.STATE.queued_cmds == set(), "short of the threshold must not fire /done"
    assert mod.STATE.event_sel == key, "falls back to ordinary click-to-select"


def test_swipe_state_does_not_leak_across_gestures():
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 8, 14, 30, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    mod.get_app = lambda: _FakeApp()
    _setup(mod, current={"description": "writing code",
                          "start": "2026-08-08T14:00:00+00:00", "project_id": None})
    key = mod._sel_key({"kind": "current"})
    click = mod._current_row_click(key)
    # A release with no matching prior MOUSE_DOWN (e.g. a drag that started
    # elsewhere and released here) must never fire /done off a stale start.
    click(_FakeMouseEvent(mod, mod.MouseEventType.MOUSE_UP, 100))
    assert mod.STATE.queued_cmds == set()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
