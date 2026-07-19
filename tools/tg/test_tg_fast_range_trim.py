"""tg-fast.py's "<desc> <start>-<end>" range creation must trim/split/delete
any existing entry that overlaps the new range first, same as did-fast.py's
time-range /did items and janus.py's entry-edit-to-a-new-time path (user
request 2026-07-19: editing/creating time entries must stay MECE — "if
there's a different time entry, shorten it to make room, or delete the old
one if full overlap").

The user was explicitly surprised tg-fast DIDN'T already do this ("I thought
'did' or 'tg' would do that") — cmd_create_range previously had zero overlap
handling at all (unlike cmd_backdated, which already trims via the older,
point-based _trim_overlapping). This wires the same shared toggl_api.trim_range
(mcp/toggl_server/toggl_api.py) that did-fast.py and janus.py use, rather than
reimplementing a third copy.
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).parent
SRC = HERE / "tg-fast.py"
TZ = ZoneInfo("America/Los_Angeles")


def _load():
    spec = importlib.util.spec_from_file_location("tg_fast_trim", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_fast_trim"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


def test_cmd_create_range_calls_trim_range_before_creating(mod, monkeypatch):
    calls = []

    class _FakeToggl:
        def trim_range(self, start_dt, end_dt, exclude_ids=None):
            calls.append((start_dt, end_dt))
            return ["Trimmed: old thing 09:00-09:29"]

    monkeypatch.setattr(mod, "_toggl_api", lambda: _FakeToggl())
    monkeypatch.setattr(mod, "_run_cli", lambda *a: "Created: new thing 09:30-10:00 [id:1]")

    out = mod.cmd_create_range("new thing", "i9", [], "09:30", "10:00")

    today = datetime.now(TZ).date()
    expected_start = datetime(today.year, today.month, today.day, 9, 30, tzinfo=TZ)
    expected_end = datetime(today.year, today.month, today.day, 10, 0, tzinfo=TZ)
    assert calls == [(expected_start, expected_end)]
    assert "Trimmed: old thing 09:00-09:29" in out
    assert "Created: new thing 09:30-10:00 [id:1]" in out


def test_cmd_create_range_handles_overnight_wrap(mod, monkeypatch):
    """An end time earlier than start (e.g. 23:30-00:15) spans midnight —
    the trim window must roll the end to the next day, not compute a
    negative/zero-width range."""
    calls = []

    class _FakeToggl:
        def trim_range(self, start_dt, end_dt, exclude_ids=None):
            calls.append((start_dt, end_dt))
            return []

    monkeypatch.setattr(mod, "_toggl_api", lambda: _FakeToggl())
    monkeypatch.setattr(mod, "_run_cli", lambda *a: "Created (split): ...")

    mod.cmd_create_range("睡觉", "睡觉", [], "23:30", "00:15")
    (start_dt, end_dt), = calls
    assert end_dt > start_dt
    assert (end_dt - start_dt) == timedelta(minutes=45)


def test_cmd_create_range_trim_failure_does_not_block_creation(mod, monkeypatch):
    class _FakeToggl:
        def trim_range(self, start_dt, end_dt, exclude_ids=None):
            raise RuntimeError("Toggl API GET -> 500")

    monkeypatch.setattr(mod, "_toggl_api", lambda: _FakeToggl())
    monkeypatch.setattr(mod, "_run_cli", lambda *a: "Created: new thing 09:30-10:00 [id:1]")

    out = mod.cmd_create_range("new thing", "i9", [], "09:30", "10:00")
    assert "Created: new thing 09:30-10:00 [id:1]" in out
    assert "trim failed" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
