"""Regression (2026-11-03): backfill_jbm_archive.toggl_day asked Toggl for
bare YYYY-MM-DD dates (= UTC midnight), dropping every entry after 16:00 PST
/ 17:00 PDT from that day's backfill."""
import datetime as dt
import importlib.util
import urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("bjbm", HERE / "backfill_jbm_archive.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _q(day):
    return urllib.parse.parse_qs(urllib.parse.urlparse(m.toggl_day_url(day)).query)


def test_pst_day_uses_minus_eight():
    q = _q(dt.date(2026, 11, 2))
    assert q["start_date"] == ["2026-11-02T00:00:00-08:00"]
    assert q["end_date"] == ["2026-11-03T00:00:00-08:00"]


def test_pdt_day_uses_minus_seven():
    q = _q(dt.date(2026, 10, 15))
    assert q["start_date"] == ["2026-10-15T00:00:00-07:00"]
    assert q["end_date"] == ["2026-10-16T00:00:00-07:00"]


def test_dst_end_day_spans_25_hours():
    s, e = m.local_day_bounds(dt.date(2026, 11, 1))
    assert s.endswith("-07:00") and e.endswith("-08:00")
    span = dt.datetime.fromisoformat(e) - dt.datetime.fromisoformat(s)
    assert span == dt.timedelta(hours=25)


def test_evening_entry_falls_inside_its_local_day():
    s, e = map(dt.datetime.fromisoformat, m.local_day_bounds(dt.date(2026, 11, 2)))
    evening = dt.datetime(2026, 11, 3, 3, 30, tzinfo=dt.timezone.utc)  # 19:30 PST 11-02
    assert s <= evening < e
