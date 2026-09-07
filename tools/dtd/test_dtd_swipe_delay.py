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


# ---------------------------------------------------------------------------
# Full picker (2026-09-07): "right now on left swipe I can only delay to the
# next block... can we make it so hitting the clock opens up the menu and I
# can delay to any of the blocks today (or the 15/30/1h options that exist
# in dtd cli as well?)". Mirrors terminal dtd's ctrl-v picker (DTD_BLOCKARM/
# DTD_BLOCKAPPLY): any remaining 地支 block today, plus the always-available
# +10m/+30m/+1h minute delays (the terminal's actual trio, not 15m).
# ---------------------------------------------------------------------------

def _fixed_now(h, m=0):
    class _FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 7, h, m)
    return _FixedDatetime


def test_remaining_blocks_today_filters_and_sorts(monkeypatch):
    monkeypatch.setattr(dtd._dt, "datetime", _fixed_now(13, 0))  # inside 未 (12-13)
    blocks = dtd.remaining_blocks_today()
    assert [b["hour"] for b in blocks] == [14, 16, 18, 20]
    assert [b["glyph"] for b in blocks] == ["申", "酉", "戌", "亥"]


def test_remaining_blocks_today_empty_after_hai_starts(monkeypatch):
    monkeypatch.setattr(dtd._dt, "datetime", _fixed_now(20, 30))  # inside 亥 (20-21)
    assert dtd.remaining_blocks_today() == []


def test_snooze_to_block_writes_any_explicit_hour(monkeypatch, tmp_path):
    """Unlike snooze_to_next_block, this must accept ANY block's hour, not
    just the immediate next one -- the whole point of the full picker."""
    snooze_file = tmp_path / "dtd-block-snooze.json"
    monkeypatch.setattr(dtd, "SNOOZE_FILE", snooze_file)
    monkeypatch.setattr(dtd._dt, "datetime", _fixed_now(9, 0))  # inside 巳

    result = dtd.snooze_to_block("t1", 20)  # 亥, several blocks away

    assert result == {"ok": True, "hour": 20}
    written = json.loads(snooze_file.read_text())
    assert written["snoozes"]["t1"] == 20


def test_snooze_minutes_writes_epoch_float(monkeypatch, tmp_path):
    snooze_file = tmp_path / "dtd-block-snooze.json"
    monkeypatch.setattr(dtd, "SNOOZE_FILE", snooze_file)
    fixed = _fixed_now(9, 0)
    monkeypatch.setattr(dtd._dt, "datetime", fixed)

    result = dtd.snooze_minutes("t2", 30)

    assert result == {"ok": True, "minutes": 30}
    written = json.loads(snooze_file.read_text())
    expected = fixed.now().timestamp() + 30 * 60
    assert written["snoozes"]["t2"] == expected
    assert isinstance(written["snoozes"]["t2"], float), (
        "must round-trip as a JSON float (decimal point) so the reader can "
        "tell a minute-delay apart from a plain int block-delay hour")


def test_snooze_minutes_rejects_unsupported_value(tmp_path):
    result = dtd.snooze_minutes("t3", 45)
    assert result["ok"] is False


def test_snoozed_ids_distinguishes_hour_int_from_epoch_float(monkeypatch, tmp_path):
    """Regression: the reader used to do `now_hour < int(v)` for EVERY
    snooze value. For a minute-delay's epoch float, int(v) is a huge
    number, so that comparison was always true -- a minute-delayed task
    would never reappear until the next day's date reset. Fixed to mirror
    terminal dtd.sh's isinstance(v, float) distinction."""
    snooze_file = tmp_path / "dtd-block-snooze.json"
    monkeypatch.setattr(dtd, "SNOOZE_FILE", snooze_file)
    fixed = _fixed_now(9, 0)
    monkeypatch.setattr(dtd._dt, "datetime", fixed)
    now_ts = fixed.now().timestamp()
    today = dt.date(2026, 9, 7).isoformat()
    snooze_file.write_text(json.dumps({"date": today, "snoozes": {
        "past_minute_delay": now_ts - 60,     # 1 minute ago -> should reappear
        "future_minute_delay": now_ts + 600,  # 10 minutes from now -> still hidden
        "past_block": 8,                       # 巳 already started -> should reappear
        "future_block": 14,                    # 申 hasn't started -> still hidden
    }}))

    ids = dtd._snoozed_ids()

    assert ids == {"future_minute_delay", "future_block"}, (
        f"got {ids!r}")


def test_api_delay_options_returns_blocks_and_minutes(monkeypatch):
    monkeypatch.setattr(dtd._dt, "datetime", _fixed_now(9, 0))
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.get("/api/delay-options")
    d = r.get_json()
    assert d["ok"] is True
    assert d["minutes"] == [10, 30, 60]
    assert {"glyph": "午", "hour": 10} in d["blocks"]
    assert {"glyph": "巳", "hour": 8} not in d["blocks"], "already-started block must not be offered"


def test_api_delay_hour_rejects_missing_fields():
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-hour", json={"id": "x"})  # no hour
    assert r.status_code == 400
    r = client.post("/api/delay-hour", json={"hour": 14})  # no id
    assert r.status_code == 400


def test_api_delay_hour_rejects_an_already_passed_block(monkeypatch):
    monkeypatch.setattr(dtd._dt, "datetime", _fixed_now(15, 0))  # inside 申 (14-15)
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-hour", json={"id": "x", "hour": 14})  # 申 already started
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_api_delay_hour_wires_to_snooze_to_block(monkeypatch):
    monkeypatch.setattr(dtd._dt, "datetime", _fixed_now(9, 0))
    captured = []
    monkeypatch.setattr(dtd, "snooze_to_block",
                        lambda tid, hour: captured.append((tid, hour)) or {"ok": True, "hour": hour})
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-hour", json={"id": "xyz", "hour": 14})
    assert r.get_json() == {"ok": True, "hour": 14}
    assert captured == [("xyz", 14)]


def test_api_delay_minutes_rejects_missing_fields():
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-minutes", json={"id": "x"})  # no minutes
    assert r.status_code == 400
    r = client.post("/api/delay-minutes", json={"minutes": 30})  # no id
    assert r.status_code == 400


def test_api_delay_minutes_wires_to_snooze_minutes(monkeypatch):
    monkeypatch.setattr(dtd, "snooze_minutes",
                        lambda tid, minutes: {"ok": True, "minutes": minutes, "id": tid})
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/delay-minutes", json={"id": "xyz", "minutes": 60})
    assert r.get_json() == {"ok": True, "minutes": 60, "id": "xyz"}


def test_clock_button_opens_menu_not_instant_next_block():
    """Structural: the swipe-left ⏰ button must open the picker sheet
    (openDelayMenu), not directly call runDelay('block', ...) — the whole
    point of this feature (previously it instant-delayed to next block)."""
    src = (Path(__file__).parent / "dtd.py").read_text()
    assert "act-block').onclick = ()=> openDelayMenu(" in src
    assert "act-block').onclick = ()=> runDelay('block'" not in src


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
