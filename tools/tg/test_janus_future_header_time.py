"""Regression: a future block's header event must show its own start time when
it doesn't begin at the block's :00.

The compact block header is labelled `<block>:00`, and the dominant upcoming
event rides that line (`午:00 ☀️ standup (60)`). Before 2026-07-05 the event
carried no time of its own, so a meeting starting at 11:00 read as filling 午
from 10:00. The header must print the full HH:MM whenever the event's start
differs from the block start — and stay bare when it really is at :00."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_hdr_time", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_hdr_time"] = mod
    spec.loader.exec_module(mod)
    return mod


def _pick(hour, minute, label="standup", dur=60):
    start = dtm.datetime.now(TZ).replace(hour=hour, minute=minute,
                                         second=0, microsecond=0)
    return {"start_dt": start, "time_str": f"{start:%H:%M}", "label": label,
            "style": "", "dur_min": dur}


def _header_line(mod, picks):
    out = mod._compact_block_lines("午", 10, picks, 0, "", is_future=True)
    text = "".join(t for _, t in out)
    return text.split("\n", 1)[0]


def test_off_hour_event_shows_full_time():
    mod = _load_tui()
    hdr = _header_line(mod, [_pick(11, 0)])
    assert "11:00" in hdr, f"header must show the event's real hour: {hdr!r}"
    assert "standup" in hdr


def test_off_minute_event_shows_full_time():
    # Same hour as the block but not :00 (10:30) — still ambiguous, still shown.
    mod = _load_tui()
    hdr = _header_line(mod, [_pick(10, 30)])
    assert "10:30" in hdr, f"header must show an off-minute start: {hdr!r}"


def test_block_start_event_stays_bare():
    # An event exactly at the block's :00 adds no redundant time.
    mod = _load_tui()
    hdr = _header_line(mod, [_pick(10, 0)])
    assert "10:00" not in hdr, f"no redundant time for a :00 event: {hdr!r}"
    assert hdr.startswith("午:00"), hdr


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
