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
