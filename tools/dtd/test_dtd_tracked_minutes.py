#!/usr/bin/env python3
"""/api/summary carries `minutes` (2026-11-15) for terminal dtd's top line
("38 tasks ── dtd ── 27 done, 512m [765]"): minutes tracked today outside 睡觉,
read from janus's day cache, never from Toggl. A stale or missing cache gives
None so the line shows no number instead of an old one."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_tracked", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)

DAY = "2026-11-15"
TZ = dt.timezone(dt.timedelta(hours=-8))


def _at(h, m):
    return dt.datetime(2026, 11, 15, h, m, tzinfo=TZ)


def _entry(start, end, project=1, running=False):
    return {"start_dt": start.isoformat(), "end_dt": end.isoformat(),
            "project_id": project, "running": running, "desc": "x"}


def _cache(tmp_path, entries, age=0, now=None):
    p = tmp_path / "janus-day-entries.json"
    p.write_text(json.dumps({DAY: entries, "2026-11-14": [_entry(_at(1, 0), _at(9, 0))]}))
    now = now or _at(10, 0).timestamp()
    os.utime(p, (now - age, now - age))
    return p, now


def test_sums_the_day_outside_sleep(tmp_path):
    p, now = _cache(tmp_path, [
        _entry(_at(0, 0), _at(7, 18), project=dtd.SLEEP_PROJECT_ID),
        _entry(_at(7, 18), _at(7, 25)),
        _entry(_at(8, 0), _at(8, 30)),
    ])
    assert dtd.tracked_minutes(DAY, now, p) == 37


def test_running_entry_counts_up_to_now(tmp_path):
    p, now = _cache(tmp_path, [_entry(_at(9, 40), _at(9, 41), running=True)])
    assert dtd.tracked_minutes(DAY, now, p) == 20


def test_stale_missing_or_dayless_cache_is_none(tmp_path):
    p, now = _cache(tmp_path, [_entry(_at(8, 0), _at(8, 30))], age=dtd.TRACKED_MAX_AGE + 60)
    assert dtd.tracked_minutes(DAY, now, p) is None
    assert dtd.tracked_minutes(DAY, now, tmp_path / "nope.json") is None
    p, now = _cache(tmp_path, [])
    assert dtd.tracked_minutes("2026-11-16", now, p) is None
    assert dtd.tracked_minutes(DAY, now, p) == 0
