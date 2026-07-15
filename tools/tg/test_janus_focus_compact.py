"""The focus band (current + next block) used to render in a visually
distinct "detail band" style (dash-ruled headers, HH:MM-prefixed rows with no
duration shown, a 15-min gcal preview grid, a sub-second ticking timer bar).
User report 2026-07-15: "I like the way 午 is rendering... but not 巳... I
want the focus blocks to have 8 lines, but render in the same style I do for
the rest of the day."

render_focus_compact() replaces render_detail() in the render pipeline: both
the current and next block now render via the SAME _compact_block_lines()
card style used everywhere else (render_morning / render_evening), just with
FOCUS_ROWS=8 body rows instead of the usual 3. The running task keeps a
lightweight "▶" marker (whole-minute duration, not ticking); idle time since
the last entry falls out as the same flashing "empty → HH:MM (Nm)" gap row
every other block already uses."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_focus", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_focus"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(desc, start, end, project_id=None, running=False):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": running, "id": 1}


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup_common(mod):
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0


def test_render_all_uses_focus_compact_not_old_detail_band():
    """render_all's composition must call render_focus_compact, not the old
    render_detail — the dash-rule/HH:MM-prefixed detail band is dormant."""
    mod = _load_tui()
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def render_all()")
    body = src[i_def:src.index("\n\n\n", i_def)]
    assert "render_focus_compact()" in body
    assert "render_detail()" not in body


def test_current_block_renders_compact_card_style():
    """The current block's header must be the plain compact-card format
    ("巳:00 <emoji>  <pts>分"), not the old dash-ruled section_rule format
    ("─ 巳 ... ───── 78分")."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = [_entry("kids out the door", today.replace(hour=8),
                                today.replace(hour=8, minute=30), project_id=1)]
    mod.STATE.current = None
    mod.view_now = lambda: today.replace(hour=8, minute=40)
    text = "".join(t for _, t in mod.render_focus_compact())
    assert "巳:00" in text
    assert "─ 巳" not in text, "must not use the old dash-ruled header"


def test_current_block_has_eight_body_rows():
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = []
    mod.STATE.current = None
    mod.view_now = lambda: today.replace(hour=8, minute=5)
    frags = mod.render_focus_compact()
    text = "".join(t for _, t in frags)
    lines = text.split("\n")
    # First block: header + 8 body lines = 9 non-empty-split lines before 午's header.
    boundary = next(i for i, l in enumerate(lines) if l.startswith("午:00"))
    assert boundary == 9, f"expected header + 8 body rows before 午, got {boundary} lines:\n{text}"


def test_running_task_shows_live_marker_even_under_a_minute():
    """Regression: _past_block_picks' `mins < 1` filter (meant to drop stray
    sub-minute completed entries) was ALSO dropping the running task when it
    had just started — the ▶ row never appeared until a full minute had
    elapsed. The running entry must survive regardless of duration."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    start = today.replace(hour=9, minute=2, second=25)
    now = today.replace(hour=9, minute=3, second=6)  # 41s elapsed
    mod.STATE.entries = [_entry("0t", start, now, project_id=1, running=True)]
    mod.STATE.current = {"start": start.isoformat(), "description": "0t", "project_id": 1}
    mod.view_now = lambda: now
    frags = mod.render_focus_compact()
    text = "".join(t for _, t in frags)
    assert "▶" in text and "0t" in text
    assert any(s.startswith("bold") and "0t" in t for s, t in frags), \
        "running row must carry the bold ▶ style, not a plain entry row"


def test_idle_gap_flashes_bounded_by_now_not_future_block_end():
    """Regression: _block_gaps' trailing gap used the block's FIXED end time,
    which for the still-in-progress current block is in the FUTURE — an idle
    stretch must stop growing at `now`, not silently extend into
    not-yet-elapsed time."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = [_entry("kids out the door", today.replace(hour=8),
                                today.replace(hour=8, minute=50), project_id=1)]
    mod.STATE.current = None
    now = today.replace(hour=9, minute=5)  # 15 min idle since 8:50
    mod.view_now = lambda: now
    text = "".join(t for _, t in mod.render_focus_compact())
    assert "empty" in text and "09:05" in text and "(15m)" in text
    assert "09:15" not in text.split("午:00")[0], \
        "the idle gap must not extend past `now` into the block's unelapsed future"


