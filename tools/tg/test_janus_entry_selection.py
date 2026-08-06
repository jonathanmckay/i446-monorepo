"""Selection generalized beyond calendar events to real Toggl entries and
untracked gaps (user request 2026-07-17: "can we make it so that I can select
toggl time entries as well?" / "I want to be able to select empty components
and fill th[em] with a time entry").

STATE.visible_events / STATE.event_sel are now a discriminated union of THREE
kinds, all cycled by the same Tab/Shift-Tab/Down/Up ring and acted on by the
same Enter key:
  - a raw gcal event dict (no "kind" key — exactly the original shape, so
    every pre-existing event-cursor test/call site keeps working untouched)
  - {"kind": "entry", start_dt, entry_ids, raw_desc, project_id} — a real
    tracked Toggl entry (or a merged span of several same-desc ones). Enter
    loads its description (+ @code) into the input line and arms
    STATE.edit_target; the NEXT Enter applies the (possibly retyped) text as
    an update to every entry_id in the span, rather than falling through to
    the normal create/start path.
  - {"kind": "empty", start_dt, dur_min} — an untracked gap. Enter prefills a
    ready-made "HHMM-HHMM " range and lets the ORDINARY typed-command path
    (already handles "<desc> <start>-<end> @<project>") create it — no new
    backend at all.
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_entrysel", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_entrysel"] = mod
    spec.loader.exec_module(mod)
    return mod


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


def _entry(desc, start, end, project_id=None, running=False, id=1):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": running, "id": id}


def _setup_common(mod):
    mod.STATE.current_known = True
    mod.STATE.current = None
    mod.STATE.entries_known = True
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.events = []
    mod.STATE.visible_events = []
    mod.STATE.event_sel = None
    mod.STATE.edit_target = None


# ─── _sel_key / prefill / parse helpers ─────────────────────────────────────

def test_sel_key_for_raw_event_matches_event_key():
    mod = _load_tui()
    today = _midnight()
    ev = {"start_dt": today.replace(hour=9), "title": "standup"}
    assert mod._sel_key(ev) == mod._event_key(ev)


def test_sel_key_kinds_never_collide():
    mod = _load_tui()
    today = _midnight()
    ev = {"start_dt": today.replace(hour=9), "title": "standup"}
    entry = {"kind": "entry", "start_dt": today.replace(hour=9), "entry_ids": [1]}
    empty = {"kind": "empty", "start_dt": today.replace(hour=9)}
    keys = {mod._sel_key(ev), mod._sel_key(entry), mod._sel_key(empty)}
    assert len(keys) == 3


def test_entry_edit_prefill_includes_project_code():
    mod = _load_tui()
    item = {"raw_desc": "carolina 1|1", "project_id": mod.PROJECT_MAP["i9"]}
    assert mod._entry_edit_prefill(item) == "carolina 1|1 @i9"


def test_entry_edit_prefill_omits_bare_at_when_no_project():
    mod = _load_tui()
    item = {"raw_desc": "unsorted thing", "project_id": None}
    prefill = mod._entry_edit_prefill(item)
    assert prefill == "unsorted thing"
    assert "@" not in prefill


def test_empty_gap_prefill_is_time_range_with_trailing_space():
    mod = _load_tui()
    today = _midnight()
    item = {"start_dt": today.replace(hour=10), "dur_min": 60}
    assert mod._empty_gap_prefill(item) == "1000-1100 "


def test_parse_edit_text_splits_trailing_code():
    mod = _load_tui()
    assert mod._parse_edit_text("carolina 1:1 sync @i9") == ("carolina 1:1 sync", "i9", None, [])


def test_parse_edit_text_no_code():
    mod = _load_tui()
    assert mod._parse_edit_text("just a rename") == ("just a rename", None, None, [])


# ─── Registration: real entries + gaps become selectable ───────────────────

def test_render_morning_registers_real_entry_as_selectable():
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    mod.STATE.entries = [_entry("carolina 1|1", today.replace(hour=8),
                                today.replace(hour=8, minute=30), id=101)]
    mod.view_now = lambda: today.replace(hour=12, minute=5)  # 未 current -> 巳 is a past block
    mod.render_morning()
    entries = [it for it in mod.STATE.visible_events if it.get("kind") == "entry"]
    assert any(it["entry_ids"] == [101] and it["raw_desc"] == "carolina 1|1" for it in entries)


def test_merged_contiguous_same_desc_entries_carry_all_ids():
    """Two real Toggl entries that happen to share a description and be
    contiguous render as ONE row (existing merge behavior) — an edit on that
    row must apply to BOTH underlying entries, not an arbitrary first/last."""
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    mod.STATE.entries = [
        _entry("0l", today.replace(hour=8), today.replace(hour=8, minute=15), id=201),
        _entry("0l", today.replace(hour=8, minute=15), today.replace(hour=8, minute=30), id=202),
    ]
    mod.view_now = lambda: today.replace(hour=12, minute=5)
    mod.render_morning()
    entries = [it for it in mod.STATE.visible_events if it.get("kind") == "entry"]
    matches = [it for it in entries if set(it["entry_ids"]) == {201, 202}]
    assert matches, f"expected one merged row carrying both ids, got {entries!r}"


def test_render_morning_registers_gap_as_selectable_empty():
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    mod.STATE.entries = [_entry("kids out the door", today.replace(hour=8),
                                today.replace(hour=8, minute=15), id=301)]
    mod.view_now = lambda: today.replace(hour=12, minute=5)
    mod.render_morning()
    empties = [it for it in mod.STATE.visible_events if it.get("kind") == "empty"]
    assert empties, "an untracked >= GAP_MIN stretch must register as a selectable empty item"


def test_sleep_synthetic_pick_is_not_selectable():
    """_block_sleep_item has no real Toggl id behind it — must never become
    an "entry" selection target (there's nothing to update_entry on)."""
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    mod.STATE.entries = [_entry("睡觉", today.replace(hour=0),
                                today.replace(hour=6, minute=30), id=401)]
    mod.view_now = lambda: today.replace(hour=12, minute=5)
    mod.render_morning()
    entries = [it for it in mod.STATE.visible_events if it.get("kind") == "entry"]
    assert not any(it.get("raw_desc") == "睡觉" and 401 not in it.get("entry_ids", [])
                   for it in entries)


def test_running_entry_is_selectable():
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    start = today.replace(hour=9, minute=2)
    now = today.replace(hour=9, minute=10)
    mod.STATE.entries = [_entry("0t", start, now, running=True, id=501)]
    mod.STATE.current = {"start": start.isoformat(), "description": "0t", "project_id": None}
    mod.view_now = lambda: now
    mod.render_focus_compact()
    entries = [it for it in mod.STATE.visible_events if it.get("kind") == "entry"]
    assert any(it["entry_ids"] == [501] for it in entries), \
        "the live running row must be selectable for edit too, not just completed entries"


# ─── Highlight styling ───────────────────────────────────────────────────────

def test_selected_entry_row_gets_full_row_background_band():
    mod = _load_tui()
    today = _midnight()
    # A decoy at the block's own :00 (2026-08-06: the chronologically-first
    # real entry now rides the header — see test_janus_compact_blocks.py's
    # widened header-promotion rule) keeps "carolina" itself as an ordinary
    # BODY row, which is what this test is actually about: an isolated,
    # separately-styled time fragment on a body row, not the header's fused
    # `blk_name + time` fragment.
    decoy = {"start_dt": today.replace(hour=8), "time_str": "08:00", "label": "standup",
             "style": "", "dur_min": 10, "entry_ids": [0], "raw_desc": "standup",
             "project_id": None}
    pick = {"start_dt": today.replace(hour=9), "time_str": "09:00", "label": "carolina 1|1 · i9",
            "style": "fg:#2979ff", "dur_min": 15, "entry_ids": [1], "raw_desc": "carolina 1|1",
            "project_id": None}
    mod.STATE.event_sel = mod._sel_key({"kind": "entry", "start_dt": pick["start_dt"], "entry_ids": [1]})
    frags = mod._compact_block_lines("巳", 8, [decoy, pick], 0, "", max_rows=8, track_selection=True)
    assert any(s == "class:selected_accent" and t.strip() == "09:00" for s, t in frags)
    assert any("bg:#3a3a3a" in s and "carolina" in t for s, t in frags)
    assert any(s == "class:selected_bg" for s, t in frags)
    assert not any("reverse" in s for s, t in frags)


def test_selection_of_entry_row_never_shifts_horizontal_position():
    mod = _load_tui()
    today = _midnight()
    pick = {"start_dt": today.replace(hour=9), "time_str": "09:00", "label": "carolina 1|1 · i9",
            "style": "fg:#2979ff", "dur_min": 15, "entry_ids": [1], "raw_desc": "carolina 1|1",
            "project_id": None}
    mod.STATE.event_sel = None
    unselected = "".join(t for _, t in mod._compact_block_lines("巳", 8, [pick], 0, "",
                                                                  max_rows=8, track_selection=True))
    mod.STATE.event_sel = mod._sel_key({"kind": "entry", "start_dt": pick["start_dt"], "entry_ids": [1]})
    selected = "".join(t for _, t in mod._compact_block_lines("巳", 8, [pick], 0, "",
                                                               max_rows=8, track_selection=True))
    assert selected == unselected, "highlighting must not change any character or width, only color"


def test_selected_gap_row_gets_highlight():
    mod = _load_tui()
    today = _midnight()
    gap = {"start_dt": today.replace(hour=9), "time_str": "09:00", "label": "",
          "style": "", "dur_min": 45, "is_gap": True}
    mod.STATE.event_sel = mod._sel_key({"kind": "empty", "start_dt": gap["start_dt"]})
    frags = mod._compact_block_lines("巳", 8, [gap], 0, "", max_rows=8, track_selection=True)
    assert any(s == "class:selected_bg" for s, t in frags)
    assert any(s == "class:selected_accent" for s, t in frags)


# ─── Enter handler: arm-edit / prefill-empty / apply-edit ───────────────────

def test_enter_on_selected_entry_arms_edit_and_prefills_input():
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    item = {"kind": "entry", "start_dt": today.replace(hour=9), "entry_ids": [7],
            "raw_desc": "carolina 1|1", "project_id": mod.PROJECT_MAP["i9"]}
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    mod.input_buffer.text = ""
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.event_sel is None
    assert mod.STATE.edit_target == {"ids": [7], "date": today.date()}
    assert mod.input_buffer.text == "carolina 1|1 @i9"


def test_enter_on_selected_empty_prefills_without_arming_edit():
    mod = _load_tui()
    today = _midnight()
    _setup_common(mod)
    item = {"kind": "empty", "start_dt": today.replace(hour=14), "dur_min": 30}
    mod.STATE.visible_events = [item]
    mod.STATE.event_sel = mod._sel_key(item)
    mod.input_buffer.text = ""
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.event_sel is None
    assert mod.STATE.edit_target is None, "filling an empty slot must NOT arm an edit target"
    assert mod.input_buffer.text == "1400-1430 "


def test_enter_with_armed_edit_and_empty_text_cancels():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = ""
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.edit_target is None
    assert "cancelled" in mod.STATE.flash


def test_enter_with_armed_edit_and_unknown_code_flashes_and_drops_target():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = "renamed thing @notarealcode"
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.edit_target is None, \
        "the target is consumed unconditionally at the top -- an error must not resurrect it"
    assert "unknown project code" in mod.STATE.flash


def test_enter_with_armed_edit_and_valid_text_flashes_edit_command():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7, 8], "date": today.date()}
    mod.input_buffer.text = "carolina 1:1 sync @i9"
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.edit_target is None
    assert mod.STATE.flash.startswith("$ edit carolina 1:1 sync @i9")


