"""Regression tests for the toggl_range MCP tool (2026-09-02).

Added because /1s's weekly review was making 7 sequential toggl_date calls
(one per day) in a tight burst — the single heaviest contributor to hitting
Toggl's 600/hr API quota this session. toggl_range fetches the whole range
in ONE Toggl API call and buckets entries into per-day sections client-side,
reusing the same UTC-padding + local-date-filter logic toggl_date already
uses for a single day.

Key things this must not regress:
- Exactly one get_entries() call regardless of range length (the whole point).
- Each day's entries land in that day's section, not a neighbor's (the
  bucketing must not just dump everything into every section).
- Output for a 1-day range matches toggl_date's output for that same day
  byte-for-byte (the shared _format_day_section refactor must not have
  changed toggl_date's existing behavior).
- The UTC-boundary padding still catches cross-midnight/late-night entries
  at the EDGES of the range (not just mid-range days), same bug class as
  test_server_filter_includes_cross_midnight_clipped in
  test_toggl_cli_today.py.
"""
import datetime
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from test_toggl_cli_today import _load_server_module  # noqa: E402


def _day_section(out: str, date_str: str) -> str:
    """Extract one day's section from toggl_range's concatenated output.
    Sections are joined with "\\n\\n", but each section ALSO contains
    internal "\\n\\n" (between the entry list and the project breakdown),
    so a naive out.split("\\n\\n") over-splits — slice between this day's
    "# YYYY-MM-DD" header and the next one instead."""
    marker = f"# {date_str}"
    start = out.index(marker)
    nxt = out.find("\n\n# 20", start)
    return out[start:nxt] if nxt != -1 else out[start:]


def _entry(eid, desc, start_dt, stop_dt, project_id=None):
    return {
        "id": eid,
        "description": desc,
        "project_id": project_id,
        "start": start_dt.isoformat(),
        "stop": stop_dt.isoformat() if stop_dt else None,
        "duration": (int((stop_dt - start_dt).total_seconds()) if stop_dt else -1),
    }


def test_toggl_range_makes_exactly_one_api_call(monkeypatch):
    srv = _load_server_module()
    calls = []
    monkeypatch.setattr(srv.toggl_api, "get_entries",
                         lambda **kw: calls.append(kw) or [])
    srv.toggl_range("2026-08-23", "2026-08-29")  # 7-day range
    assert len(calls) == 1, f"expected 1 API call for a 7-day range, got {len(calls)}"


def test_toggl_range_pads_one_day_each_side(monkeypatch):
    srv = _load_server_module()
    calls = []
    monkeypatch.setattr(srv.toggl_api, "get_entries",
                         lambda **kw: calls.append(kw) or [])
    srv.toggl_range("2026-08-23", "2026-08-25")
    assert calls[0]["start_date"] == "2026-08-22"  # start - 1 day
    assert calls[0]["end_date"] == "2026-08-27"    # end + 2 days (exclusive-end pad)


def test_toggl_range_buckets_entries_into_correct_day(monkeypatch):
    srv = _load_server_module()
    TZ = srv.TZ
    d1 = datetime.date(2026, 8, 23)
    d2 = datetime.date(2026, 8, 24)
    d3 = datetime.date(2026, 8, 25)
    e1 = _entry(1, "day1 task", datetime.datetime.combine(d1, datetime.time(9, 0), tzinfo=TZ),
                datetime.datetime.combine(d1, datetime.time(9, 30), tzinfo=TZ))
    e2 = _entry(2, "day2 task", datetime.datetime.combine(d2, datetime.time(9, 0), tzinfo=TZ),
                datetime.datetime.combine(d2, datetime.time(9, 30), tzinfo=TZ))
    e3 = _entry(3, "day3 task", datetime.datetime.combine(d3, datetime.time(9, 0), tzinfo=TZ),
                datetime.datetime.combine(d3, datetime.time(9, 30), tzinfo=TZ))
    monkeypatch.setattr(srv.toggl_api, "get_entries", lambda **kw: [e1, e2, e3])

    out = srv.toggl_range("2026-08-23", "2026-08-25")
    s1 = _day_section(out, "2026-08-23")
    s2 = _day_section(out, "2026-08-24")
    s3 = _day_section(out, "2026-08-25")
    assert "day1 task" in s1 and "day2 task" not in s1 and "day3 task" not in s1
    assert "day2 task" in s2 and "day1 task" not in s2 and "day3 task" not in s2
    assert "day3 task" in s3 and "day1 task" not in s3 and "day2 task" not in s3


def test_toggl_range_matches_toggl_date_for_single_day(monkeypatch):
    """The shared _format_day_section refactor must not change toggl_date's
    existing output — compare a 1-day toggl_range call against toggl_date
    for the identical mocked entries."""
    srv = _load_server_module()
    TZ = srv.TZ
    d = datetime.date(2026, 8, 24)
    entries = [
        _entry(1, "work", datetime.datetime.combine(d, datetime.time(9, 0), tzinfo=TZ),
               datetime.datetime.combine(d, datetime.time(10, 0), tzinfo=TZ), project_id=1),
        _entry(2, "lunch", datetime.datetime.combine(d, datetime.time(12, 0), tzinfo=TZ),
               datetime.datetime.combine(d, datetime.time(12, 30), tzinfo=TZ)),
    ]
    monkeypatch.setattr(srv.toggl_api, "get_entries", lambda **kw: entries)

    range_out = srv.toggl_range("2026-08-24", "2026-08-24")
    date_out = srv.toggl_date("2026-08-24")
    assert range_out == date_out, f"\n--range--\n{range_out}\n--date--\n{date_out}"


def test_toggl_range_catches_cross_midnight_entry_at_range_edge(monkeypatch):
    """Same bug class as test_server_filter_includes_cross_midnight_clipped:
    an overnight entry spanning the END of the range must still be visible
    on the last day's section, not silently dropped by the range padding."""
    srv = _load_server_module()
    TZ = srv.TZ
    last_day = datetime.date(2026, 8, 29)
    next_day = last_day + datetime.timedelta(days=1)
    start = datetime.datetime.combine(last_day, datetime.time(21, 47), tzinfo=TZ)
    stop = datetime.datetime.combine(next_day, datetime.time(7, 38), tzinfo=TZ)
    entry = _entry(9, "睡觉", start, stop)
    monkeypatch.setattr(srv.toggl_api, "get_entries", lambda **kw: [entry])

    out = srv.toggl_range("2026-08-23", "2026-08-29")
    last_section = _day_section(out, "2026-08-29")
    assert "睡觉" in last_section, last_section


def test_toggl_range_rejects_end_before_start():
    srv = _load_server_module()
    out = srv.toggl_range("2026-08-29", "2026-08-23")
    assert "Error" in out


def test_toggl_range_rejects_ranges_over_31_days():
    srv = _load_server_module()
    out = srv.toggl_range("2026-01-01", "2026-03-01")
    assert "Error" in out
