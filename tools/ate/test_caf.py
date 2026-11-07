"""/caf (2026-11-01): caffeine points into hcbi's caf column on the day the
text names. The parser is exercised with a fixed "today"; the write is
exercised against a stub daemon client so nothing touches Excel.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import caf  # noqa: E402

SUN = dt.date(2026, 11, 1)   # a Sunday


@pytest.mark.parametrize("text, points, date", [
    ("-60", -60.0, SUN),
    ("+50", 50.0, SUN),
    ("120 yesterday", 120.0, dt.date(2026, 10, 31)),
    ("50 tomorrow", 50.0, dt.date(2026, 11, 2)),
    ("-40 10.30", -40.0, dt.date(2026, 10, 30)),
    ("-40 10/30", -40.0, dt.date(2026, 10, 30)),
    ("60 friday", 60.0, dt.date(2026, 10, 30)),
    ("60 sunday", 60.0, SUN),                       # today is that weekday
    ("60 last sunday", 60.0, dt.date(2026, 10, 25)),  # "last" never means today
    ("-120 3 days ago", -120.0, dt.date(2026, 10, 29)),
    ("2026-10-28 -35", -35.0, dt.date(2026, 10, 28)),
    ("caf 50 pts on thursday", 50.0, dt.date(2026, 10, 29)),
    ("-60 12.25", -60.0, dt.date(2025, 12, 25)),    # not happened yet this year → last year
    # date-first (2026-11-02: "10.30 -60" read 10.30 as the points)
    ("10.30 -60", -60.0, dt.date(2026, 10, 30)),
    ("10/30 -60", -60.0, dt.date(2026, 10, 30)),
    ("10.30 120", 120.0, dt.date(2026, 10, 30)),
    ("10.30 +50", 50.0, dt.date(2026, 10, 30)),
])
def test_parse_points_and_date(text, points, date):
    assert caf.parse(text, SUN) == {"points": points, "date": date}


def test_parse_rejects_no_number_and_unknown_phrase():
    with pytest.raises(ValueError):
        caf.parse("yesterday", SUN)
    with pytest.raises(ValueError):
        caf.parse("-60 someday", SUN)


class _Excel:
    def __init__(self, before=""):
        self.calls = []; self.before = before

    def batch_write(self, sheet, writes, *, date=None, row=None, src=None, fallback=True):
        self.calls.append(dict(sheet=sheet, writes=writes, date=date, src=src))
        return {"ok": True, "row": 307, "results": [{"ok": True, "col": writes[0]["col"],
                                                     "before_value": self.before, "value": writes[0]["value"]}]}


class _Cols:
    @staticmethod
    def col(sheet, header):
        assert (sheet, header) == ("hcbi", "caf")
        return "W"


def test_write_sets_the_caf_column_on_the_named_day():
    ex = _Excel()
    out = caf.write("-60 yesterday", SUN, excel=ex, cols=_Cols)
    assert ex.calls == [dict(sheet="hcbi", writes=[{"col": "W", "value": -60, "mode": "set"}],
                             date="10/31", src="caf -60 2026-10-31")]
    assert out == {"ok": True, "points": -60, "date": "2026-10-31", "col": "W", "row": 307, "before": None}
    assert caf.fmt(out) == "caf → -60 (2026-10-31, hcbi W307)"


def test_write_reports_the_value_it_replaced():
    out = caf.write("120", SUN, excel=_Excel(before="-60.0"), cols=_Cols)
    assert out["before"] == "-60.0"
    assert caf.fmt(out) == "caf → 120 (2026-11-01, hcbi W307, was -60.0)"


def test_write_surfaces_daemon_errors():
    class _Down(_Excel):
        def batch_write(self, *a, **k):
            return {"ok": False, "error": "date_not_found"}
    out = caf.write("-60", SUN, excel=_Down(), cols=_Cols)
    assert out == {"ok": False, "error": "date_not_found", "date": "2026-11-01"}
    assert caf.fmt(out).startswith("ERROR: date_not_found")


def test_cli_parse_writes_nothing():
    r = subprocess.run([sys.executable, str(HERE / "caf.py"), "--parse", "-60", "yesterday"],
                       capture_output=True, text=True)
    j = json.loads(r.stdout)
    assert r.returncode == 0 and j["ok"] and j["points"] == -60.0
    assert j["date"] == (dt.date.fromisoformat(j["date"])).isoformat()
    r = subprocess.run([sys.executable, str(HERE / "caf.py"), "--parse", "yesterday"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and not json.loads(r.stdout)["ok"]
