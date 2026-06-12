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
    spec = importlib.util.spec_from_file_location("tg_tui_compact", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_compact"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _pick(mod, label, start, dur=30):
    return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": label,
            "style": "", "dur_min": dur}


def test_emojis_right_of_block_minute_stamp():
    mod = _load_tui()
    start = _midnight().replace(hour=12, minute=1)
    frags = mod._compact_block_lines("午", 12, [_pick(mod, "Blizz", start)], 0, "☀️📧")
    header = "".join(t for _, t in frags).split("\n")[0]
    assert "午:01 ☀️📧" in header, f"emojis must follow 午:01, got: {header!r}"
    assert "午 ☀️📧:01" not in header


def test_through_event_draws_continuation_in_empty_block():
    """An event spanning the whole block (started earlier) → four ◇ │ rows,
    not the untracked ┄ grid."""
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
    assert text.count("◇ │") == 4
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
    assert text.count("◇ │") == 2
    assert text.count("┄" * 42) == 2  # 13:00 / 13:30 stay untracked grid


def test_pads_after_header_event_continue_event():
    """First (header) event runs the entire block → the 3 pad rows show the
    half-hour marks after its start as ◇ │."""
    mod = _load_tui()
    today = _midnight()
    start = today.replace(hour=14, minute=0)
    mod.STATE.events = [{
        "title": "Strategy", "start_dt": start, "end_dt": today.replace(hour=16),
    }]
    cont = mod._block_gcal_cont(14, today)
    frags = mod._compact_block_lines(
        "申", 14, [_pick(mod, "Strategy", start, dur=120)], 0, "", cont=cont)
    text = "".join(t for _, t in frags)
    assert "14:30 " in text and "15:00 " in text and "15:30 " in text
    assert text.count("◇ │") == 3


def test_past_block_continues_finished_event_to_its_end():
    """A long meeting that already happened keeps its ◇ │ rows in the morning
    view through the event's end; tracked toggl rows still come first."""
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
    block = "\n".join(wu[wu.index(next(l for l in wu if l.startswith("─午"))):][:4])
    assert "blizz" in block, "toggl entry keeps the header"
    assert block.count("◇ │") == 3, "pads continue the event: 10:30/11:00/11:30"


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
