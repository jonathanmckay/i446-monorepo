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
        == "+20 XCORE adj @i9 午"


def test_adjust_cmd_on_a_past_day_carries_the_date_not_a_block():
    assert janus._points_adjust_cmd("hiit", "hcb", 5, _t(12, 42), _t(13, 0), True) \
        == "+5 hiit adj @hcb 10/28"


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
