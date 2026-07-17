#!/usr/bin/env python3
"""Regression test: did-fast's time-range Toggl entries must trim/split any
existing entry (completed or the currently-running one) that overlaps the new
range, instead of blindly creating on top of it.

Bug (2026-07-16): manually backfilling "asha" (09:30-10:00) then "asha prep"
(09:30-10:30) via /did double-counted both time AND points over the shared
09:30-10:00 window -- did-fast's Toggl-entry creation had no overlap handling
at all (unlike tg-fast.py's `_trim_overlapping`, which only ever handles a
single backdate INSTANT, not a full range with two-sided overlap).

_trim_toggl_range generalizes that: for each existing entry overlapping
[start_dt, end_dt), it keeps the non-overlapping remainder(s) and deletes the
original. The running entry is special-cased since it has no fixed end --
its "after" remainder is RESUMED as a new running entry, not trimmed to a stop.
"""
import importlib.util
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("did_fast_trim", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_trim"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def df():
    return _load()


class FakeTogglApi:
    """Records every mutation call instead of touching the network."""

    def __init__(self, entries):
        self._entries = entries
        self.created = []
        self.deleted = []
        self.resumed = []

    def get_entries(self, start_date=None, end_date=None):
        return self._entries

    def create_entry(self, description, start_iso, stop_iso, duration_sec,
                      project_id=None, tags=None):
        self.created.append((description, start_iso, stop_iso, project_id))
        return {"id": 999}

    def start_timer(self, description, project_id=None, tags=None, start_time=None):
        self.resumed.append((description, start_time, project_id))
        return {"id": 998}

    def delete_entry(self, entry_id):
        self.deleted.append(entry_id)
        return True


def _entry(id_, desc, start, stop=None, project_id=None, duration=None):
    return {
        "id": id_, "description": desc,
        "start": start.isoformat(),
        "stop": stop.isoformat() if stop else None,
        "duration": duration if duration is not None else (
            -1 if stop is None else int((stop - start).total_seconds())),
        "project_id": project_id,
        "tags": [],
    }


def _patch(monkeypatch, df, fake):
    monkeypatch.setattr(df, "_toggl_api", lambda: fake)


def test_no_overlap_is_a_no_op(df, monkeypatch):
    day = datetime(2026, 7, 17, tzinfo=TZ)
    existing = _entry(1, "morning standup", day.replace(hour=8), day.replace(hour=8, minute=30))
    fake = FakeTogglApi([existing])
    _patch(monkeypatch, df, fake)
    out = df._trim_toggl_range(day.replace(hour=9), day.replace(hour=10))
    assert out == []
    assert fake.deleted == []
    assert fake.created == []


def test_fully_contained_entry_is_deleted_outright(df, monkeypatch):
    day = datetime(2026, 7, 17, tzinfo=TZ)
    existing = _entry(1, "meetings", day.replace(hour=9, minute=30), day.replace(hour=10))
    fake = FakeTogglApi([existing])
    _patch(monkeypatch, df, fake)
    out = df._trim_toggl_range(day.replace(hour=9), day.replace(hour=10, minute=30))
    assert fake.deleted == [1]
    assert fake.created == []  # nothing survives on either side
    assert any("Trimmed" not in line for line in out) or out == []


def test_overlap_at_front_trims_and_keeps_pre_portion(df, monkeypatch):
    """New range starts mid-way through an existing completed entry: the
    portion BEFORE the new range survives, trimmed to end 1 minute early."""
    day = datetime(2026, 7, 17, tzinfo=TZ)
    existing = _entry(1, "asha", day.replace(hour=9, minute=30), day.replace(hour=10, minute=30),
                       project_id=42)
    fake = FakeTogglApi([existing])
    _patch(monkeypatch, df, fake)
    df._trim_toggl_range(day.replace(hour=10), day.replace(hour=10, minute=30))
    assert fake.deleted == [1]
    assert len(fake.created) == 1
    desc, start_iso, stop_iso, proj = fake.created[0]
    assert desc == "asha"
    assert proj == 42
    assert start_iso == day.replace(hour=9, minute=30).isoformat()
    assert stop_iso == day.replace(hour=9, minute=59).isoformat()


def test_overlap_at_back_trims_and_keeps_post_portion(df, monkeypatch):
    """Existing completed entry extends PAST the new range's end: the tail
    survives, trimmed to start 1 minute after the new range ends."""
    day = datetime(2026, 7, 17, tzinfo=TZ)
    existing = _entry(1, "deep work", day.replace(hour=9, minute=30), day.replace(hour=11))
    fake = FakeTogglApi([existing])
    _patch(monkeypatch, df, fake)
    df._trim_toggl_range(day.replace(hour=9, minute=30), day.replace(hour=10))
    assert fake.deleted == [1]
    assert len(fake.created) == 1
    desc, start_iso, stop_iso, proj = fake.created[0]
    assert start_iso == day.replace(hour=10, minute=1).isoformat()
    assert stop_iso == day.replace(hour=11).isoformat()


def test_entry_spanning_both_sides_is_split_in_two(df, monkeypatch):
    day = datetime(2026, 7, 17, tzinfo=TZ)
    existing = _entry(1, "work", day.replace(hour=9), day.replace(hour=12))
    fake = FakeTogglApi([existing])
    _patch(monkeypatch, df, fake)
    df._trim_toggl_range(day.replace(hour=10), day.replace(hour=10, minute=30))
    assert fake.deleted == [1]
    assert len(fake.created) == 2
    fronts = [c for c in fake.created if c[1] == day.replace(hour=9).isoformat()]
    backs = [c for c in fake.created if c[2] == day.replace(hour=12).isoformat()]
    assert len(fronts) == 1 and fronts[0][2] == day.replace(hour=9, minute=59).isoformat()
    assert len(backs) == 1 and backs[0][1] == day.replace(hour=10, minute=31).isoformat()


def test_running_entry_overlapping_the_front_is_resumed_after(df, monkeypatch):
    """The forgot-to-stop-the-timer case: a still-running generic entry
    started this morning and overlaps the new range. Its pre-portion is
    trimmed like any completed entry, but its "after" portion can't be
    trimmed to a stop -- it's RESUMED as a new running entry."""
    day = datetime(2026, 7, 17, tzinfo=TZ)
    running = _entry(1, "meetings", day.replace(hour=9), stop=None, project_id=7)
    fake = FakeTogglApi([running])
    _patch(monkeypatch, df, fake)

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return day.replace(hour=10, minute=45)

    monkeypatch.setattr(df, "datetime", _FrozenNow)
    df._trim_toggl_range(day.replace(hour=9, minute=30), day.replace(hour=10))
    assert fake.deleted == [1]
    assert len(fake.created) == 1  # pre-portion, trimmed
    assert fake.created[0][1] == day.replace(hour=9).isoformat()
    assert fake.created[0][2] == day.replace(hour=9, minute=29).isoformat()
    assert len(fake.resumed) == 1  # post-portion, resumed (not trimmed to a stop)
    desc, start_time, proj = fake.resumed[0]
    assert desc == "meetings" and proj == 7
    assert start_time == day.replace(hour=10, minute=1).isoformat()


def test_create_toggl_calls_trim_before_creating():
    """Structural: _create_toggl (the actual Step 5.5/6 write path used by
    every time-range /did item) must call _trim_toggl_range, not just the new
    helper existing in isolation -- else the fix never fires in practice."""
    src = (HERE / "did-fast.py").read_text()
    i_def = src.index("def _create_toggl(args):")
    body = src[i_def:src.index("\n        with ThreadPoolExecutor", i_def)]
    assert "_trim_toggl_range(" in body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
