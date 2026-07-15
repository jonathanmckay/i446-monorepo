"""Event cursor: dtd-style highlight scoped to the current block's gcal event
rows (user request 2026-07-15: "in the same way that I can highlight a task
in dtd, I want to be able to do this in Janus... turn a calendar event into a
time entry with one shortcut, usually in real time").

Tab/Shift-Tab cycle STATE.event_sel (a (start_dt, title) KEY, not an index —
an index would silently point at a different event once the list resizes);
Enter, on an empty input line with something armed, converts the selected
event into a running Toggl timer via tg-fast.py's own backdated-start
handling. No selection is armed by default, so a bare Enter never surprises."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_evcursor", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_evcursor"] = mod
    spec.loader.exec_module(mod)
    return mod


# prompt_toolkit normalizes some @kb.add() aliases to their canonical Keys
# enum value internally (e.g. "enter" -> Keys.ControlM = "c-m", "tab" ->
# Keys.ControlI = "c-i") — Keys is a str-mixin enum, so comparing against the
# canonical string still works, but the ALIAS string used at registration
# time does not.
_KEY_ALIASES = {"enter": "c-m", "tab": "c-i"}


def _binding(mod, key):
    canon = _KEY_ALIASES.get(key, key)
    hits = [b for b in mod.kb.bindings if b.keys == (canon,)]
    assert hits, f"no binding for {key!r}"
    return hits[0]


class _FakeApp:
    def create_background_task(self, coro):
        coro.close()  # never awaited in tests; close to avoid warnings

    def invalidate(self):
        pass


class _FakeEvent:
    app = _FakeApp()


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _gcal_event(title, start, end, calendar="Outlook"):
    return {"start_dt": start, "end_dt": end, "title": title, "calendar": calendar,
            "all_day": False, "transparency": "opaque"}


# ─── _event_key / _event_to_tg_command ──────────────────────────────────────

def test_event_key_is_start_and_title_not_identity():
    mod = _load_tui()
    today = _midnight()
    ev1 = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    ev2 = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    assert mod._event_key(ev1) == mod._event_key(ev2), \
        "two dicts describing the same real event must produce the same key"


def test_event_to_command_backdates_when_already_started():
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("Gen 10 Console Forecast Walkthrough",
                     today.replace(hour=10, minute=30), today.replace(hour=11), calendar="Outlook")
    now = today.replace(hour=10, minute=45)
    cmd = mod._event_to_tg_command(ev, now)
    assert cmd.startswith("1030 "), f"must backdate to the event's own start: {cmd!r}"
    assert "Gen 10 Console Forecast Walkthrough" in cmd


def test_event_to_command_plain_start_when_not_yet_started():
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("1:1 Jonathan & Scott",
                     today.replace(hour=11, minute=5), today.replace(hour=11, minute=30))
    now = today.replace(hour=10, minute=45)
    cmd = mod._event_to_tg_command(ev, now)
    assert not cmd[:4].isdigit() or cmd[4] != " ", "must NOT backdate an event that hasn't started"
    assert cmd.startswith("1:1 Jonathan & Scott")


def test_event_to_command_omits_bare_at_when_no_project_match():
    """gcal_project_code can return "" (no calendar/keyword match) — the
    command must not end up with a dangling bare '@'."""
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("totally unmapped event thing",
                     today.replace(hour=10, minute=45), today.replace(hour=11), calendar="some random calendar")
    now = today.replace(hour=10, minute=50)
    cmd = mod._event_to_tg_command(ev, now)
    assert not cmd.rstrip().endswith("@"), f"must not leave a dangling @: {cmd!r}"
    assert "@" not in cmd


# ─── STATE.visible_events / _compact_block_lines(track_selection=...) ──────

def test_visible_events_only_populated_when_tracking():
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    pick = {"start_dt": ev["start_dt"], "time_str": "09:00", "label": "standup",
            "style": "", "dur_min": 15, "is_event": True, "event": ev}
    mod.STATE.visible_events = ["stale"]
    mod._compact_block_lines("巳", 8, [pick], 0, "", max_rows=8, track_selection=False)
    assert mod.STATE.visible_events == ["stale"], \
        "a non-tracking call (every other block) must not touch visible_events"

    # Tracking EXTENDS rather than replaces — render_focus_compact calls this
    # for both the current AND next block and resets the list itself once up
    # front, so a bare assignment here would let the second call silently
    # wipe out the first block's events.
    mod.STATE.visible_events = []
    mod._compact_block_lines("巳", 8, [pick], 0, "", max_rows=8, track_selection=True)
    assert mod.STATE.visible_events == [ev]


def test_head_event_is_selectable_and_highlighted():
    """Regression: a future block's dominant/only event rides the HEADER
    line (the `if is_future and picks:` branch), never the body `rows` — the
    original track_selection wiring only scanned `rows`, so a block with a
    single event (the common case) had NOTHING selectable at all."""
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("GamePass sync", today.replace(hour=10, minute=30), today.replace(hour=11))
    pick = {"start_dt": ev["start_dt"], "time_str": "10:30", "label": "GamePass sync",
            "style": "fg:#2979ff", "dur_min": 30, "is_event": True, "event": ev}
    mod.STATE.visible_events = []
    frags = mod._compact_block_lines("午", 10, [pick], 0, "", is_future=True,
                                     max_rows=8, track_selection=True)
    assert mod.STATE.visible_events == [ev], "head event must register for the cursor"
    assert not any("bg:#3a3a3a" in s or "selected" in s for s, t in frags), \
        "unselected head must not be highlighted"

    mod.STATE.visible_events = []
    mod.STATE.event_sel = mod._event_key(ev)
    frags = mod._compact_block_lines("午", 10, [pick], 0, "", is_future=True,
                                     max_rows=8, track_selection=True)
    assert any("GamePass sync" in t and ("bg:#3a3a3a" in s or "selected" in s)
              for s, t in frags), "selected head must carry the highlight"
    assert not any("reverse" in s for s, t in frags)


def test_render_focus_compact_tracks_next_block_events_too():
    """Regression (user report 2026-07-15: "still can't seem to select...
    future calendar entries"): render_focus_compact only tracked the CURRENT
    block's events for the cursor — the NEXT block's were never added to
    STATE.visible_events at all, so Tab could never reach them."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = []
    mod.STATE.entries_known = True
    mod.STATE.current = None
    mod.STATE.current_known = True
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.events = [_gcal_event("future block meeting",
                                    today.replace(hour=10, minute=30),
                                    today.replace(hour=11))]
    mod.view_now = lambda: today.replace(hour=8, minute=5)  # 巳(8-9)=current, 午(10-11)=next
    mod.render_focus_compact()
    titles = [e["title"] for e in mod.STATE.visible_events]
    assert "future block meeting" in titles


def test_visible_events_excludes_rows_trimmed_by_max_rows():
    """The event cursor's selectable set must be exactly what's ON SCREEN —
    an event that lost the entry_rows[:max_rows] cut must not be selectable
    (Tab landing on something invisible would be confusing)."""
    mod = _load_tui()
    today = _midnight()
    # 3 long entries fill all 3 rows at max_rows=3; the event pick (shorter)
    # gets crowded out of `rows` by _compact_block_lines' duration-sort cap...
    # actually entries and marks share the row budget by TIME, not duration,
    # for events/gaps mixed with real picks — so instead exercise the cap via
    # more events than max_rows allows.
    events = [
        _gcal_event(f"ev{i}", today.replace(hour=8, minute=i * 5),
                   today.replace(hour=8, minute=i * 5 + 4))
        for i in range(5)
    ]
    picks = [{"start_dt": e["start_dt"], "time_str": f"{e['start_dt']:%H:%M}",
             "label": e["title"], "style": "", "dur_min": 4, "is_event": True, "event": e}
            for e in events]
    mod._compact_block_lines("巳", 8, picks, 0, "", max_rows=3, track_selection=True)
    assert len(mod.STATE.visible_events) <= 3
    assert all(ev in events for ev in mod.STATE.visible_events)


def test_selected_event_row_gets_full_row_background_band():
    """dtd-style highlight (user report 2026-07-15): a flat background band
    across the WHOLE row, not ANSI reverse video — the time column, label,
    and duration must all carry the highlight background."""
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    pick = {"start_dt": ev["start_dt"], "time_str": "09:00", "label": "standup",
            "style": "fg:#2979ff", "dur_min": 15, "is_event": True, "event": ev}
    mod.STATE.event_sel = mod._event_key(ev)
    frags = mod._compact_block_lines("巳", 8, [pick], 0, "", max_rows=8, track_selection=True)
    # Time column gets the accent style; label carries the fg color + bg band;
    # the trailing " (15)\n" duration fragment carries the plain bg band.
    assert any(s == "class:selected_accent" and t.strip() == "09:00" for s, t in frags)
    assert any("bg:#3a3a3a" in s and "standup" in t for s, t in frags)
    assert any(s == "class:selected_bg" and t == " (15)\n" for s, t in frags)
    assert not any("reverse" in s for s, t in frags), "must not use ANSI reverse video"


def test_unselected_event_row_uses_plain_time_and_dim_styles():
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    pick = {"start_dt": ev["start_dt"], "time_str": "09:00", "label": "standup",
            "style": "fg:#2979ff", "dur_min": 15, "is_event": True, "event": ev}
    mod.STATE.event_sel = None
    frags = mod._compact_block_lines("巳", 8, [pick], 0, "", max_rows=8, track_selection=True)
    assert any(s == "class:time" for s, t in frags if t.strip() == "09:00")
    assert any(s == "class:dim" for s, t in frags if t == " (15)\n")
    assert not any("selected" in s or "reverse" in s for s, t in frags)


def test_selection_never_shifts_row_horizontal_position():
    """Regression (user report 2026-07-15: "keep the horizontal positioning
    that I had per block"): highlighting a row must be a pure color change —
    same characters, same widths, same right-justified duration column — so
    toggling event_sel never moves anything left/right."""
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    pick = {"start_dt": ev["start_dt"], "time_str": "09:00", "label": "standup",
            "style": "fg:#2979ff", "dur_min": 15, "is_event": True, "event": ev}
    mod.STATE.event_sel = None
    unselected = "".join(t for _, t in
                         mod._compact_block_lines("巳", 8, [pick], 0, "", max_rows=8, track_selection=True))
    mod.STATE.event_sel = mod._event_key(ev)
    selected = "".join(t for _, t in
                       mod._compact_block_lines("巳", 8, [pick], 0, "", max_rows=8, track_selection=True))
    assert selected == unselected, "highlighting must not change any character or width, only color"


# ─── Tab / Shift-Tab keybindings ────────────────────────────────────────────

def test_tab_and_shift_tab_are_bound_and_gated_on_empty_input():
    mod = _load_tui()
    for key in ("tab", "s-tab"):
        b = _binding(mod, key)
        mod.input_buffer.text = ""
        assert bool(b.filter()), f"{key!r} must fire on an empty command line"
        mod.input_buffer.text = "some typed thing"
        assert not bool(b.filter()), f"{key!r} must not intercept mid-input text"
    mod.input_buffer.text = ""


def test_tab_with_no_visible_events_is_a_noop():
    mod = _load_tui()
    mod.STATE.visible_events = []
    mod.STATE.event_sel = None
    _binding(mod, "tab").handler(_FakeEvent())
    assert mod.STATE.event_sel is None


def test_tab_arms_first_event_when_nothing_selected():
    mod = _load_tui()
    today = _midnight()
    ev1 = _gcal_event("a", today.replace(hour=9), today.replace(hour=9, minute=15))
    ev2 = _gcal_event("b", today.replace(hour=9, minute=30), today.replace(hour=9, minute=45))
    mod.STATE.visible_events = [ev1, ev2]
    mod.STATE.event_sel = None
    _binding(mod, "tab").handler(_FakeEvent())
    assert mod.STATE.event_sel == mod._event_key(ev1)


def test_tab_cycles_forward_and_wraps():
    mod = _load_tui()
    today = _midnight()
    evs = [_gcal_event(str(i), today.replace(hour=9, minute=i * 10),
                       today.replace(hour=9, minute=i * 10 + 5)) for i in range(3)]
    mod.STATE.visible_events = evs
    mod.STATE.event_sel = mod._event_key(evs[2])  # last one
    _binding(mod, "tab").handler(_FakeEvent())
    assert mod.STATE.event_sel == mod._event_key(evs[0]), "must wrap to the first"


def test_shift_tab_cycles_backward_and_wraps():
    mod = _load_tui()
    today = _midnight()
    evs = [_gcal_event(str(i), today.replace(hour=9, minute=i * 10),
                       today.replace(hour=9, minute=i * 10 + 5)) for i in range(3)]
    mod.STATE.visible_events = evs
    mod.STATE.event_sel = mod._event_key(evs[0])
    _binding(mod, "s-tab").handler(_FakeEvent())
    assert mod.STATE.event_sel == mod._event_key(evs[2]), "must wrap to the last"


def test_selection_surviving_list_change_falls_back_to_first_on_next_tab():
    """A stale key (list resized since it was armed) simply reads as 'not
    found' — Tab from there re-arms at index 0 rather than erroring or
    silently pointing at the wrong event."""
    mod = _load_tui()
    today = _midnight()
    evs = [_gcal_event(str(i), today.replace(hour=9, minute=i * 10),
                       today.replace(hour=9, minute=i * 10 + 5)) for i in range(2)]
    mod.STATE.event_sel = ("stale-key-not-in-list", "x")
    mod.STATE.visible_events = evs
    _binding(mod, "tab").handler(_FakeEvent())
    assert mod.STATE.event_sel == mod._event_key(evs[0])


# ─── Enter handler: convert-selected-event branch ──────────────────────────

def test_enter_with_no_selection_and_empty_input_is_still_a_noop():
    """Regression guard: no selection is armed by default, so this must
    remain exactly the old no-op behavior — never an unintended timer."""
    mod = _load_tui()
    mod.STATE.visible_events = []
    mod.STATE.event_sel = None
    mod.input_buffer.text = ""
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.event_sel is None


def test_enter_with_armed_selection_clears_it_and_flashes():
    mod = _load_tui()
    today = _midnight()
    ev = _gcal_event("standup", today.replace(hour=9), today.replace(hour=9, minute=15))
    mod.STATE.visible_events = [ev]
    mod.STATE.event_sel = mod._event_key(ev)
    mod.input_buffer.text = ""
    mod.view_now = lambda: today.replace(hour=9, minute=5)
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.event_sel is None, "selection must clear once converted"
    assert "standup" in mod.STATE.flash


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
