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


def test_zero_slot_entry_rides_header_no_duplicate_row():
    """Non-focus PAST block: an entry occupying the :00 slot rides the header
    line itself — `午:00 Blizz ... 30m ₦4` — instead of duplicating a `  :00`
    body row right under a bare header (user request 2026-07-30: "Shouldn't
    XBOX Developer be on the 未 line rather than repeating it?"). The ₦ label
    keeps the right edge."""
    mod = _load_tui()
    start = _midnight().replace(hour=12, minute=0)
    frags = mod._compact_block_lines("午", 12, [_pick(mod, "Blizz", start)], 0, "₦4")
    text = "".join(t for _, t in frags)
    lines = text.split("\n")
    header = lines[0]
    assert header.startswith("午:00"), f"header starts with block:00, got: {header!r}"
    assert header.endswith("₦4"), f"pts label right-aligned, got: {header!r}"
    assert "Blizz" in header, "the :00 entry rides the header"
    assert not any("Blizz" in ln for ln in lines[1:]), "no duplicated body row"
    assert not any(ln.startswith("  :00") for ln in lines[1:]), \
        "the vacated :00 slot must not grow a grid-mark row"


def test_deleted_calendar_event_lets_the_next_real_entry_take_the_header():
    """The exact user-reported scenario, verbatim (2026-08-06): "we should
    have 未:00 with EL:JM 1:1 on the right, and then after I delete that
    from the calendar, it should be 未:15 with -1g as the time entry.
    Essentially every hour/block :00 doesn't need to be its own line."
    Modeled as two independent renders of the same block — before the
    calendar event exists (it rides the header at :00) and after it's
    deleted (the -1g entry at :15 takes over, still just ONE header line,
    never a bare :00 header stacked above it)."""
    mod = _load_tui()
    zero = _midnight().replace(hour=12, minute=0)
    quarter = _midnight().replace(hour=12, minute=15)

    before = "".join(t for _, t in mod._compact_block_lines(
        "未", 12, [_pick(mod, "EL:JM 1:1", zero)], 0, ""))
    before_header = before.split("\n")[0]
    assert before_header.startswith("未:00"), f"got: {before_header!r}"
    assert "EL:JM 1:1" in before_header

    after = "".join(t for _, t in mod._compact_block_lines(
        "未", 12, [_pick(mod, "-1g", quarter)], 0, ""))
    lines = after.split("\n")
    after_header = lines[0]
    assert after_header.startswith("未:15"), f"got: {after_header!r}"
    assert "-1g" in after_header, "the surviving entry takes the header, not a body row"
    assert not any(ln.startswith("未:00") or ln.startswith("  :00") for ln in lines), \
        "no leftover bare :00 header line once the :00 event is gone"


def test_mid_block_entry_rides_header_with_its_own_time():
    """Widened 2026-08-06 (user request: "every hour/block :00 doesn't need
    to be its own line"): the block's FIRST real entry rides the header
    regardless of whether it starts exactly at :00 — labelled with its own
    time (`午:01`, not the literal `午:00`) instead of leaving a bare header
    and demoting the entry to an abbreviated body row two lines down."""
    mod = _load_tui()
    start = _midnight().replace(hour=12, minute=1)
    frags = mod._compact_block_lines("午", 12, [_pick(mod, "Blizz", start)], 0, "₦4")
    text = "".join(t for _, t in frags)
    lines = text.split("\n")
    header = lines[0]
    assert header.startswith("午:01"), f"header shows the entry's own time, got: {header!r}"
    assert header.endswith("₦4")
    assert "Blizz" in header, "the block's first (and only) entry rides the header"
    assert not any("Blizz" in ln for ln in lines[1:]), "no duplicated body row"


def test_second_hour_entry_rides_header_with_full_time_and_correct_gutter():
    """When the block's first real entry falls in its SECOND hour (e.g. a
    12:00-14:00 block whose earliest entry is at 13:05), the header shows
    the full HH:MM (space-separated from the block name, not fused into a
    `blk:MM` suffix that would misread as the block's own hour) — and the
    header's busy-bar gutter cell must reflect THAT slot (13:05), not the
    block's own :00 slot it no longer represents."""
    mod = _load_tui()
    start = _midnight().replace(hour=13, minute=5)
    mod.STATE.events = [{
        "title": "conflict", "start_dt": start, "end_dt": start.replace(minute=35),
    }]
    frags = mod._compact_block_lines("未", 12, [_pick(mod, "late start", start)], 0, "")
    text = "".join(t for _, t in frags)
    header = text.split("\n")[0]
    assert header.startswith("未 13:05"), f"got: {header!r}"
    assert "late start" in header
    # The busy-bar gutter is a single glyph cell rendered as its own
    # fragment; a stale (blk_sh, 0)-keyed lookup would find no coverage at
    # 12:00 and render a plain space instead of the busy glyph.
    gutter_frags = [t for s, t in frags if s == "class:gutter_busy"]
    assert gutter_frags and gutter_frags[0] == "▍", (
        "header gutter must reflect the entry's OWN slot (13:00-13:30, "
        f"covered by the conflicting event), got fragments: {gutter_frags!r}")


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
    assert text.count("│ ◇") == 3  # 12:30 / 13:00 / 13:30; 12:00 is the header
    assert "◇ │" not in text  # gcal continuation right-justifies (2026-07-30)
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
    assert text.count("│ ◇") == 1  # only 12:30 covered (12:00 is the header slot)
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
    assert text.count("│ ◇") == 3  # 14:30 / 15:00 / 15:30


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
    # 2026-08-06: blizz@10:01 is the block's first real entry, so it now
    # rides the header itself (`午:01`) instead of a bare `午:00` header with
    # blizz relegated to a body row underneath.
    block = "\n".join(wu[wu.index(next(l for l in wu if l.startswith("午:01"))):][:4])
    assert "blizz" in block, "the block's first entry rides the header"
    # blizz now occupies the header instead of a body row, freeing that body
    # slot for one more continuation mark: 10:30/11:00/11:30 all covered by
    # the still-running event (tracked reality wins where it overlaps, but
    # nothing tracked competes for these three anymore).
    assert block.count("◇ │") == 3


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