# ─── Time-range retype retimes, doesn't pollute the description ────────────
# (user request 2026-07-18: "if I edit an event with a new time seris [sic]
# (i.e. hhmm-hhmm) it updates the time not the description")

def test_parse_edit_text_extracts_bare_time_range_leaves_desc_none():
    mod = _load_tui()
    desc, code, time_range, _tags = mod._parse_edit_text("0930-1000")
    assert desc is None, "a bare time range must not become the new description"
    assert code is None
    assert time_range == ("0930", "1000")


def test_parse_edit_text_extracts_time_range_alongside_desc_and_code():
    mod = _load_tui()
    desc, code, time_range, _tags = mod._parse_edit_text("carolina sync 0930-1000 @i9")
    assert desc == "carolina sync"
    assert code == "i9"
    assert time_range == ("0930", "1000")


def test_enter_with_armed_edit_bare_time_range_updates_time_only():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = "0930-1000"
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.edit_target is None
    assert "0930-1000" in mod.STATE.flash
    assert "(desc unchanged)" in mod.STATE.flash


def test_enter_with_armed_edit_time_range_on_merged_row_is_refused():
    """A merged multi-entry row has no single well-defined new time to
    retime ALL of them to -- must refuse, not silently corrupt one of them."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7, 8], "date": today.date()}
    mod.input_buffer.text = "0930-1000"
    _binding(mod, "enter").handler(_FakeEvent())
    assert mod.STATE.edit_target is None
    assert "merged" in mod.STATE.flash


# ─── Cancellation on day-nav / escape ───────────────────────────────────────

def test_escape_clears_armed_edit_and_resets_input():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = "carolina 1|1 @i9"
    _binding(mod, "escape").handler(_FakeEvent())
    assert mod.STATE.edit_target is None
    assert mod.input_buffer.text == ""
    assert "cancelled" in mod.STATE.flash


def test_escape_clears_armed_selection_even_without_edit_target():
    mod = _load_tui()
    mod.STATE.edit_target = None
    mod.STATE.event_sel = ("event", "key")
    _binding(mod, "escape").handler(_FakeEvent())
    assert mod.STATE.event_sel is None


def test_day_back_clears_armed_edit_and_resets_input():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = "carolina 1|1 @i9"
    mod.STATE.day_offset = 0
    _binding(mod, "c-left").handler(_FakeEvent())
    assert mod.STATE.edit_target is None
    assert mod.input_buffer.text == ""


# ─── MECE: a retimed entry must trim/split whatever it now overlaps ────────
# (user request 2026-07-19: "if I make an edit to time entries, I want to
# make sure the time entries for that period are MECE... shorten [an
# overlapping entry] to make room, or delete the old one if full overlap")

class _CapturingApp:
    """Unlike _FakeApp, actually RUNS the background coroutine (via
    asyncio.run) instead of discarding it — needed to verify what happens
    INSIDE _apply_edit_and_refresh, not just the synchronous setup before it."""

    def create_background_task(self, coro):
        import asyncio
        asyncio.run(coro)

    def invalidate(self):
        pass


class _CapturingEvent:
    def __init__(self):
        self.app = _CapturingApp()


def test_apply_edit_with_time_range_trims_before_updating(monkeypatch):
    mod = _load_tui()
    today = _midnight()
    calls = []

    class _FakeToggl:
        def trim_range(self, start_dt, end_dt, exclude_ids=None):
            calls.append(("trim", start_dt, end_dt, exclude_ids))
            return ["Trimmed: something"]

        def update_entry(self, entry_id, **fields):
            calls.append(("update", entry_id, fields))

    monkeypatch.setattr(mod, "toggl_api", _FakeToggl())
    monkeypatch.setattr(mod, "fetch_current", lambda *a, **k: None)
    monkeypatch.setattr(mod, "fetch_today", lambda *a, **k: None)

    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = "0930-1000"
    _binding(mod, "enter").handler(_CapturingEvent())

    assert calls[0][0] == "trim"
    _, start_dt, end_dt, exclude_ids = calls[0]
    assert start_dt == today.replace(hour=9, minute=30)
    assert end_dt == today.replace(hour=10)
    assert exclude_ids == {7}, "the entry being retimed must not trim itself"
    assert calls[1] == ("update", 7, {"start": start_dt.isoformat(), "stop": end_dt.isoformat(),
                                       "duration": 1800})


def test_apply_edit_without_time_range_never_calls_trim(monkeypatch):
    mod = _load_tui()
    today = _midnight()
    calls = []

    class _FakeToggl:
        def trim_range(self, *a, **k):
            calls.append("trim")
            return []

        def update_entry(self, entry_id, **fields):
            calls.append(("update", entry_id, fields))

    monkeypatch.setattr(mod, "toggl_api", _FakeToggl())
    monkeypatch.setattr(mod, "fetch_current", lambda *a, **k: None)
    monkeypatch.setattr(mod, "fetch_today", lambda *a, **k: None)

    mod.STATE.edit_target = {"ids": [7], "date": today.date()}
    mod.input_buffer.text = "just a rename @i9"
    _binding(mod, "enter").handler(_CapturingEvent())

    assert "trim" not in calls, "a plain desc/project edit must not touch other entries at all"
    assert calls == [("update", 7, {"description": "just a rename", "project_id": mod.PROJECT_MAP["i9"]})]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
