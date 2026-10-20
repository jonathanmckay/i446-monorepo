#!/usr/bin/env python3
"""Feature tests (2026-09-27): dtd mobile delay picker gains a freeform
today-only delay input and a recurring-only 'skip to next occurrence' action,
plus the server self-restarts when its own source changes (staleness fix).

- Freeform delay (_parse_freeform_delay): accepts a clock time later today
  (HHMM / HH:MM) or a relative duration (90m / 2h), returns an epoch float;
  rejects past times, malformed input, and anything beyond today.
- Skip-to-next (skip_recurrence): shells defer-fast in skip mode; recurring
  only, guarded on the returned `recurring` flag.
- Staleness: a _watch_own_source loop exists that exits on source-mtime change
  (launchd KeepAlive relaunches fresh) — asserted structurally.
"""
from __future__ import annotations

import ast
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402


class _FixedDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 27, 14, 0)  # 14:00 today


def _patch_now(monkeypatch):
    monkeypatch.setattr(dtd._dt, "datetime", _FixedDatetime)


# ── freeform parser ─────────────────────────────────────────────────────────
def test_freeform_relative_minutes(monkeypatch):
    _patch_now(monkeypatch)
    epoch, err = dtd._parse_freeform_delay("90m")
    assert err is None
    assert epoch == _FixedDatetime.now().timestamp() + 90 * 60
    assert isinstance(epoch, float)


def test_freeform_relative_hours(monkeypatch):
    _patch_now(monkeypatch)
    epoch, err = dtd._parse_freeform_delay("2h")
    assert err is None
    assert epoch == _FixedDatetime.now().timestamp() + 120 * 60


def test_freeform_clock_time_today(monkeypatch):
    _patch_now(monkeypatch)
    for txt in ("1830", "18:30"):
        epoch, err = dtd._parse_freeform_delay(txt)
        assert err is None, txt
        got = dt.datetime.fromtimestamp(epoch)
        assert (got.hour, got.minute) == (18, 30), txt


def test_freeform_rejects_past_time_today(monkeypatch):
    _patch_now(monkeypatch)  # now = 14:00
    epoch, err = dtd._parse_freeform_delay("09:00")
    assert epoch is None and "passed" in err


def test_freeform_rejects_out_of_range_and_garbage(monkeypatch):
    _patch_now(monkeypatch)
    for bad in ("2599", "25:00", "abc", "", "12:60"):
        epoch, err = dtd._parse_freeform_delay(bad)
        assert epoch is None and err, bad


def test_freeform_rejects_too_far(monkeypatch):
    _patch_now(monkeypatch)
    epoch, err = dtd._parse_freeform_delay("2000h")
    assert epoch is None and err


# ── skip-recurrence (defer-fast skip mode) ──────────────────────────────────
class _Proc:
    def __init__(self, stdout, rc=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, rc, stderr


def test_skip_recurrence_calls_defer_fast_skip_mode(monkeypatch):
    calls = {}

    def fake_run(args, **kw):
        calls["args"] = args
        return _Proc('{"recurring": true, "next_recurrence": "2026-09-28"}')

    monkeypatch.setattr(dtd.subprocess, "run", fake_run)
    out = dtd.skip_recurrence("ABC123")
    # must invoke defer-fast by id in skip mode ("0")
    assert "--id" in calls["args"] and "ABC123" in calls["args"] and "0" in calls["args"]
    assert out == {"ok": True, "next": "2026-09-28"}


def test_skip_recurrence_rejects_non_recurring(monkeypatch):
    monkeypatch.setattr(dtd.subprocess, "run",
                        lambda args, **kw: _Proc('{"recurring": false, "target_date": "2026-09-28"}'))
    out = dtd.skip_recurrence("XYZ")
    assert out["ok"] is False and "recurring" in out["error"]


# ── staleness self-restart (structural) ─────────────────────────────────────
def test_watch_own_source_exists_and_exits_on_change():
    src = (Path(__file__).resolve().parent / "dtd.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_watch_own_source"), None)
    assert fn is not None, "no _watch_own_source staleness guard"
    names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "getmtime" in names, "must compare source mtime"
    # must hard-exit so launchd KeepAlive relaunches a fresh process
    assert any(isinstance(n, ast.Attribute) and n.attr == "_exit" for n in ast.walk(fn)), \
        "must os._exit on change"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
