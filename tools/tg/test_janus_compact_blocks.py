"""Compact-block layout rules: block-ritual emojis sit to the right of the
``block:mm`` stamp (not between block char and minute), and gcal events that
flow through a future block draw the focus band's ◇ │ continuation glyphs
instead of leaving the block blank."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_compact", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_compact"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _pick(mod, label, start, dur=30):
    return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": label,
            "style": "", "dur_min": dur}


def test_header_is_bare_block_stamp_entry_in_body():
    """Non-focus PAST block: header is the bare `午:00` :00 slot with the
    ritual points label at the RIGHT edge (user request 2026-07-21: "in the
    block lines... not in the header"); the entry sits in the body (no
    longer riding the header rule)."""
    mod = _load_tui()
    start = _midnight().replace(hour=12, minute=1)
    frags = mod._compact_block_lines("午", 12, [_pick(mod, "Blizz", start)], 0, "₦4")
    text = "".join(t for _, t in frags)
    header = text.split("\n")[0]
    assert header.startswith("午:00"), f"header starts with block:00, got: {header!r}"
    assert header.endswith("₦4"), f"pts label right-aligned, got: {header!r}"
    assert "Blizz" not in header, "entry must not ride the header"
    assert "Blizz" in text, "entry shows in the body"
    assert not header.startswith("─"), "no dashed rule header anymore"


def test_future_block_header_carries_event_with_minute_duration():
    """Non-focus FUTURE block: the dominant upcoming event rides the header
    with its duration as (N) minutes; a ₦ label (rare on a future block)
    trails at the line's end."""
    mod = _load_tui()
    start = _midnight().replace(hour=14, minute=0)
    frags = mod._compact_block_lines(
        "申", 14, [_pick(mod, "Strategy", start, dur=60)], 0, "₦1", is_future=True)
    header = "".join(t for _, t in frags).split("\n")[0]
    assert header.startswith("申:00"), f"header starts with block:00, got: {header!r}"
    assert "Strategy" in header and "(60)" in header, f"event + (N) on header: {header!r}"
    assert header.endswith("₦1"), f"₦ label trails the header line, got: {header!r}"


def test_ritual_pts_label_sums_config_points():
    """Emoji stamps convert to a bare points label (₦ dropped 2026-07-21): ☀️=1 + 📧=3 = 4; all five
    rituals = ₦13; the 😈 auto-marker and an unstamped header score nothing
    (user request 2026-07-20: show the block's -1n points, not the icons)."""
    mod = _load_tui()
    assert mod._ritual_pts_label("☀️📧") == "4"
    assert mod._ritual_pts_label("☀️📧🎯⏱️✅") == "13"
    assert mod._ritual_pts_label("😈") == ""
    assert mod._ritual_pts_label("") == ""


def test_future_partial_block_shows_remaining_marks_not_blank():
    """Regression (2026-06-27): a future block with one event on the header used
    to blank-pad its remaining rows (午 dropped its 10:30 / 11:00). Now those
    half-hour marks render as just the time."""
    mod = _load_tui()
    start = _midnight().replace(hour=10, minute=0)
    frags = mod._compact_block_lines(
        "午", 10, [_pick(mod, "sync", start, dur=30)], 0, "", is_future=True)
    lines = [ln for ln in "".join(t for _, t in frags).split("\n")]
    body = [ln for ln in lines[1:] if ln != ""]
    assert len(body) == 3, f"expected 3 non-blank body rows, got {body}"
    assert any("11:00" in ln for ln in body), f"11:00 mark missing: {body}"
    assert sum(1 for ln in body if ln.startswith("  :30")) == 2  # 10:30 & 11:30


def test_abbreviated_time_column_rolls_hour_over():
    """Body time column prints the full HH:MM only when the hour changes; the
    same-hour mark abbreviates to `  :30`."""
    mod = _load_tui()
    start = _midnight().replace(hour=10, minute=0)
    frags = mod._compact_block_lines(
        "午", 10, [_pick(mod, "sync", start, dur=30)], 0, "", is_future=True)
    body = [ln for ln in "".join(t for _, t in frags).split("\n")[1:] if ln]
    assert body[0].startswith("  :30"), f"10:30 abbreviates, got {body[0]!r}"
    assert body[1].startswith("11:00"), f"hour roll-over is full, got {body[1]!r}"
    assert body[2].startswith("  :30"), f"11:30 abbreviates, got {body[2]!r}"


