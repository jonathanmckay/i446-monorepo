"""User request 2026-08-09: "when I'm doing recording, I think I want to have
an equalizer showing to show the meeting to date, whether there was signal
either on my end or the other end -- essentially I want visibility into the
.wav to see if it's working or not." meet.py's existing silence/one-sided
checks only warn every 60-180s into a log file nobody watches live — this bit
the user in the exact session that prompted the request (call audio sat at
zero for 2 minutes before the fallback warning fired).

meet.py now prints "LEVEL mic=N [call=N]" once a second (tools/meet/test_meet.py
covers that side). janus tails the active recording's log (path from
~/.local/state/jm/d357-state.json, the same file _load_recording_state()
already reads) for the newest such line and renders a ●mic ●call dot next to
the 🎙 marker in the pinned bottom bar: green for signal in the last ~1s, red
for flat zero, nothing before the first sample lands or when nothing is
recording.
"""
import datetime as dtm
import importlib.util
import json
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_reclevels", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_reclevels"] = mod
    spec.loader.exec_module(mod)
    return mod


def _freeze_now(mod, when):
    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    mod.dt.datetime = _DT


def _setup_recording(mod, tmp_path, desc="team sync", log_lines=""):
    log_path = tmp_path / "rec.log"
    log_path.write_text(log_lines)
    state_path = tmp_path / "d357-state.json"
    state_path.write_text(json.dumps({"log": str(log_path)}))
    mod.D357_STATE = state_path
    mod.STATE.recording = {"desc": desc, "start_dt": dtm.datetime.now(mod.TZ)}
    mod.STATE.current = {"description": desc, "start": "2026-08-09T17:00:00+00:00",
                         "project_id": None}
    mod.STATE.event_sel = None
    # Force a real read on the next call — NOT checked=0.0: time.monotonic()
    # starts near 0 in some environments (bug this whole feature tripped
    # over), so 0.0 is indistinguishable from "just checked" and would mask
    # exactly the regression this suite exists to catch.
    mod._REC_LEVEL_CACHE.update(checked=float("-inf"), have_data=False, mic_ok=None, call_ok=None)
    return log_path


def test_no_dots_when_nothing_recording():
    mod = _load_tui()
    mod.STATE.recording = None
    assert mod._read_rec_levels() is None
    assert mod._rec_level_dot_frags(None) == []


def test_no_dots_before_first_level_sample(tmp_path):
    mod = _load_tui()
    _setup_recording(mod, tmp_path, log_lines="🎙  Mic: MacBook Pro Microphone\n")
    assert mod._read_rec_levels() is None
    assert mod._rec_level_dot_frags(None) == []


def test_mic_only_session_shows_single_dot(tmp_path):
    mod = _load_tui()
    _setup_recording(mod, tmp_path, log_lines="LEVEL mic=1234\n")
    mic_ok, call_ok = mod._read_rec_levels()
    assert mic_ok is True
    assert call_ok is None
    frags = mod._rec_level_dot_frags(None)
    assert len(frags) == 1


def test_both_channels_green_when_both_have_signal(tmp_path):
    mod = _load_tui()
    _setup_recording(mod, tmp_path, log_lines="LEVEL mic=500 call=900\n")
    mic_ok, call_ok = mod._read_rec_levels()
    assert mic_ok is True and call_ok is True
    frags = mod._rec_level_dot_frags(None)
    assert len(frags) == 2
    assert all("#00d700" in style for style, _text, *_ in frags)


def test_dead_call_channel_reads_red_not_green(tmp_path):
    """The exact regression this feature exists for: call audio at flat zero
    must render red, not silently look identical to a healthy channel."""
    mod = _load_tui()
    _setup_recording(mod, tmp_path, log_lines="LEVEL mic=800 call=0\n")
    mic_ok, call_ok = mod._read_rec_levels()
    assert mic_ok is True
    assert call_ok is False
    frags = mod._rec_level_dot_frags(None)
    assert "#00d700" in frags[0][0], "mic (has signal) must read green"
    assert "#ff5555" in frags[1][0], "call (flat zero) must read red"


def test_only_the_newest_level_line_wins(tmp_path):
    """A dead channel that recovers must show green again — the dot reflects
    the LATEST sample, not the first one seen in the log."""
    mod = _load_tui()
    _setup_recording(mod, tmp_path,
                     log_lines="LEVEL mic=800 call=0\nLEVEL mic=800 call=0\nLEVEL mic=800 call=950\n")
    mic_ok, call_ok = mod._read_rec_levels()
    assert call_ok is True


def test_cache_ttl_avoids_rereading_log_every_render_tick(tmp_path):
    mod = _load_tui()
    log_path = _setup_recording(mod, tmp_path, log_lines="LEVEL mic=800 call=0\n")
    first = mod._read_rec_levels()
    assert first == (True, False)
    # Channel recovers, but within the TTL window the cached (stale) reading
    # must still be returned — the whole point of the cache is to NOT tail
    # the log on every ~0.1s repaint.
    log_path.write_text("LEVEL mic=800 call=900\n")
    cached = mod._read_rec_levels()
    assert cached == (True, False), "should still be serving the cached value"


def test_bottom_bar_renders_dots_next_to_mic_marker(tmp_path):
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 10, 33, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    _setup_recording(mod, tmp_path, desc="team sync", log_lines="LEVEL mic=800 call=0\n")
    frags = mod.render_current_bottom()
    flat = "".join(t for _s, t, *_ in frags)
    assert "🎙" in flat
    assert "●" in flat
    # Exactly 2 dots (mic + call), both attached as their own colored fragments.
    dot_frags = [f for f in frags if f[1].strip() in ("●", " ●")]
    assert len(dot_frags) == 2


def test_bottom_bar_shows_no_dots_for_a_different_running_entry(tmp_path):
    """The 🎙/dots must only appear for the entry that's ACTUALLY being
    recorded, not any arbitrary running timer."""
    mod = _load_tui()
    now = dtm.datetime(2026, 8, 9, 10, 33, 0, tzinfo=TZ)
    _freeze_now(mod, now)
    _setup_recording(mod, tmp_path, desc="team sync", log_lines="LEVEL mic=800 call=0\n")
    mod.STATE.current = {"description": "unrelated task",
                         "start": "2026-08-09T17:00:00+00:00", "project_id": None}
    frags = mod.render_current_bottom()
    flat = "".join(t for _s, t, *_ in frags)
    assert "🎙" not in flat
    assert "●" not in flat


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
