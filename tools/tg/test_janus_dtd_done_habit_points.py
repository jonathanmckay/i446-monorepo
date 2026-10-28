"""JM 2026-10-26: a 0₦ habit (0g) closed in dtd shows its 分 in janus, not
minutes. completed-today.json lists the name without points (Excel credits
habits), so _points_recorded_today falls back to the habit's row-2 weight."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def test_habit_closed_in_dtd_reports_weight_points(tmp_path, monkeypatch):
    import janus
    import neon.rates as nr
    ct = tmp_path / "completed-today.json"
    ct.write_text(json.dumps({"date": dt.datetime.now(janus._tz()).date().isoformat(),
                              "names": ["0g", "coffee"], "points": {}}))
    monkeypatch.setattr(janus, "_COMPLETED_TODAY", ct)
    monkeypatch.setattr(nr, "zero_n_done_points", lambda h: 17 if h == "0g" else None)
    assert janus._points_recorded_today("0g") == (True, 17)
    assert janus._points_recorded_today("coffee") == (False, 0)


def test_multiplied_habit_row_shows_rate_times_minutes_not_row2_weight(tmp_path, monkeypatch):
    """Bug 2026-10-28: an 18m hiit row showed [7] (hiit's row-2 weight)
    instead of [36] (hcbi!Y credits 2 × the minutes)."""
    import janus
    import neon.rates as nr
    ct = tmp_path / "completed-today.json"
    ct.write_text(json.dumps({"date": dt.datetime.now(janus._tz()).date().isoformat(),
                              "names": ["hiit", "0g"], "points": {}}))
    monkeypatch.setattr(janus, "_COMPLETED_TODAY", ct)
    monkeypatch.setattr(nr, "zero_n_done_points", lambda h: {"hiit": 7, "0g": 17}.get(h))
    monkeypatch.setattr(janus.STATE, "day_offset", 0)
    assert janus._points_recorded_today("hiit", 18) == (True, 36)
    assert janus._entry_dur_display(18, "hiit") == "[36]"
    # plain habits keep their row-2 weight regardless of minutes
    assert janus._points_recorded_today("0g", 2) == (True, 17)
