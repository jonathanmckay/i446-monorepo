#!/usr/bin/env python3
"""Feature: swipe-left action menu in dtd web (2026-08-11) — delay a day,
delay to the next block, start. Replaces the earlier swipe-left-instant-
start gesture with a reveal panel; this file covers the two new actions
(defer_task / snooze_to_next_block) and their routes. start_timer/api_start
are unchanged and stay covered by test_dtd_swipe_start.py.

defer_task mirrors terminal dtd's ctrl-d exactly by shelling out to
defer-fast.py --id <id> — no reimplementation of its recurring/non-recurring
branching. snooze_to_next_block writes the exact same
~/.local/state/jm/dtd-block-snooze.json shape _snoozed_ids() already reads,
scoped to just the next block (terminal's ctrl-v offers a full picker; the
web menu is three flat buttons, not a picker-within-a-picker — see the
feature plan).
"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_defer_task_shells_out_to_defer_fast_with_id_only(monkeypatch):
    calls = []
    def fake_run(args, **kw):
        calls.append(args)
        return _Proc(stdout='{"task": "x", "target_date": "2026-08-12", "recurring": false}')
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    result = dtd.defer_task("abc123")

    assert result == {"ok": True, "target_date": "2026-08-12"}
    assert calls == [["/usr/bin/python3", str(dtd.DEFER_FAST), "--id", "abc123"]], \
        "no extra args — defer-fast.py's own default is today+1 day, 2 claimed points"


def test_defer_task_surfaces_failure(monkeypatch):
    monkeypatch.setattr(dtd.subprocess, "run",
                        lambda args, **kw: _Proc(returncode=1, stderr="task not found"))
    result = dtd.defer_task("missing")
    assert result["ok"] is False
    assert "task not found" in result["error"]


def test_snooze_to_next_block_writes_expected_hour(monkeypatch, tmp_path):
    snooze_file = tmp_path / "dtd-block-snooze.json"
    monkeypatch.setattr(dtd, "SNOOZE_FILE", snooze_file)

    class _FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 9, 30)  # inside 巳 (08:00-09:59)

    monkeypatch.setattr(dtd._dt, "datetime", _FixedDatetime)

    result = dtd.snooze_to_next_block("t1")

    assert result == {"ok": True, "hour": 10}  # 午 starts at 10
    written = json.loads(snooze_file.read_text())
    assert written["snoozes"]["t1"] == 10
    assert written["date"] == dt.date.today().isoformat()


def test_snooze_to_next_block_preserves_other_ids(monkeypatch, tmp_path):
    snooze_file = tmp_path / "dtd-block-snooze.json"
    today = dt.date.today().isoformat()
    snooze_file.write_text(json.dumps({"date": today, "snoozes": {"existing": 16}}))
    monkeypatch.setattr(dtd, "SNOOZE_FILE", snooze_file)

    class _FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 9, 30)

    monkeypatch.setattr(dtd._dt, "datetime", _FixedDatetime)

    dtd.snooze_to_next_block("t2")

    written = json.loads(snooze_file.read_text())
    assert written["snoozes"] == {"existing": 16, "t2": 10}, \
        "must read-modify-write, not clobber other ids snoozed today"


def test_snooze_to_next_block_discards_stale_day(monkeypatch, tmp_path):
    snooze_file = tmp_path / "dtd-block-snooze.json"
    snooze_file.write_text(json.dumps({"date": "2020-01-01", "snoozes": {"stale": 4}}))
    monkeypatch.setattr(dtd, "SNOOZE_FILE", snooze_file)

    class _FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 9, 30)

    monkeypatch.setattr(dtd._dt, "datetime", _FixedDatetime)

    dtd.snooze_to_next_block("t3")

    written = json.loads(snooze_file.read_text())
    assert "stale" not in written["snoozes"], "a leftover day's snoozes must not carry forward"
    assert written["snoozes"] == {"t3": 10}


def test_snooze_to_next_block_falls_back_to_defer_when_no_next_block(monkeypatch, tmp_path):
    """Already in 亥 (20:00-21:59) — no later block today, so the button
    falls back to delay-a-day instead of silently doing nothing."""
    monkeypatch.setattr(dtd, "SNOOZE_FILE", tmp_path / "dtd-block-snooze.json")

    class _FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 11, 21, 0)

    monkeypatch.setattr(dtd._dt, "datetime", _FixedDatetime)

    called = []
    monkeypatch.setattr(dtd, "defer_task", lambda tid: called.append(tid) or {"ok": True, "target_date": "x"})

    result = dtd.snooze_to_next_block("t4")
    assert called == ["t4"]
    assert result == {"ok": True, "target_date": "x"}
    assert not (tmp_path / "dtd-block-snooze.json").exists(), \
        "fallback path must not also write a (meaningless) snooze file"


def test_api_delay_day_and_delay_block_reject_missing_id():
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    for url in ("/api/delay-day", "/api/delay-block"):
        r = client.post(url, json={})
        assert r.status_code == 400
        assert r.get_json()["ok"] is False


def test_api_delay_day_wires_to_defer_task(monkeypatch):
    monkeypatch.setattr(dtd, "defer_task", lambda tid: {"ok": True, "target_date": "2026-08-12", "id": tid})
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-day", json={"id": "xyz"})
    assert r.get_json() == {"ok": True, "target_date": "2026-08-12", "id": "xyz"}


def test_api_delay_block_wires_to_snooze_to_next_block(monkeypatch):
    monkeypatch.setattr(dtd, "snooze_to_next_block", lambda tid: {"ok": True, "hour": 10, "id": tid})
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-block", json={"id": "xyz"})
    assert r.get_json() == {"ok": True, "hour": 10, "id": "xyz"}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
