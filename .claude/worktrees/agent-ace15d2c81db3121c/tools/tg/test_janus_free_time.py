"""User request 2026-07-27: "at a glance it's hard to tell how much time I
have free" → two features:

1. Free-gap rows: meeting-free stretches >= FREE_MIN in future windows render
   as first-class green "free → HH:MM (Nm)" rows (_future_free_gaps), the
   mirror of the red "empty" past-gap rows. Applied to evening blocks, the
   focus band's next block, and the current block's remaining minutes.

2. Busy-bar gutter: a 1-char cell between the time column and row body on
   every card row — ▍ when an opaque calendar event covers the slot, blank
   when free — the day's meeting load as a scannable barcode."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_free", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_free"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup(mod):
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.current = None


def _gcal(title, start, end, transparency="opaque", all_day=False):
    return {"start_dt": start, "end_dt": end, "title": title, "calendar": "Outlook",
            "all_day": all_day, "transparency": transparency}


# ─── _future_free_gaps ──────────────────────────────────────────────────────

def test_free_gaps_complement_of_events():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    now = today.replace(hour=9)
    mod.STATE.events = [_gcal("standup", today.replace(hour=10, minute=30),
                              today.replace(hour=11, minute=0))]
    gaps = mod._future_free_gaps(10, 11, now)  # 午 = 10:00-12:00
    assert [(g["start_dt"].strftime("%H:%M"), g["dur_min"]) for g in gaps] == \
        [("10:00", 30), ("11:00", 60)]
    assert all(g["is_free"] for g in gaps)


def test_free_gaps_current_block_starts_at_now():
    mod = _load_tui()
    _setup(mod)
    now = _midnight().replace(hour=10, minute=40)
    gaps = mod._future_free_gaps(10, 11, now)
    assert len(gaps) == 1
    assert gaps[0]["start_dt"] == now and gaps[0]["dur_min"] == 80


def test_free_gaps_elapsed_block_yields_nothing():
    mod = _load_tui()
    _setup(mod)
    now = _midnight().replace(hour=14)
    assert mod._future_free_gaps(10, 11, now) == []


def test_free_gaps_ignore_transparent_and_all_day():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    now = today.replace(hour=9)
    mod.STATE.events = [
        _gcal("OOF", today, today + dtm.timedelta(days=1), all_day=True),
        _gcal("fyi hold", today.replace(hour=10), today.replace(hour=12),
              transparency="transparent"),
    ]
    gaps = mod._future_free_gaps(10, 11, now)
    assert len(gaps) == 1 and gaps[0]["dur_min"] == 120


def test_free_gaps_under_free_min_dropped():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    now = today.replace(hour=9)
    mod.STATE.events = [_gcal("m", today.replace(hour=10, minute=10),
                              today.replace(hour=11, minute=55))]
    gaps = mod._future_free_gaps(10, 11, now)
    assert gaps == [], f"10m head + 5m tail are both under FREE_MIN: {gaps!r}"


# ─── rendering ──────────────────────────────────────────────────────────────

def test_evening_block_shows_free_row_between_meetings():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.view_now = lambda: today.replace(hour=13)
    mod.STATE.events = [
        _gcal("ES:JM 1:1", today.replace(hour=16, minute=30), today.replace(hour=17)),
    ]
    text = "".join(t for _, t, *_ in mod.render_evening())
    assert "free →" in text
    assert "酉:00" in text  # block header still the meeting-bearing card


def test_fully_free_future_block_keeps_bare_header_free_in_body():
    """A free row must never ride the future header — the header slot belongs
    to the block's dominant meeting; with no meetings the header stays bare
    and the free stretch is a body row."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    picks = mod._future_free_gaps(18, 19, today.replace(hour=13))
    frags = mod._compact_block_lines("戌", 18, picks, 0, "", is_future=True)
    text = "".join(t for _, t, *_ in frags)
    header = text.split("\n")[0]
    assert "free" not in header
    assert "free → 20:00 (120m)" in text  # durations are minutes-denominated
    styles = [s for s, t, *_ in frags if "free →" in t]
    assert styles == ["class:free"], f"free rows must use class:free: {styles!r}"


def test_current_block_shows_remaining_free_time():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.view_now = lambda: today.replace(hour=8, minute=30)
    text = "".join(t for _, t, *_ in mod.render_focus_compact())
    top = text.split("午:00")[0]
    assert "free → 10:00 (90m)" in top


def test_gutter_busy_slot_marks_and_free_slot_blank():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.events = [_gcal("standup", today.replace(hour=10, minute=30),
                              today.replace(hour=11))]
    mod.view_now = lambda: today.replace(hour=9)
    sty, ch = mod._gutter(10, 30, 30)
    assert ch == "▍" and sty == "class:gutter_busy"
    sty2, ch2 = mod._gutter(11, 30, 30)
    assert ch2 == " "


def test_gutter_ignores_all_day_events():
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.events = [_gcal("OOF", today, today + dtm.timedelta(days=1),
                              all_day=True)]
    mod.view_now = lambda: today.replace(hour=9)
    _, ch = mod._gutter(10, 0, 30)
    assert ch == " "


def test_gutter_cell_rendered_in_rows_same_width_as_before():
    """The gutter replaces the single space between time column and body, so
    a row's total width must not change (no layout drift)."""
    mod = _load_tui()
    _setup(mod)
    today = _midnight()
    mod.STATE.events = [_gcal("m", today.replace(hour=6, minute=30),
                              today.replace(hour=7))]
    mod.view_now = lambda: today.replace(hour=5)
    frags = mod._compact_block_lines("辰", 6, [], 0, "")
    lines = "".join(t for _, t, *_ in frags).split("\n")
    assert any("▍" in l for l in lines), "busy slot must draw its gutter cell"
    busy_line = next(l for l in lines if "▍" in l)
    free_line = next(l for l in lines if "·" in l and "▍" not in l)
    assert busy_line.index("▍") == free_line.index("·") - 1, \
        "gutter must occupy exactly the old separator-space column"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
