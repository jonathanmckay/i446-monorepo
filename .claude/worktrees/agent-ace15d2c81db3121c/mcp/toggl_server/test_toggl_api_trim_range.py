"""trim_range: shared overlap-cleanup for any caller that creates or moves a
definite Toggl time range.

Originally did-fast-only (`_trim_toggl_range`, added 2026-07-16 after
backfilling "asha" then "asha prep" over the same half hour double-counted
both time and points -- did-fast's time-range entry creation had no overlap
handling at all). Promoted into toggl_api once janus.py's entry-edit-to-a-
new-time feature needed the identical logic (2026-07-19: "if I edit ... time
entries ... MECE -- shorten [an overlapping entry], or delete ... if full
overlap") rather than a third copy of it (tg-fast.py's typed range-creation
path is the third caller).
"""
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

MCP_DIR = Path(__file__).resolve().parents[1]  # .../i446-monorepo/mcp
sys.path.insert(0, str(MCP_DIR))
from toggl_server import toggl_api  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")


class _Recorder:
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


def _patch(monkeypatch, rec):
    monkeypatch.setattr(toggl_api, "get_entries", rec.get_entries)
    monkeypatch.setattr(toggl_api, "create_entry", rec.create_entry)
    monkeypatch.setattr(toggl_api, "start_timer", rec.start_timer)
    monkeypatch.setattr(toggl_api, "delete_entry", rec.delete_entry)


def test_no_overlap_is_a_no_op(monkeypatch):
    day = datetime(2026, 7, 19, tzinfo=TZ)
    existing = _entry(1, "morning standup", day.replace(hour=8), day.replace(hour=8, minute=30))
    rec = _Recorder([existing])
    _patch(monkeypatch, rec)
    out = toggl_api.trim_range(day.replace(hour=9), day.replace(hour=10))
    assert out == []
    assert rec.deleted == []
    assert rec.created == []


def test_fully_contained_entry_is_deleted_outright(monkeypatch):
    day = datetime(2026, 7, 19, tzinfo=TZ)
    existing = _entry(1, "meetings", day.replace(hour=9, minute=30), day.replace(hour=10))
    rec = _Recorder([existing])
    _patch(monkeypatch, rec)
    toggl_api.trim_range(day.replace(hour=9), day.replace(hour=10, minute=30))
    assert rec.deleted == [1]
    assert rec.created == []


def test_overlap_at_front_trims_and_keeps_pre_portion(monkeypatch):
    day = datetime(2026, 7, 19, tzinfo=TZ)
    existing = _entry(1, "asha", day.replace(hour=9, minute=30), day.replace(hour=10, minute=30),
                       project_id=42)
    rec = _Recorder([existing])
    _patch(monkeypatch, rec)
    toggl_api.trim_range(day.replace(hour=10), day.replace(hour=10, minute=30))
    assert rec.deleted == [1]
    desc, start_iso, stop_iso, proj = rec.created[0]
    assert desc == "asha" and proj == 42
    assert start_iso == day.replace(hour=9, minute=30).isoformat()
    assert stop_iso == day.replace(hour=9, minute=59).isoformat()


def test_overlap_at_back_trims_and_keeps_post_portion(monkeypatch):
    day = datetime(2026, 7, 19, tzinfo=TZ)
    existing = _entry(1, "deep work", day.replace(hour=9, minute=30), day.replace(hour=11))
    rec = _Recorder([existing])
    _patch(monkeypatch, rec)
    toggl_api.trim_range(day.replace(hour=9, minute=30), day.replace(hour=10))
    assert rec.deleted == [1]
    desc, start_iso, stop_iso, proj = rec.created[0]
    assert start_iso == day.replace(hour=10, minute=1).isoformat()
    assert stop_iso == day.replace(hour=11).isoformat()


def test_entry_spanning_both_sides_is_split_in_two(monkeypatch):
    day = datetime(2026, 7, 19, tzinfo=TZ)
    existing = _entry(1, "work", day.replace(hour=9), day.replace(hour=12))
    rec = _Recorder([existing])
    _patch(monkeypatch, rec)
    toggl_api.trim_range(day.replace(hour=10), day.replace(hour=10, minute=30))
    assert rec.deleted == [1]
    assert len(rec.created) == 2
    fronts = [c for c in rec.created if c[1] == day.replace(hour=9).isoformat()]
    backs = [c for c in rec.created if c[2] == day.replace(hour=12).isoformat()]
    assert len(fronts) == 1 and fronts[0][2] == day.replace(hour=9, minute=59).isoformat()
    assert len(backs) == 1 and backs[0][1] == day.replace(hour=10, minute=31).isoformat()


def test_running_entry_overlapping_the_front_is_resumed_after(monkeypatch):
    day = datetime(2026, 7, 19, tzinfo=TZ)
    running = _entry(1, "meetings", day.replace(hour=9), stop=None, project_id=7)
    rec = _Recorder([running])
    _patch(monkeypatch, rec)

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return day.replace(hour=10, minute=45)

    monkeypatch.setattr(toggl_api, "datetime", _FrozenNow)
    toggl_api.trim_range(day.replace(hour=9, minute=30), day.replace(hour=10))
    assert rec.deleted == [1]
    assert len(rec.created) == 1
    assert rec.created[0][1] == day.replace(hour=9).isoformat()
    assert rec.created[0][2] == day.replace(hour=9, minute=29).isoformat()
    assert len(rec.resumed) == 1
    desc, start_time, proj = rec.resumed[0]
    assert desc == "meetings" and proj == 7
    assert start_time == day.replace(hour=10, minute=1).isoformat()


def test_excluded_id_is_never_trimmed(monkeypatch):
    """The entry being RETIMED (janus's edit path) must not try to trim
    itself out from under its own edit."""
    day = datetime(2026, 7, 19, tzinfo=TZ)
    existing = _entry(1, "carolina 1|1", day.replace(hour=9), day.replace(hour=10))
    rec = _Recorder([existing])
    _patch(monkeypatch, rec)
    out = toggl_api.trim_range(day.replace(hour=9, minute=30), day.replace(hour=10, minute=30),
                                exclude_ids={1})
    assert out == []
    assert rec.deleted == []
    assert rec.created == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
