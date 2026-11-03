"""toggl_cli `date <YYYY-MM-DD>`: the command /0r's SKILL.md calls (it did not
exist until 2026-11-01; every /0r run improvised a workaround script).

Pinned to America/Los_Angeles so the DST cases are deterministic: 2026-11-01
is a 25-hour day (PDT -> PST at 02:00), 2026-03-08 a 23-hour day.
"""
import datetime
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("toggl_cli_d", HERE / "toggl_cli.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules["toggl_cli_d"] = cli
_spec.loader.exec_module(cli)

LA = ZoneInfo("America/Los_Angeles")
UTC = datetime.timezone.utc


@pytest.fixture(autouse=True)
def _pin_la(monkeypatch):
    monkeypatch.setattr(cli.daytime, "active_zone", lambda: LA)


def _entry(eid, desc, start_utc, stop_utc):
    return {"id": eid, "description": desc, "project_id": None,
            "start": start_utc.isoformat(),
            "stop": stop_utc.isoformat() if stop_utc else None,
            "duration": int((stop_utc - start_utc).total_seconds()) if stop_utc else -1}


def _u(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=UTC)


def _run(monkeypatch, day, entries):
    calls = []
    monkeypatch.setattr(cli.toggl_api, "get_entries",
                        lambda **kw: calls.append(kw) or entries)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_date([day])
    return buf.getvalue(), calls


def test_date_command_registered():
    assert cli.COMMANDS["date"] is cli.cmd_date


def test_date_fetches_padded_window(monkeypatch):
    _, calls = _run(monkeypatch, "2026-10-30", [])
    assert calls == [{"start_date": "2026-10-29", "end_date": "2026-11-01"}]


def test_date_rejects_bad_input():
    with pytest.raises(SystemExit):
        cli.cmd_date(["10/30"])
    with pytest.raises(SystemExit):
        cli.cmd_date([])


def test_date_clips_both_midnights(monkeypatch):
    # 睡觉 10-29 22:00 PDT -> 10-30 07:00 PDT (05:00Z -> 14:00Z)
    # 睡觉 10-30 23:00 PDT -> 10-31 06:30 PDT (06:00Z -> 13:30Z)
    # work 10-30 09:00-10:00 PDT, an entry fully on 10-29, one fully on 10-31
    entries = [
        _entry(1, "睡觉a", _u(2026, 10, 30, 5), _u(2026, 10, 30, 14)),
        _entry(2, "work", _u(2026, 10, 30, 16), _u(2026, 10, 30, 17)),
        _entry(3, "睡觉b", _u(2026, 10, 31, 6), _u(2026, 10, 31, 13, 30)),
        _entry(4, "yday", _u(2026, 10, 29, 17), _u(2026, 10, 29, 18)),
        _entry(5, "tmrw", _u(2026, 10, 31, 17), _u(2026, 10, 31, 18)),
    ]
    out, _ = _run(monkeypatch, "2026-10-30", entries)
    assert "00:00-07:00 睡觉a" in out and "(420min)" in out, out
    assert "09:00-10:00 work (60min)" in out, out
    assert "23:00-00:00 睡觉b" in out and "(60min)" in out, out
    assert "yday" not in out and "tmrw" not in out, out
    assert "Total: 9h 0min across 3 entries" in out, out
    # sorted chronologically
    assert out.index("睡觉a") < out.index("work") < out.index("睡觉b")


def test_date_dst_fallback_day_is_25_hours(monkeypatch):
    # 2026-11-01 LA: 00:00 PDT (07:00Z) -> 2026-11-02 00:00 PST (08:00Z) = 25h.
    # One entry covering the whole span plus margins must clip to 25h, not 24.
    entries = [_entry(1, "all", _u(2026, 11, 1, 5), _u(2026, 11, 2, 10))]
    out, _ = _run(monkeypatch, "2026-11-01", entries)
    assert "00:00-00:00 all (1500min)" in out, out
    assert "Total: 25h 0min" in out, out


def test_date_dst_fallback_overnight_sleep(monkeypatch):
    # 睡觉 10-31 22:00 PDT (05:00Z) -> 11-01 07:00 PST (15:00Z): 10 real hours,
    # 8 of them after midnight (the 01:00 hour happens twice). A wall-clock
    # subtraction would say 7h.
    entries = [_entry(1, "睡觉", _u(2026, 11, 1, 5), _u(2026, 11, 1, 15))]
    out, _ = _run(monkeypatch, "2026-11-01", entries)
    assert "00:00-07:00 睡觉 (480min)" in out, out
    out_prev, _ = _run(monkeypatch, "2026-10-31", entries)
    assert "22:00-00:00 睡觉 (120min)" in out_prev, out_prev


def test_date_dst_fallback_repeated_hour_entries(monkeypatch):
    # 01:30 PDT (08:30Z) and 01:30 PST (09:30Z) are both on 11-01 and sort by
    # real time, not wall clock.
    entries = [
        _entry(2, "second", _u(2026, 11, 1, 9, 30), _u(2026, 11, 1, 9, 45)),
        _entry(1, "first", _u(2026, 11, 1, 8, 30), _u(2026, 11, 1, 8, 45)),
    ]
    out, _ = _run(monkeypatch, "2026-11-01", entries)
    assert out.index("first") < out.index("second"), out
    assert "Total: 0h 30min across 2 entries" in out, out


def test_date_dst_spring_forward_day_is_23_hours(monkeypatch):
    # 2026-03-08 LA: 00:00 PST (08:00Z) -> 03-09 00:00 PDT (07:00Z) = 23h.
    entries = [_entry(1, "all", _u(2026, 3, 8, 0), _u(2026, 3, 9, 12))]
    out, _ = _run(monkeypatch, "2026-03-08", entries)
    assert "Total: 23h 0min" in out, out


def test_date_running_timer_from_previous_day(monkeypatch):
    # Still-running timer that began the day before: shown from 00:00,
    # duration stays the running sentinel.
    entries = [_entry(1, "run", _u(2026, 10, 30, 5), None)]
    out, _ = _run(monkeypatch, "2026-10-30", entries)
    assert "00:00-running run (running)" in out, out


def test_date_does_not_mutate_raw_entries(monkeypatch):
    e = _entry(1, "睡觉", _u(2026, 11, 1, 5), _u(2026, 11, 1, 15))
    snapshot = dict(e)
    _run(monkeypatch, "2026-11-01", [e])
    assert e == snapshot
