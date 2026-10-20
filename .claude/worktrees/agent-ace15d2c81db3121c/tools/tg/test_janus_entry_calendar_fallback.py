"""Regression test: granting points (opt+enter) for a tracked Toggl entry
whose OWN project_id doesn't resolve to a code.

Bug (user report 2026-08-16): a real meeting ("IM|JM 1|1", on the "m5x2 Cal"
calendar) was tracked as a Toggl entry and rendered correctly in m5x2's red
on screen, but opt+enter to grant its points failed with "no match, needs
domain disambiguation" — did-fast never saw an `@m5x2` suffix because the
handler resolved the entry's project via the bare, no-fallback `proj_code()`
instead of anything that could fall back to the calendar the meeting (and
its color) actually came from.

Fix: `_calendar_fallback_code()` — when the entry's own project code is
empty, look for a same-titled, time-overlapping calendar event in
STATE.events and borrow ITS resolved code via gcal_project_code(). The
handler now also uses toggl_project_code() (not bare proj_code()) as its
first attempt, matching every other project-code resolution in this file.
"""
import datetime as dt
import importlib.util
import inspect
import sys
import zoneinfo
from pathlib import Path

HERE = Path(__file__).parent
TZ = zoneinfo.ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_t2", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_t2"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ev(title, start, end, calendar):
    return {"title": title, "start_dt": start, "end_dt": end, "calendar": calendar}


def test_calendar_fallback_resolves_m5x2_from_matching_calendar_event():
    m = _load_tui()
    start = dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
    end = dt.datetime(2026, 8, 16, 10, 24, tzinfo=TZ)
    m.STATE.events = [_ev("IM|JM 1|1",
                          dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ),
                          dt.datetime(2026, 8, 16, 10, 30, tzinfo=TZ),
                          "m5x2 Cal")]
    assert m._calendar_fallback_code("IM|JM 1|1", start, end) == "m5x2"


def test_calendar_fallback_ignores_non_overlapping_event():
    m = _load_tui()
    start = dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
    end = dt.datetime(2026, 8, 16, 10, 24, tzinfo=TZ)
    m.STATE.events = [_ev("IM|JM 1|1",
                          dt.datetime(2026, 8, 16, 14, 0, tzinfo=TZ),
                          dt.datetime(2026, 8, 16, 14, 30, tzinfo=TZ),
                          "m5x2 Cal")]
    assert m._calendar_fallback_code("IM|JM 1|1", start, end) == ""


def test_calendar_fallback_ignores_different_title():
    m = _load_tui()
    start = dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
    end = dt.datetime(2026, 8, 16, 10, 24, tzinfo=TZ)
    m.STATE.events = [_ev("some other meeting",
                          dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ),
                          dt.datetime(2026, 8, 16, 10, 30, tzinfo=TZ),
                          "m5x2 Cal")]
    assert m._calendar_fallback_code("IM|JM 1|1", start, end) == ""


def test_calendar_fallback_no_events_returns_empty():
    m = _load_tui()
    m.STATE.events = []
    start = dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
    end = dt.datetime(2026, 8, 16, 10, 24, tzinfo=TZ)
    assert m._calendar_fallback_code("IM|JM 1|1", start, end) == ""


def test_calendar_fallback_skips_uncolorable_calendar():
    """A same-titled overlapping event on a calendar/keyword set that
    gcal_project_code itself can't resolve contributes nothing (stays "",
    not some accidental match). Title deliberately carries none of
    EVENT_KEYWORDS' substrings (no "1|1", "m5x2", etc.)."""
    m = _load_tui()
    start = dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ)
    end = dt.datetime(2026, 8, 16, 10, 24, tzinfo=TZ)
    m.STATE.events = [_ev("Coffee with Sam",
                          dt.datetime(2026, 8, 16, 10, 0, tzinfo=TZ),
                          dt.datetime(2026, 8, 16, 10, 30, tzinfo=TZ),
                          "Some Unmapped Calendar")]
    assert m._calendar_fallback_code("Coffee with Sam", start, end) == ""


def test_entry_grant_points_handler_uses_fallback_chain_in_source():
    """opt+enter's tracked-entry branch must try toggl_project_code() (the
    literal-"m5x2"-in-title-safe resolver every other conversion path in
    this file uses) and then _calendar_fallback_code() — never fall back to
    the bare, no-safety-net proj_code() the original bug used."""
    m = _load_tui()
    src = inspect.getsource(m)
    handler_start = src.index('@kb.add("escape", "enter")')
    handler_src = src[handler_start:handler_start + 4000]
    assert "code = toggl_project_code(item.get(\"project_id\"), desc)" in handler_src
    assert "code = _calendar_fallback_code(desc, start, end)" in handler_src
