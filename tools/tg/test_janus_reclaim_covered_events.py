"""User request 2026-07-29: "if there is any toggl entry (even a clock I
didn't turn off) then the calendar events disappear ... let me still select
calendar events so I can bring the meetings into toggl and get the credit."

_past_event_picks used to hide any ended event with ANY overlapping Toggl
entry. A runaway clock (one long undifferentiated entry spanning several
meetings) therefore made every meeting unselectable — and unconvertible.
_event_reclaimable lets a covered event through when the covering entry is
>60m AND ≥30m longer than the event (a meeting deliberately tracked as its
own entry stays hidden — no double-credit bait). No recency or same-day cut
("often the stale toggl entry is from the previous day"); yesterday-started
overnight clocks count as covering candidates."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_reclaim", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_reclaim"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _entry(start, end, desc="work", eid=1):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": 1, "running": False, "id": eid, "tags": []}


def _event(title, start, end):
    return {"start_dt": start, "end_dt": end, "title": title, "calendar": "Outlook",
            "all_day": False, "transparency": "opaque"}


def test_runaway_clock_meeting_is_reclaimable():
    mod = _load_tui()
    mod.STATE.day_offset = 0
    today = _midnight()
    now = today.replace(hour=13)
    clock = _entry(today.replace(hour=9), today.replace(hour=13))  # 4h runaway
    standup = _event("XCORE Standup", today.replace(hour=10, minute=30),
                     today.replace(hour=11))
    assert mod._event_reclaimable(standup, [clock], now) is True


def test_deliberately_tracked_meeting_stays_hidden():
    """Entry ≈ meeting length → it IS the meeting's tracking; resurfacing it
    would invite double-booking + double points."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    today = _midnight()
    now = today.replace(hour=15)
    mtg_entry = _entry(today.replace(hour=13), today.replace(hour=14),
                       desc="martech understanding")
    mtg = _event("Martech Understanding", today.replace(hour=13),
                 today.replace(hour=14))
    assert mod._event_reclaimable(mtg, [mtg_entry], now) is False


def test_short_covering_entry_not_reclaim_bait():
    mod = _load_tui()
    mod.STATE.day_offset = 0
    today = _midnight()
    now = today.replace(hour=11)
    halfhour = _entry(today.replace(hour=10), today.replace(hour=10, minute=45))
    mtg = _event("1:1", today.replace(hour=10), today.replace(hour=10, minute=30))
    assert mod._event_reclaimable(mtg, [halfhour], now) is False, \
        "covering entry must exceed 60m to smell like a runaway clock"


def test_no_recency_cut_and_yesterday_clock_counts():
    """User: "I want it to span days, because often the stale toggl entry is
    from the previous day." A morning meeting covered only by an overnight
    clock STARTED YESTERDAY (which lives in entries_yday, not entries) must
    still be reclaimable, and an old same-day meeting stays reclaimable —
    no recency window."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    today = _midnight()
    now = today.replace(hour=20)
    # Old same-day meeting under a same-day runaway clock: still reclaimable.
    clock = _entry(today.replace(hour=8), today.replace(hour=14))
    old_mtg = _event("morning sync", today.replace(hour=9), today.replace(hour=9, minute=30))
    mod.STATE.entries_yday = []
    assert mod._event_reclaimable(old_mtg, [clock], now) is True
    # Overnight clock from YESTERDAY covering a 05:30 meeting today.
    overnight = _entry(today - dtm.timedelta(hours=4), today.replace(hour=6, minute=49),
                       desc="fall asleep")
    dawn_mtg = _event("APAC sync", today.replace(hour=5, minute=30), today.replace(hour=6))
    mod.STATE.entries_yday = [overnight]
    assert mod._event_reclaimable(dawn_mtg, [], now) is True, \
        "yesterday-started clocks must count as covering candidates"
    mod.STATE.entries_yday = []


def test_past_event_picks_includes_reclaimable_covered_meeting():
    """End-to-end through the pick pipeline: the covered standup must come
    back as a selectable is_event pick despite the runaway clock."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    today = _midnight()
    now = today.replace(hour=12, minute=30)
    clock = _entry(today.replace(hour=9), today.replace(hour=12, minute=15))
    standup = _event("XCORE Standup", today.replace(hour=10, minute=30),
                     today.replace(hour=11))
    picks = mod._past_event_picks("午", [standup], [clock], now)
    assert [p["label"] for p in picks] == ["XCORE Standup"]
    assert picks[0]["is_event"] and picks[0]["event"] is standup, \
        "carries the raw event so Enter-conversion (did-fast trim) works"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_alt_enter_on_calendar_event_runs_shared_conversion():
    """User request 2026-07-29 follow-up: ⌥↵ on a calendar event must run
    the SAME convert-and-credit path as Enter (did-fast for ended events =
    Toggl entry + points), not flash "select a tracked entry first"."""
    src = (HERE / "janus.py").read_text()
    i = src.index('kb.add("escape", "enter")')
    body = src[i:src.index('def _run_did_and_refresh', i)]
    assert "_convert_selected_event" in body, "⌥↵ must route raw events to the shared conversion"
    assert body.index("_convert_selected_event") < body.index("select a tracked entry first")
    # And Enter's event branch uses the same helper — one conversion path.
    assert src.count("_convert_selected_event(item, event.app)") == 2