def test_next_block_uses_future_compact_picks_not_shared_preview_grid():
    """The next (not-yet-started) block renders via _future_block_picks, same
    as any other future block — not the old continuous 15-min gcal grid that
    straddled both blocks with no per-block distinction."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = []
    mod.STATE.current = None
    mod.STATE.events = [{
        "start_dt": today.replace(hour=10, minute=30),
        "end_dt": today.replace(hour=11, minute=0),
        "title": "m5x2 Strat (1|1|1)", "calendar": "m5x2 Cal",
        "all_day": False, "transparency": "opaque",
    }]
    mod.view_now = lambda: today.replace(hour=8, minute=5)
    text = "".join(t for _, t in mod.render_focus_compact())
    assert "午:00" in text
    assert "m5x2 Strat" in text


def _gcal_event(title, start, end, calendar="Outlook"):
    return {"start_dt": start, "end_dt": end, "title": title, "calendar": calendar,
            "all_day": False, "transparency": "opaque"}


def test_current_block_shows_upcoming_meeting_title_not_just_a_glyph():
    """Regression (2026-07-15, user report: "it doesn't seem like janus is
    showing the other events... specifically the other three meetings that
    should be in my outlook"): a gcal event later in the CURRENT block used
    to draw only an anonymous "◇ │" continuation glyph via the cont dict —
    never its title. The event must show as a real row, same as it would in
    a future block, with a parenthesized (not tracked) duration."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = []
    mod.STATE.current = None
    mod.STATE.events = [_gcal_event("1:1 Jonathan & Scott",
                                    today.replace(hour=11, minute=5),
                                    today.replace(hour=11, minute=30))]
    mod.view_now = lambda: today.replace(hour=10, minute=31)  # 午 = 10-11
    text = "".join(t for _, t in mod.render_focus_compact())
    top = text.split("未:00")[0]
    assert "1:1 Jonathan & Scott" in top
    assert "(25)" in top, "scheduled (not tracked) duration must be parenthesized"


def test_current_block_shows_in_progress_meeting_even_though_it_already_started():
    """A meeting that started BEFORE now but hasn't ended yet (the case that
    matters most for turning a live meeting into a time entry) must still
    show — not just ones starting later."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = []
    mod.STATE.current = None
    mod.STATE.events = [_gcal_event("Gen 10 Console Forecast Walkthrough",
                                    today.replace(hour=10, minute=30),
                                    today.replace(hour=11, minute=0))]
    mod.view_now = lambda: today.replace(hour=10, minute=45)  # mid-meeting
    text = "".join(t for _, t in mod.render_focus_compact())
    top = text.split("未:00")[0]
    assert "Gen 10 Console Forecast Walkthrough" in top


def test_current_block_hides_fully_past_untracked_meeting():
    """A meeting that already ENDED before now, with no covering Toggl entry,
    is deliberately out of scope for now (past-calendar-vs-time-entry
    reconciliation, 2026-07-15: "maybe we don't need to make changes there
    yet") — it must not show as a phantom row."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = []
    mod.STATE.current = None
    mod.STATE.events = [_gcal_event("HL:JM 1:1",
                                    today.replace(hour=10, minute=0),
                                    today.replace(hour=10, minute=20))]
    mod.view_now = lambda: today.replace(hour=10, minute=31)
    text = "".join(t for _, t in mod.render_focus_compact())
    top = text.split("未:00")[0]
    assert "HL:JM 1:1" not in top


def test_compact_block_lines_default_max_rows_unchanged():
    """max_rows defaults to 3 — every OTHER block (render_morning /
    render_evening callers, which never pass max_rows) must render exactly
    as before this change."""
    mod = _load_tui()
    today = _midnight()
    frags = mod._compact_block_lines("辰", 6, [], 0, "")
    text = "".join(t for _, t in frags)
    assert text.count("\n") == 4, "default card must stay header + 3 body rows"


def test_compact_block_lines_max_rows_eight_uses_15min_marks_including_00():
    mod = _load_tui()
    frags = mod._compact_block_lines("巳", 8, [], 0, "", max_rows=8)
    text = "".join(t for _, t in frags)
    assert text.count("\n") == 9, "header + 8 body rows"
    # The :00 slot is the header's own row ("巳:00"); the body covers the
    # other 7 slots up to the hour rollover, then the rolled-over hour. Each
    # empty mark carries a "·" placeholder (restored 2026-07-15).
    body = text.split("\n", 1)[1]
    assert ":00 ·\n" in body, "the :00 mark below the header (blk_sh's own :00, not the header)"
    for mm in (":15", ":30", ":45", "09:00"):
        assert mm in body


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
