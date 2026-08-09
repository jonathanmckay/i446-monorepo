"""Tests for meet.py — one-sided audio detection + recorder safety."""

import signal
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meet import (
    GracefulStop,
    SAMPLE_RATE,
    airpods_hfp_active,
    capture_quality_warnings,
    channel_peak,
    check_one_sided,
    format_level_line,
)


# ── Live audio silence detection (unit-level) ────────────────────────────────

SILENCE_THRESH = 500
SILENCE_RATIO_WARN = 0.55


def test_mostly_silent_audio_detected():
    """Audio that is >55% silence should trigger one-sided warning."""
    sr = 16000
    # 2 min of audio: 30s speech, 90s silence
    speech = np.random.randint(-5000, 5000, sr * 30, dtype=np.int16)
    silence = np.random.randint(-100, 100, sr * 90, dtype=np.int16)
    audio = np.concatenate([speech, silence])
    silent_ratio = np.mean(np.abs(audio) < SILENCE_THRESH)
    assert silent_ratio > SILENCE_RATIO_WARN, (
        f"Test audio should be mostly silent, got {silent_ratio:.0%}"
    )


def test_active_conversation_not_flagged():
    """Audio with continuous speech should not trigger warning."""
    sr = 16000
    # 2 min of mostly active audio (some natural pauses)
    speech = np.random.randint(-3000, 3000, sr * 100, dtype=np.int16)
    pause = np.random.randint(-100, 100, sr * 20, dtype=np.int16)
    audio = np.concatenate([speech, pause])
    silent_ratio = np.mean(np.abs(audio) < SILENCE_THRESH)
    assert silent_ratio < SILENCE_RATIO_WARN, (
        f"Active audio should not be mostly silent, got {silent_ratio:.0%}"
    )


# ── Transcript-level filler detection (kept as utility) ─────────────────────

def test_one_sided_transcript_detected():
    """Transcript with heavy filler should be flagged."""
    transcript = (
        "So tell me about your most impactful project. "
        "Mm-hmm. Yep. Yep. Yeah. Mm-hmm. Mm-hmm. Yeah. Mm-hmm. Yep. "
        "Mm-hmm. Mm-hmm. Mm-hmm. Mm-hmm. Yep. Mm-hmm. Mm-hmm. "
        "That's really interesting, tell me more about the impact. "
        "Yep. Okay. Yep. Yep. Mm-hmm. Mm-hmm. Yeah. Mm-hmm. Yep. "
        "Mm-hmm. Yeah. Yep. Yeah. Mm-hmm. Yep. Mm-hmm. Yep. "
        "So how did you measure effort here? "
        "Mm-hmm. Yeah. Yeah. Yep. Yep. Yeah. Mm-hmm. Yep. "
    )
    result = check_one_sided(transcript)
    assert result is not None, "Should detect one-sided transcript"
    assert result >= 40, f"Filler pct should be >=40%, got {result}%"


def test_normal_transcript_not_flagged():
    """Transcript with real dialogue should not be flagged."""
    transcript = (
        "So tell me about your most impactful project. "
        "Sure, I led the migration to microservices at Acme Corp. "
        "We reduced deploy times from 4 hours to 15 minutes. "
        "That's impressive. How did you measure the impact? "
        "We tracked deployment frequency and change failure rate. "
        "The failure rate dropped from 12% to under 3%. "
        "And how did the team respond to the change? "
        "Initially there was resistance, but we ran workshops. "
        "After seeing the results, everyone was on board. "
        "Great. Let's talk about a project that failed. "
        "At my previous company, we tried to build a real-time analytics pipeline. "
        "We underestimated the data volume by 10x. "
    )
    result = check_one_sided(transcript)
    assert result is None, f"Normal transcript should not be flagged, got {result}%"


def test_short_transcript_skipped():
    """Transcripts too short to judge should not be flagged."""
    result = check_one_sided("Hello. Yep. Mm-hmm.")
    assert result is None


def test_all_filler_flagged():
    """100% filler must be flagged."""
    result = check_one_sided("Mm-hmm. Yep. Yeah. " * 20)
    assert result is not None
    assert result >= 90