def test_same_day_duplicate_entry_reclaims_covered_meeting():
    """The CosmosDB case (2026-07-29): `read fy2027 priorities` 14:31-15:28
    (57m — under the 60m runaway floor) covered the 45m CosmosDB meeting,
    but the desc duplicates the 13:37-13:59 entry — a restarted activity
    timer that ran through the meeting. The dup is the overrun signal."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod.STATE.entries_yday = []
    today = _midnight()
    now = today.replace(hour=15, minute=30)
    first = _entry(today.replace(hour=13, minute=37), today.replace(hour=13, minute=59),
                   desc="read fy2027 priorities", eid=1)
    resumed = _entry(today.replace(hour=14, minute=31), today.replace(hour=15, minute=28),
                     desc="read fy2027 priorities", eid=2)
    cosmos = _event("CosmosDB Deprecation, Part 3", today.replace(hour=14, minute=30),
                    today.replace(hour=15, minute=15))
    assert mod._event_reclaimable(cosmos, [first, resumed], now) is True


def test_unique_meeting_sized_entry_still_hidden():
    """No dup + under the runaway floor → still treated as the meeting's own
    deliberate tracking."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod.STATE.entries_yday = []
    today = _midnight()
    now = today.replace(hour=16)
    only = _entry(today.replace(hour=14, minute=31), today.replace(hour=15, minute=28),
                  desc="read fy2027 priorities", eid=2)
    cosmos = _event("CosmosDB Deprecation, Part 3", today.replace(hour=14, minute=30),
                    today.replace(hour=15, minute=15))
    assert mod._event_reclaimable(cosmos, [only], now) is False


def test_zero_minute_earlier_entry_is_not_a_dup_basis():
    """0m artifacts (double-taps) must not mark every later same-name entry
    as overrun."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod.STATE.entries_yday = []
    today = _midnight()
    blip = _entry(today.replace(hour=13, minute=36), today.replace(hour=13, minute=36),
                  desc="tasks", eid=1)
    later = _entry(today.replace(hour=14), today.replace(hour=14, minute=40),
                   desc="tasks", eid=2)
    assert mod._same_day_dup(later, [blip, later]) is False


def test_partial_overlap_dup_does_not_resurface_event():
    """巳 regression (2026-07-29): a 13m 冥想 (dup of an earlier 冥想)
    brushing the tail of a 45m PT session resurfaced the whole event even
    though the window was tracked granularly. The suspect must cover ≥80%
    of the meeting by itself."""
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod.STATE.entries_yday = []
    today = _midnight()
    now = today.replace(hour=11)
    med1 = _entry(today.replace(hour=9, minute=10), today.replace(hour=9, minute=19),
                  desc="冥想", eid=1)
    hiit = _entry(today.replace(hour=9, minute=20), today.replace(hour=9, minute=46),
                  desc="hiit", eid=2)
    med2 = _entry(today.replace(hour=9, minute=47), today.replace(hour=10, minute=1),
                  desc="冥想", eid=3)
    pt = _event("Potrero PT", today.replace(hour=9, minute=15), today.replace(hour=10))
    assert mod._event_reclaimable(pt, [med1, hiit, med2], now) is False


def test_conversion_command_strips_commas_from_title():
    """"CosmosDB Deprecation, Part 3" split on the comma inside did-fast and
    created a Toggl entry literally named "Part" (2026-07-29). Separators
    must never survive into the command string."""
    mod = _load_tui()
    today = _midnight()
    ev = _event("CosmosDB Deprecation, Part 3", today.replace(hour=14, minute=30),
                today.replace(hour=15, minute=15))
    cmd = mod._event_to_did_command(ev)
    assert "," not in cmd and ";" not in cmd
    assert cmd.startswith("CosmosDB Deprecation Part 3 1430-1515")


def test_fetch_gcal_shape_dedupes_cross_calendar_copies():
    """The same meeting arrives from Outlook AND the MSFT-import calendar;
    reclaim surfaced it as triple rows ("Potrero PT" ×3). fetch_gcal must
    keep one copy per (title, start, end)."""
    src = (HERE / "janus.py").read_text()
    i = src.index("def fetch_gcal")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "seen_ev" in body and "deduped" in body


def test_row_cap_prefers_tracked_entries_over_event_picks():
    """"if there are good toggl entries, then toggl should be the default":
    a big reclaimed event pick must not crowd real entries out of the card."""
    mod = _load_tui()
    mod.STATE.events = []
    today = _midnight()
    picks = [
        {"start_dt": today.replace(hour=8, minute=48), "time_str": "", "label": "asha sync",
         "style": "", "dur_min": 22, "entry_ids": [1], "raw_desc": "asha sync", "project_id": None},
        {"start_dt": today.replace(hour=9, minute=10), "time_str": "", "label": "冥想",
         "style": "", "dur_min": 8, "entry_ids": [2], "raw_desc": "冥想", "project_id": None},
        {"start_dt": today.replace(hour=9, minute=20), "time_str": "", "label": "hiit",
         "style": "", "dur_min": 26, "entry_ids": [3], "raw_desc": "hiit", "project_id": None},
        {"start_dt": today.replace(hour=9, minute=15), "time_str": "", "label": "Potrero PT",
         "style": "", "dur_min": 45, "is_event": True,
         "event": _event("Potrero PT", today.replace(hour=9, minute=15), today.replace(hour=10))},
    ]
    text = "".join(t for _, t in mod._compact_block_lines("巳", 8, picks, 0, ""))
    for name in ("asha sync", "冥想", "hiit"):
        assert name in text, f"tracked entry {name!r} must survive the cap"


def test_entry_rows_show_no_project_code_suffix():
    """Colors carry the project — labels drop the " · code" suffix
    (user 2026-07-29)."""
    src = (HERE / "janus.py").read_text()
    i = src.index("def _past_block_picks")
    body = src[i:src.index("\ndef ", i + 10)]
    assert '· {code}' not in body
    i = src.index("def _block_spill_items")
    body = src[i:src.index("\ndef ", i + 10)]
    assert '· {code}' not in body
