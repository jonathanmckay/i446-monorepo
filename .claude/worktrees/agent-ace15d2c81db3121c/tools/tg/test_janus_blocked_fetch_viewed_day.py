"""User report 2026-10-06: "time entries from today seem to be from yesterday
rather than refreshed", then after a restart: "now it's just showing calendar
from today and not toggl entries".

Toggl's shared 402 cooldown kept re-tripping across midnight. fetch_today()
returns early while blocked, so the running janus kept 10/5's entries and drew
them at their clock times on 10/6; a fresh janus started empty and said
"Toggl unconfirmed" even though a confirmed 10/6 day cache existed. The
day-cache fallback only ran on a FAILED fetch, never on a SKIPPED one.
"""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load(tmp_path):
    spec = importlib.util.spec_from_file_location("janus_blocked_day", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_blocked_day"] = mod
    spec.loader.exec_module(mod)
    mod.DAY_CACHE_PATH = tmp_path / "day-cache.json"
    mod._toggl_blocked = lambda: True
    mod.STATE.day_offset = 0
    return mod


def _entry(day, hh, desc):
    st = dt.datetime.combine(day, dt.time(hh, 0)).astimezone()
    return {"start_dt": st, "end_dt": st + dt.timedelta(minutes=15), "desc": desc,
            "project_id": None, "running": False, "id": hh, "tags": []}


def test_blocked_fetch_swaps_yesterdays_entries_for_todays_cache(tmp_path):
    mod = _load(tmp_path)
    today = mod.view_now().date()
    yday = today - dt.timedelta(days=1)
    mod._write_day_cache(today.isoformat(), [_entry(today, 6, "vibing")])
    mod.STATE.entries = [_entry(yday, 5, "economist")]  # loaded before midnight
    mod.STATE.entries_day = yday
    mod.STATE.entries_known = True
    mod.fetch_today()
    assert [e["desc"] for e in mod.STATE.entries] == ["vibing"]
    assert mod.STATE.entries_known is True
    assert mod.STATE.entries_day == today


def test_blocked_fetch_at_cold_start_uses_todays_cache(tmp_path):
    mod = _load(tmp_path)
    today = mod.view_now().date()
    mod._write_day_cache(today.isoformat(), [_entry(today, 7, "get ready")])
    mod.STATE.entries, mod.STATE.entries_day, mod.STATE.entries_known = [], None, False
    mod.fetch_today()
    assert [e["desc"] for e in mod.STATE.entries] == ["get ready"]
    assert mod.STATE.entries_known is True


def test_blocked_fetch_without_cache_clears_other_days_entries(tmp_path):
    mod = _load(tmp_path)
    yday = mod.view_now().date() - dt.timedelta(days=1)
    mod.DAY_CACHE_PATH.write_text(json.dumps({}))
    mod.STATE.entries = [_entry(yday, 5, "economist")]
    mod.STATE.entries_day = yday
    mod.STATE.entries_known = True
    mod.fetch_today()
    assert mod.STATE.entries == []
    assert mod.STATE.entries_known is False