def test_future_body_entry_duration_in_minutes():
    """A future body entry (not on the header) shows its duration as (N)."""
    mod = _load_tui()
    e1 = _midnight().replace(hour=10, minute=0)
    e2 = _midnight().replace(hour=10, minute=30)
    picks = [_pick(mod, "first", e1, dur=30), _pick(mod, "second", e2, dur=45)]
    frags = mod._compact_block_lines("午", 10, picks, 0, "", is_future=True)
    text = "".join(t for _, t in frags)
    assert "first (30)" in text.split("\n")[0], "first rides header with (30)"
    assert "second" in text and "(45)" in text, "second is a body row with (45)"


def test_through_event_draws_continuation_in_empty_block():
    """An event spanning the whole block (started earlier) → its 3 body marks
    (the :00 slot is the bare header) draw ◇ │, not the untracked ┄ grid."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.events = [{
        "title": "XBOX Workshop", "start_dt": today.replace(hour=10),
        "end_dt": today.replace(hour=16),
    }]
    cont = mod._block_gcal_cont(12, today)
    assert set(cont) == {(12, 0), (12, 30), (13, 0), (13, 30)}
    frags = mod._compact_block_lines("未", 12, [], 0, "", cont=cont)
    text = "".join(t for _, t in frags)
    assert text.count("◇ │") == 3  # 12:30 / 13:00 / 13:30; 12:00 is the header
    assert "┄" not in text


def test_partial_coverage_mixes_grid_and_continuation():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.events = [{
        "title": "Workshop", "start_dt": today.replace(hour=10),
        "end_dt": today.replace(hour=13),
    }]
    cont = mod._block_gcal_cont(12, today)
    assert set(cont) == {(12, 0), (12, 30)}
    frags = mod._compact_block_lines("未", 12, [], 0, "", cont=cont)
    text = "".join(t for _, t in frags)
    assert text.count("◇ │") == 1  # only 12:30 covered (12:00 is the header slot)
    assert "┄" not in text          # empty marks render as just the time now
    assert "13:00" in text          # uncovered marks: just the time


def test_future_header_event_spans_block_marks_continue():
    """A 2h future event rides the header; its 3 body marks draw ◇ │."""
    mod = _load_tui()
    today = _midnight()
    start = today.replace(hour=14, minute=0)
    mod.STATE.events = [{
        "title": "Strategy", "start_dt": start, "end_dt": today.replace(hour=16),
    }]
    cont = mod._block_gcal_cont(14, today)
    frags = mod._compact_block_lines(
        "申", 14, [_pick(mod, "Strategy", start, dur=120)], 0, "",
        cont=cont, is_future=True)
    text = "".join(t for _, t in frags)
    assert "Strategy (120)" in text.split("\n")[0]
    assert text.count("◇ │") == 3  # 14:30 / 15:00 / 15:30


def test_past_block_continues_finished_event_to_its_end():
    """A long meeting that already happened keeps ◇ │ on the body marks it
    covers; the tracked toggl entry takes a body row first."""
    mod = _load_tui()
    today = _midnight()
    mod.STATE.entries = [
        {"start_dt": today.replace(hour=10, minute=1),
         "end_dt": today.replace(hour=12), "desc": "blizz",
         "project_id": None, "running": False, "id": 1},
    ]
    mod.STATE.entries_yday = []
    mod.STATE.block_points = {}
    mod.STATE.events = [{
        "title": "XBOX Workshop", "start_dt": today.replace(hour=10),
        "end_dt": today.replace(hour=14),
    }]
    mod.detail_window = lambda: (today.replace(hour=12), today.replace(hour=16))
    text = "".join(t for _, t in mod.render_morning())
    wu = [ln for ln in text.split("\n")]
    block = "\n".join(wu[wu.index(next(l for l in wu if l.startswith("午:00"))):][:4])
    assert "blizz" in block, "toggl entry takes a body row"
    # blizz@10:01 + the two fill marks 10:30/11:00 (both covered) → 2 ◇ │.
    assert block.count("◇ │") == 2


def test_transparent_and_allday_events_do_not_continue():
    mod = _load_tui()
    today = _midnight()
    mod.STATE.events = [
        {"title": "OOO", "start_dt": today.replace(hour=10),
         "end_dt": today.replace(hour=16), "transparency": "transparent"},
        {"title": "Birthday", "start_dt": today, "end_dt": today + dtm.timedelta(days=1),
         "all_day": True},
    ]
    assert mod._block_gcal_cont(12, today) == {}
