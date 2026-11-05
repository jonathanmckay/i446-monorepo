"""^G "these points were wrong" (JM 2026-10-28): the difference is appended
as a signed did-fast 分 log in the entry's own block, and the row shows the
corrected total."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import janus  # noqa: E402


def _t(h, m):
    return dt.datetime(2026, 10, 28, h, m, tzinfo=janus._tz())


def test_adjust_cmd_is_signed_noted_and_in_the_entrys_block():
    assert janus._points_adjust_cmd("write memo [30]", "i9", -12, _t(8, 30), _t(9, 10), False) \
        == "-12 write memo adj @i9 巳"
    assert janus._points_adjust_cmd("XCORE", "i9", 20, _t(9, 18), _t(12, 41), False) \
        == "+20 XCORE adj @i9 未"   # ended in 未 (JM 2026-11-01), not the 午 majority


def test_adjust_cmd_on_a_past_day_carries_its_block_and_date():
    # JM 2026-11-02: did-fast credits a past day's block too; the glyph goes
    # before the trailing M/D, which did-fast strips first.
    assert janus._points_adjust_cmd("hiit", "hcb", 5, _t(12, 42), _t(13, 0), True) \
        == "+5 hiit adj @hcb 未 10/28"


def _view(monkeypatch, tmp_path, day, offset):
    monkeypatch.setattr(janus, "_ENTRY_PTS_DIR", tmp_path)
    monkeypatch.setattr(janus.STATE, "day_offset", offset)
    monkeypatch.setattr(janus, "view_now",
                        lambda: dt.datetime(day.year, day.month, day.day, 23, 59, tzinfo=janus._tz()))


def test_past_day_row_shows_the_points_janus_logged(tmp_path, monkeypatch):
    """JM 2026-11-02: a past-day entry logged from janus shows its 分 in the
    right column, not its minutes."""
    day = dt.date(2026, 11, 1)
    _view(monkeypatch, tmp_path, day, -1)
    assert janus._entry_dur_display(120, "interview prep", [4608630595]) == janus.fmt_dur(120)
    janus._entry_pts_add(day, {"4608630595": 420})
    assert janus._entry_dur_display(120, "interview prep", [4608630595]) == "120m [420]"
    # a same-name entry on that day that was not logged keeps its minutes
    assert janus._entry_dur_display(90, "interview prep", [4607267560]) == janus.fmt_dur(90)


def test_entry_points_record_replaces_and_adjust_adds(tmp_path, monkeypatch):
    day = dt.date(2026, 11, 1)
    _view(monkeypatch, tmp_path, day, -1)
    janus._entry_pts_add(day, {"7": 100})
    janus._entry_pts_add(day, {"7": 120})
    janus._entry_pts_add(day, {"7": -20}, add=True)
    assert janus._row_points("x", 30, [7]) == (True, 100)
    # a merged row sums its entries
    janus._entry_pts_add(day, {"8": 5})
    assert janus._row_points("x", 30, [7, 8]) == (True, 105)


def test_row_points_include_adjustments(tmp_path, monkeypatch):
    ct = tmp_path / "completed-today.json"
    today = dt.datetime.now(janus._tz()).date().isoformat()
    ct.write_text(json.dumps({"date": today, "names": ["write memo"], "points": {"write memo": 30}}))
    monkeypatch.setattr(janus, "_COMPLETED_TODAY", ct)
    monkeypatch.setattr(janus, "_POINTS_ADJ", tmp_path / "adj.json")
    assert janus._row_points("write memo", 40, [111]) == (True, 30)
    janus._points_adj_add([111], -12)
    assert janus._row_points("write memo", 40, [111]) == (True, 18)
    assert janus._row_points("write memo", 40, [222]) == (True, 30)  # other entry untouched


def test_stale_zero_record_on_a_per_minute_habit_shows_its_minutes(tmp_path, monkeypatch):
    """Bug 2026-11-03: a 69m o314 row logged from janus showed [0]. did-fast
    reports no 0分 points for a sheet-credited 0n habit, so janus recorded
    {entry: 0}; the record outranked everything after did_points was fixed.
    A 0 record on a per-minute 0n habit is that stale value, not a real 0."""
    day = dt.date(2026, 11, 1)
    _view(monkeypatch, tmp_path, day, -1)
    janus._entry_pts_add(day, {"4609785250": 0})
    assert janus._entry_dur_display(69, "o314", [4609785250]) == "69m [69]"
    # a real record still wins, and a 0 on an ordinary task stays 0
    janus._entry_pts_add(day, {"4609785250": 104})
    assert janus._entry_dur_display(69, "o314", [4609785250]) == "69m [104]"
    janus._entry_pts_add(day, {"5": 0})
    assert janus._entry_dur_display(20, "read memo", [5]) == "20m [0]"


def test_per_minute_habit_row_is_priced_at_the_logged_minutes(tmp_path, monkeypatch):
    """Bug 2026-11-04: an 80m hiit Toggl entry showed [160] (80 x 2) while
    JM logged 24 minutes of actual HIIT, so Neon credited 48. A row can't be
    worth more minutes than the habit's 0n cell holds."""
    import json as _json
    ct = tmp_path / "completed-today.json"
    ct.write_text(_json.dumps({"date": dt.datetime.now(janus._tz()).date().isoformat(),
                               "names": ["hiit"], "points": {}}))
    monkeypatch.setattr(janus, "_COMPLETED_TODAY", ct)
    monkeypatch.setattr(janus.STATE, "habits_today", [("hiit", 24.0), ("o314", 70.0)])
    assert janus._points_recorded_today("hiit", 80) == (True, 48)
    # a session shorter than the day's logged total keeps its own minutes
    monkeypatch.setattr(janus.STATE, "habits_today", [("hiit", 95.0)])
    assert janus._points_recorded_today("hiit", 80) == (True, 160)
    # nothing logged in 0n yet (strip not loaded): fall back to the entry
    monkeypatch.setattr(janus.STATE, "habits_today", [])
    assert janus._points_recorded_today("hiit", 80) == (True, 160)
