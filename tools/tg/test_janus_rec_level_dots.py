"""User request 2026-08-09: "when I'm doing recording, I think I want to have
an equalizer showing to show the meeting to date, whether there was signal
either on my end or the other end -- essentially I want visibility into the
.wav to see if it's working or not." meet.py's existing silence/one-sided
checks only warn every 60-180s into a log file nobody watches live — this bit
the user in the exact session that prompted the request (call audio sat at
zero for 2 minutes before the fallback warning fired).

2026-08-10 follow-up ("something more clear to show input and output audio
channels ... more realtime ... two lights that can both flash"): meet.py now
prints "LEVEL mic=N [call=N]" at 4 Hz (tools/meet/test_meet.py covers that
side) and the dots are three-state per channel — bright green flashing with
speech (newest sample ≥ _REC_ACTIVE_THRESH), dim green when the channel is
alive but silent, red only when the whole ~3s window is flat zero (a single
0 between words is normal digital silence on the call leg, not a broken
route). janus tails the active recording's log (path from
~/.local/state/jm/d357-state.json, the same file _load_recording_state()
already reads) and renders ●mic ●call next to the 🎙 marker in the pinned
bottom bar; nothing renders before the first sample lands or when nothing is
recording.
"""
import datetime as dtm
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")

ACTIVE = "bold #00ff5f"
QUIET = "#2e8b57"
DEAD = "bold #ff5555"


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
    mod._REC_LEVEL_CACHE.update(checked=float("-inf"), have_data=False, mic=None, call=None)
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
    mic, call = mod._read_rec_levels()
    assert mic == "active"
    assert call is None
    frags = mod._rec_level_dot_frags(None)
    assert len(frags) == 1


def test_speech_flashes_bright_and_silence_dims(tmp_path):
    """The two lights must FLASH with activity: a sample at/above the speech
    threshold renders the bright style, a below-threshold (but nonzero)
    sample the dim one — the 4 Hz cadence does the flashing."""
    mod = _load_tui()
    _setup_recording(mod, tmp_path, log_lines="LEVEL mic=2000 call=150\n")
    assert mod._read_rec_levels() == ("active", "quiet")
    frags = mod._rec_level_dot_frags(None)
    assert frags[0][0] == ACTIVE, "speaking mic must render the bright flash style"
    assert frags[1][0] == QUIET, "alive-but-silent call must render dim, not red"


def test_both_channels_flash_when_both_speak(tmp_path):
    mod = _load_tui()
    _setup_recording(mod, tmp_path, log_lines="LEVEL mic=900 call=950\n")
    assert mod._read_rec_levels() == ("active", "active")
    frags = mod._rec_level_dot_frags(None)
    assert len(frags) == 2
    assert all(style == ACTIVE for style, _text, *_ in frags)


def test_dead_call_channel_reads_red_not_green(tmp_path):
    """The exact regression this feature exists for: call audio at flat zero
    across the window must render red, not silently look identical to a
    healthy channel."""
    mod = _load_tui()
    lines = "".join("LEVEL mic=800 call=0\n" for _ in range(12))
    _setup_recording(mod, tmp_path, log_lines=lines)
    mic, call = mod._read_rec_levels()
    assert mic == "active"
    assert call == "dead"
    frags = mod._rec_level_dot_frags(None)
    assert frags[0][0] == ACTIVE, "mic (has signal) must read bright"
    assert frags[1][0] == DEAD, "call (flat zero) must read red"


def test_momentary_zero_between_words_is_not_dead(tmp_path):
    """A single 0 sample on the call leg is normal digital silence between
    words at 4 Hz — it must dim, not flip red."""
    mod = _load_tui()
    _setup_recording(mod, tmp_path,
                     log_lines="LEVEL mic=800 call=1200\nLEVEL mic=800 call=0\n")
    mic, call = mod._read_rec_levels()
    assert call == "quiet", "one silent sample after real signal is not a dead route"


def test_dead_channel_recovering_flashes_again(tmp_path):
    """A dead channel that recovers must show life again — the state reflects
    the newest samples, not the first ones seen in the log."""
    mod = _load_tui()
    _setup_recording(mod, tmp_path,
                     log_lines="LEVEL mic=800 call=0\nLEVEL mic=800 call=0\nLEVEL mic=800 call=950\n")
    mic, call = mod._read_rec_levels()
    assert call == "active"


def test_cache_ttl_avoids_rereading_log_every_render_tick(tmp_path):
    mod = _load_tui()
    lines = "".join("LEVEL mic=800 call=0\n" for _ in range(12))
    log_path = _setup_recording(mod, tmp_path, log_lines=lines)
    first = mod._read_rec_levels()
    assert first == ("active", "dead")
    # Channel recovers, but within the TTL window the cached (stale) reading
    # must still be returned — the whole point of the cache is to NOT tail
    # the log on every ~0.1s repaint.
    log_path.write_text("LEVEL mic=800 call=900\n")
    cached = mod._read_rec_levels()
    assert cached == ("active", "dead"), "should still be serving the cached value"


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
