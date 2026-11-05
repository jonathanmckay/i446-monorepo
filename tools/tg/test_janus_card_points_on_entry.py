"""Regression (2026-11-05): "1 hcb shows in time, but should be linked to the
1hcb task and the number of points that it got". The card closed before its
Toggl entries existed, so only name and time can link them: one completion
shows on one row, the entry it finished in, as on mobile (lib/janus_done.py)."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_janus_block_view import _load_tui  # noqa: E402


@pytest.fixture
def mod(tmp_path, monkeypatch):
    m = _load_tui()
    done = tmp_path / "completed-today.json"
    today = dt.datetime.now(m._tz()).date().isoformat()
    base = m.view_now().replace(hour=8, minute=30, second=0, microsecond=0)
    at = (base + dt.timedelta(minutes=10)).timestamp()      # 08:40, inside the first entry
    done.write_text(json.dumps({"date": today, "names": ["1 hcb"], "points": {"1 hcb": 15},
                                "done_at": {"1 hcb": at}}))
    monkeypatch.setattr(m, "_COMPLETED_TODAY", done)
    monkeypatch.setattr(m, "_recorded_entry_pts", lambda d, ids: None)
    monkeypatch.setattr(m, "_points_adj_today", lambda: {})
    m.STATE.day_offset = 0
    m.STATE.habits_today = []
    m.STATE.entries = [
        {"id": 1, "desc": "1 hcb", "start_dt": base, "running": False,
         "end_dt": base + dt.timedelta(minutes=23)},
        {"id": 2, "desc": "1 hcb", "start_dt": base + dt.timedelta(hours=2), "running": False,
         "end_dt": base + dt.timedelta(hours=2, minutes=6)},
    ]
    return m


def test_card_points_land_on_the_entry_it_finished_in(mod):
    # JM: "it should be when I did it, 8:37", not the last same-name entry.
    assert mod._row_points("1 hcb", 23, [1]) == (True, 15)
    assert mod._row_points("1 hcb", 6, [2]) == (False, 0)


def test_merged_row_holding_that_entry_gets_the_points(mod):
    assert mod._row_points("1 hcb", 29, [1, 2]) == (True, 15)


def test_row_with_points_shows_minutes_then_points(mod):
    assert mod._entry_dur_display(23, "1 hcb", [1]) == "23m [15]"
    assert mod._entry_dur_display(6, "1 hcb", [2]) == "6m"
