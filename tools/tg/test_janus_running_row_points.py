"""Regression (2026-11-23): "the points still are not attaching to the time
entry, or at least they don't render in Janus". A dtd split had recorded 100分
on the running timer's entry (lib/entry_points.py), but the running body row
printed bare minutes: it never read the per-entry record."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_janus_block_view import _load_tui  # noqa: E402


def _picks(mod):
    today = mod.view_now().replace(hour=0, minute=0, second=0, microsecond=0)
    base = {"style": "", "project_id": None, "tags": []}
    return [
        {**base, "start_dt": today.replace(hour=12, minute=5), "time_str": "12:05",
         "label": "hci", "dur_min": 40, "entry_ids": [1], "raw_desc": "hci"},
        {**base, "start_dt": today.replace(hour=12, minute=54), "time_str": "12:54",
         "label": "allergies", "dur_min": 9, "is_running": True,
         "entry_ids": [4631481286], "raw_desc": "allergies"},
    ]


def _row(mod, recorded):
    mod.STATE.events = []
    mod.STATE.day_offset = 0
    mod._recorded_entry_pts = lambda day, ids: recorded.get(str((ids or [None])[0]))
    mod._points_recorded_today = lambda *a, **k: (False, 0)
    mod._points_adj_today = lambda: {}
    text = "".join(t for _, t, *_ in mod._compact_block_lines("未", 12, _picks(mod), 0, ""))
    return next(l for l in text.split("\n") if "allergies" in l)


def test_running_row_shows_points_recorded_on_its_entry():
    mod = _load_tui()
    assert "9m [100]" in _row(mod, {"4631481286": 100})


def test_running_row_without_a_record_shows_minutes_only():
    mod = _load_tui()
    row = _row(mod, {})
    assert "9m" in row and "[" not in row


def test_running_row_ignores_a_finished_session_of_the_same_name():
    # completed-today is keyed by name: an earlier, closed "allergies" must not
    # put its 分 on the timer that is still going.
    mod = _load_tui()
    mod._recorded_entry_pts = lambda day, ids: None
    mod._points_recorded_today = lambda *a, **k: (True, 55)
    assert mod._running_dur_display(9, [4631481286]) == "9m"


def test_helper_reads_the_viewed_day(monkeypatch=None):
    mod = _load_tui()
    seen = []
    mod._recorded_entry_pts = lambda day, ids: seen.append(day) or 100
    assert mod._running_dur_display(9, [1]) == "9m [100]"
    assert seen == [mod.view_now().date()] and isinstance(seen[0], dt.date)