# ── Live level sampling (user request 2026-08-09: "visibility into the .wav
# to see if it's working or not" — janus tails these LEVEL lines to show a
# live ●mic ●call dot next to the 🎙 recording marker) ──────────────────────

def test_channel_peak_only_looks_at_frames_since_last_sample():
    """A live indicator answers "receiving bytes right now," not "ever had
    signal" — a loud chunk seen on a PRIOR sample must not keep a channel
    reading green forever after it actually goes silent."""
    loud = np.full(100, 20000, dtype=np.int16)
    silent = np.zeros(100, dtype=np.int16)
    frames = [loud]
    peak, idx = channel_peak(frames, 0)
    assert peak == 20000
    assert idx == 1

    frames.append(silent)
    peak, idx = channel_peak(frames, idx)  # only the NEW (silent) chunk
    assert peak == 0
    assert idx == 2


def test_channel_peak_no_new_frames_reads_zero_not_stale():
    frames = [np.full(100, 20000, dtype=np.int16)]
    peak, idx = channel_peak(frames, 1)  # already consumed everything
    assert peak == 0
    assert idx == 1


def test_format_level_line_matches_janus_parsing_regex():
    """The two sides of this protocol live in different files (meet.py emits,
    tools/tg/janus.py's _read_rec_levels/_LEVEL_RE parses) — this pins the
    exact wire format so they can't silently drift apart."""
    import re
    LEVEL_RE = re.compile(r"^LEVEL mic=(\d+)(?: call=(\d+))?$")

    mic_only = format_level_line(1234, None)
    m = LEVEL_RE.match(mic_only)
    assert m and m.group(1) == "1234" and m.group(2) is None

    both = format_level_line(1234, 0)
    m = LEVEL_RE.match(both)
    assert m and m.group(1) == "1234" and m.group(2) == "0"


# ── Teams mode: repeating checks + notifications ─────────────────────────────

import ast

def test_teams_check_repeats_not_oneshot():
    """Bug: teams silence check only fired once (silence_warned=True).
    Amideast meeting warned at 60s but then ran 47 min with no follow-ups.
    Fix: use last_teams_check counter instead of a boolean flag.
    """
    source = Path(__file__).parent.joinpath("meet.py").read_text()
    # Must NOT have silence_warned as the guard for teams mode
    tree = ast.parse(source)
    # Check that the recording loop uses last_teams_check, not silence_warned
    assert "last_teams_check" in source, "Teams check must use repeating interval, not one-shot flag"
    assert "silence_warned" not in source or source.count("silence_warned") == 0, (
        "silence_warned flag should be removed; teams checks must repeat"
    )


def test_teams_warnings_send_notification():
    """Bug: warnings only went to nohup log file, user never saw them.
    Fix: _notify() sends macOS notification for each warning.
    """
    source = Path(__file__).parent.joinpath("meet.py").read_text()
    assert "def _notify" in source, "meet.py must define _notify function"
    assert "display notification" in source, "_notify must use osascript display notification"
    # _notify must be called in the teams warning branches
    # Count calls in the recording function
    in_record = source[source.index("def record_audio"):]
    notify_calls = in_record.count("_notify(")
    assert notify_calls >= 3, (
        f"Expected >=3 _notify calls (both-silent, mic-only, bh-only), got {notify_calls}"
    )


def test_graceful_stop_handles_sigint_and_sigterm_without_keyboardinterrupt():
    """First stop signal should request clean shutdown; repeats must not kill finalization."""
    stopper = GracefulStop()
    stopper._handle(signal.SIGINT, None)
    stopper._handle(signal.SIGTERM, None)
    assert stopper.requested is True
    assert stopper.count == 2


def test_irreplaceable_artifacts_are_saved_under_deferred_signals():
    """WAV/TXT writes must be protected from repeated /d357 stop attempts."""
    source = Path(__file__).parent.joinpath("meet.py").read_text()
    assert "def defer_termination" in source
    assert 'with defer_termination("wav save")' in source
    assert 'with defer_termination("transcript save")' in source


