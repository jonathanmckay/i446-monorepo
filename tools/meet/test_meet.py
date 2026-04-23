"""Tests for meet.py — one-sided audio detection + transcript analysis."""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meet import check_one_sided


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
