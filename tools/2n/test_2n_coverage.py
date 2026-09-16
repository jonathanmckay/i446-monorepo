"""Tests for 2n-coverage.py's pure parts: month selection, gap detection
(with the 23:59 day-barrier slack), and fill suggestions."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("cov", Path(__file__).parent / "2n-coverage.py")
cov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cov)

TZ = cov.TZ
DAY = dt.date(2026, 8, 7)


def _e(h0, m0, h1, m1, desc="x", code="i9", day=DAY):
    s = dt.datetime.combine(day, dt.time(h0, m0), TZ)
    e = dt.datetime.combine(day, dt.time(h1, m1), TZ)
    if e <= s:
        e += dt.timedelta(days=1)
    return {"start": s, "end": e, "desc": desc, "code": code}


def test_month_range_defaults_to_last_finished_month():
    assert cov.month_range(None, today=dt.date(2026, 9, 16)) == (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert cov.month_range(None, today=dt.date(2026, 1, 3)) == (dt.date(2025, 12, 1), dt.date(2025, 12, 31))
    assert cov.month_range("2026-02") == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))


def test_full_day_with_day_barrier_entries_has_no_gap():
    """睡觉 00:00–06:30 … 睡觉 21:30–23:59 is the canonical shape: the last
    minute is structural, not a gap, so coverage is a full 24:00."""
    entries = [_e(0, 0, 6, 30, "睡觉", "睡觉"), _e(6, 30, 21, 30, "day"), _e(21, 30, 23, 59, "睡觉", "睡觉")]
    assert cov.day_gaps(DAY, entries) == []
    assert cov.day_coverage_min(DAY, entries) == cov.DAY_MIN


def test_gaps_are_found_between_and_around_entries_and_overlaps_merge():
    entries = [_e(0, 0, 6, 0, "睡觉", "睡觉"), _e(6, 30, 9, 0, "a"), _e(8, 0, 12, 0, "b"),
               _e(12, 0, 23, 59, "c")]
    gaps = cov.day_gaps(DAY, entries)
    assert [(g["start"].hour, g["start"].minute, g["minutes"]) for g in gaps] == [(6, 0, 30)]
    assert gaps[0]["prev"]["desc"] == "睡觉" and gaps[0]["next"]["desc"] == "a"
    assert cov.day_coverage_min(DAY, entries) == cov.DAY_MIN - 30


def test_min_gap_filters_listing_but_not_coverage():
    entries = [_e(0, 0, 10, 0), _e(10, 2, 23, 59)]
    assert cov.day_gaps(DAY, entries, min_gap_min=5) == []
    assert cov.day_coverage_min(DAY, entries) == cov.DAY_MIN - 2


def test_entries_spanning_midnight_are_clipped_to_the_day():
    prev_day = DAY - dt.timedelta(days=1)
    entries = [_e(22, 0, 7, 0, "睡觉", "睡觉", day=prev_day), _e(7, 0, 23, 59, "day")]
    assert cov.day_gaps(DAY, entries) == []


def _gap(h0, m0, h1, m1, prev=None, nxt=None):
    s = dt.datetime.combine(DAY, dt.time(h0, m0), TZ)
    e = dt.datetime.combine(DAY, dt.time(h1, m1), TZ)
    return {"start": s, "end": e, "minutes": int((e - s).total_seconds() // 60), "prev": prev, "next": nxt}


def _ev(h0, m0, h1, m1, title, calendar="MSFT (Slow Sync)", **kw):
    return {"title": title, "calendar": calendar,
            "start_dt": dt.datetime.combine(DAY, dt.time(h0, m0), TZ),
            "end_dt": dt.datetime.combine(DAY, dt.time(h1, m1), TZ),
            "all_day": False, "transparency": "opaque", **kw}


def test_suggest_prefers_overlapping_calendar_event():
    g = _gap(10, 0, 10, 30, prev=_e(9, 0, 10, 0, "vibing"), nxt=_e(10, 30, 11, 0, "0g", "g245"))
    s = cov.suggest(g, [_ev(10, 0, 10, 30, "XCORE Weekly")])
    assert s["fill"] == "XCORE Weekly" and s["code"] == "i9" and s["confidence"] == "high"


def test_suggest_ignores_all_day_and_transparent_events():
    g = _gap(10, 0, 10, 30, prev=_e(9, 0, 10, 0, "a"), nxt=_e(10, 30, 11, 0, "b"))
    events = [_ev(10, 0, 10, 30, "Holiday", all_day=True),
              _ev(10, 0, 10, 30, "Hold", transparency="transparent")]
    assert cov.suggest(g, events)["fill"] != "Holiday"


def test_suggest_sleep_for_night_gap_next_to_sleep_entry():
    g = _gap(4, 0, 4, 20, prev=_e(0, 0, 4, 0, "睡觉", "睡觉"), nxt=_e(4, 20, 6, 0, "ren to sleep", "xk87"))
    s = cov.suggest(g, [])
    assert s["code"] == "睡觉" and s["confidence"] == "high"


def test_suggest_same_project_both_sides():
    g = _gap(14, 55, 15, 0, prev=_e(14, 0, 14, 55, "hiring checkin"), nxt=_e(15, 0, 15, 30, "JP checkin"))
    s = cov.suggest(g, [])
    assert s["code"] == "i9" and "both sides" in s["reason"]


def test_suggest_long_hole_lists_events_instead_of_picking_one():
    g = _gap(0, 0, 16, 0)
    s = cov.suggest(g, [_ev(9, 0, 10, 0, "Standup"), _ev(13, 0, 14, 0, "Offsite", calendar="m5x2 Cal")])
    assert s["fill"].startswith("multiple:") and "Standup" in s["fill"] and "Offsite @m5x2" in s["fill"]
    assert s["confidence"] == "low"


def test_suggest_daytime_gap_with_no_signal_is_a_question_mark():
    g = _gap(13, 29, 14, 0, prev=_e(13, 0, 13, 29, "a", "xk87"), nxt=_e(14, 0, 14, 30, "b", "hcb"))
    assert cov.suggest(g, [])["fill"] == "?"


def test_audit_flags_days_under_floor_and_only_fetches_calendar_for_them():
    first, last = dt.date(2026, 8, 1), dt.date(2026, 8, 2)
    full = [_e(0, 0, 23, 59, "all", day=first)]
    short = [_e(0, 0, 12, 0, "a", "xk87", day=last), _e(12, 30, 23, 59, "b", "hcb", day=last)]
    fetched = []
    a = cov.audit(first, last, full + short, event_fetcher=lambda d: fetched.append(d) or [])
    assert [d["short"] for d in a["days"]] == [False, True]
    assert a["n_short"] == 1 and a["uncovered_min"] == 30
    assert fetched == [last]
    assert a["days"][1]["gaps"][0]["suggest"]["fill"] == "?"
    assert a["days"][1]["gaps"][0]["id"] == 1 and a["n_gaps"] == 1
    md = cov.render_md(a)
    assert "1 of 2 days have gaps" in md and "| 1 | Sun 8/2 | 12:00–12:30 |" in md


def test_seam_only_days_are_summarised_not_listed_and_numbering_is_sequential():
    first, last = dt.date(2026, 8, 1), dt.date(2026, 8, 3)
    seams = [_e(0, 0, 8, 0, "a", "x", day=first), _e(8, 2, 16, 0, "b", "y", day=first),
             _e(16, 3, 23, 59, "c", "z", day=first)]                # 5 min of 2-3 min seams → under floor? no: 5 < 15
    seams += [_e(0, 0, 6, 0, "s", "睡觉", day=last)] + [
        _e(6 + i, 4, 7 + i, 0, f"e{i}", "x", day=last) for i in range(17)] + [_e(23, 0, 23, 59, "n", "睡觉", day=last)]
    real = [_e(0, 0, 10, 0, "a", "x", day=first + dt.timedelta(days=1)),
            _e(10, 20, 20, 0, "b", "y", day=first + dt.timedelta(days=1)),
            _e(20, 30, 23, 59, "c", "z", day=first + dt.timedelta(days=1))]
    a = cov.audit(first, last, seams + real, event_fetcher=lambda d: [])
    by_date = {d["date"]: d for d in a["days"]}
    assert by_date["2026-08-03"]["seams_only"] is True and by_date["2026-08-03"]["gaps"] == []
    assert [g["id"] for g in by_date["2026-08-02"]["gaps"]] == [1, 2]
    md = cov.render_md(a)
    assert "seams only (fine, not listed): 8/3" in md
    assert "| 2 | Sun 8/2 | 20:00–20:30 |" in md


def test_parse_fill_spec_handles_plain_numbers_and_overrides():
    spec = cov.parse_fill_spec("1, 3,5=睡觉,7=family time @xk87, 9=@i9")
    assert spec == {1: (None, None), 3: (None, None), 5: ("睡觉", None),
                    7: ("family time", "xk87"), 9: (None, "i9")}


def test_fill_gaps_uses_saved_state_and_skips_unusable_rows(monkeypatch, tmp_path):
    state = tmp_path / "2n-last.json"
    state.write_text(json.dumps({"month": "2026-08", "gaps": [
        {"id": 1, "date": "2026-08-14", "start": "2026-08-14T14:55:00-07:00",
         "end": "2026-08-14T15:00:00-07:00", "minutes": 5, "fill": "hiring checkin", "code": "i9", "confidence": "med"},
        {"id": 2, "date": "2026-08-07", "start": "2026-08-07T13:29:00-07:00",
         "end": "2026-08-07T14:00:00-07:00", "minutes": 31, "fill": "?", "code": "", "confidence": "low"},
    ]}))
    monkeypatch.setattr(cov, "STATE_PATH", state)
    monkeypatch.setattr(cov, "_ensure_toggl_key", lambda: None)
    created = []
    import types, sys as _sys
    fake_api = types.SimpleNamespace(create_entry=lambda *a, **k: created.append((a, k)))
    fake_cfg = types.SimpleNamespace(PROJECT_MAP={"i9": 209635316, "xk87": 163129781})
    monkeypatch.setitem(_sys.modules, "mcp.toggl_server.toggl_api", fake_api)
    monkeypatch.setitem(_sys.modules, "mcp.toggl_server.config", fake_cfg)
    rc = cov.fill_gaps("1,2,3")
    assert rc == 2  # 2 skipped (no suggestion; unknown id)
    assert len(created) == 1
    (desc, start, end, dur), kw = created[0]
    assert desc == "hiring checkin" and dur == 300 and kw["project_id"] == 209635316
    created.clear()
    assert cov.fill_gaps("2=lunch out @xk87") == 0
    assert created[0][0][0] == "lunch out" and created[0][1]["project_id"] == 163129781