def test_airpods_hfp_detection(monkeypatch):
    monkeypatch.setattr(
        "meet.sd.query_devices",
        lambda: [
            {"name": "Jonathan's AirPods Max", "max_output_channels": 1, "default_samplerate": 24000.0},
            {"name": "BlackHole 2ch", "max_output_channels": 2, "default_samplerate": 48000.0},
        ],
    )
    assert airpods_hfp_active() is True


def test_low_speech_long_recording_flagged():
    audio = np.zeros(SAMPLE_RATE * 120, dtype=np.int16)
    warnings = capture_quality_warnings(audio, "hello")
    assert any("transcript too short" in warning for warning in warnings)
    assert any("low speech signal" in warning for warning in warnings)


def test_daemon_uses_tmux_and_valid_meet_flags():
    """Daemon used to pass invalid --teams and stop with SIGTERM."""
    source = Path(__file__).parent.joinpath("meeting-daemon.py").read_text()
    assert '"--teams"' not in source
    assert "new-session" in source
    assert "send-keys" in source
    assert '"C-c"' in source
    assert "SIGTERM" not in source


# ── Regression: teamstap remote-WAV transcription must be path-based (48kHz-safe) ──
# The teamstap remote WAV is 48kHz mono. faster-whisper resamples to 16kHz only when
# given a PATH (it decodes via PyAV); a raw ndarray is NOT resampled, so 48kHz samples
# produce NaN mel features and an empty transcript. A filing agent hit exactly this
# (2026-06-15) by hand-rolling an ndarray call. The fix: a `--transcribe` CLI that
# routes through meet.transcribe(), which passes the path. These tests pin that design.

import ast as _ast


def _meet_ast():
    src = (Path(__file__).parent / "meet.py").read_text()
    return _ast.parse(src), src


def test_transcribe_cli_arg_exists():
    """meet.py must expose a --transcribe CLI so the /d357 stop flow has one
    canonical, correct way to transcribe the teamstap remote WAV."""
    _, src = _meet_ast()
    assert '"--transcribe"' in src, "--transcribe argument missing from meet.py"
    assert "args.transcribe" in src, "main() must handle args.transcribe"


def test_transcribe_is_path_based_not_ndarray():
    """transcribe() must call model.transcribe(str(<path>), ...). Passing a decoded
    ndarray instead silently breaks on any non-16kHz WAV (no resample → empty text)."""
    tree, _ = _meet_ast()
    fn = next((n for n in _ast.walk(tree)
               if isinstance(n, _ast.FunctionDef) and n.name == "transcribe"), None)
    assert fn is not None, "transcribe() function not found"
    calls = [n for n in _ast.walk(fn)
             if isinstance(n, _ast.Call)
             and isinstance(n.func, _ast.Attribute)
             and n.func.attr == "transcribe"]
    assert calls, "transcribe() must call model.transcribe(...)"
    first = calls[0].args[0] if calls[0].args else None
    # First positional arg must be str(...) — a path — not a bare ndarray Name.
    assert isinstance(first, _ast.Call) and isinstance(first.func, _ast.Name) \
        and first.func.id == "str", \
        "model.transcribe() must receive str(<path>) so faster-whisper resamples; " \
        "passing a raw ndarray skips resampling and empties non-16kHz transcripts"


def test_mic_has_no_speech_warning_suppressed():
    """The live 'MIC HAS NO SPEECH' warning is a false positive (it fires whenever
    JM is listening while others talk — call audio is captured fine), so it must
    not be emitted. The two genuine warnings (both channels silent, call-audio
    silent) must remain."""
    _, src = _meet_ast()
    assert 'f"MIC HAS NO SPEECH' not in src, \
        "MIC HAS NO SPEECH warning must stay suppressed (false positive when listening)"
    # don't over-suppress: the real recording-problem warnings stay
    assert 'f"NO SPEECH on either channel' in src
    assert 'f"CALL AUDIO HAS NO SPEECH' in src
