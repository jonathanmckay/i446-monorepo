"""Tests for meet.py — focused on one-sided transcript detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from meet import check_one_sided


def test_one_sided_transcript_detected():
    """Transcript with heavy filler (only one side captured) should be flagged."""
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
    assert result >= 40, f"Filler pct should be ≥40%, got {result}%"


def test_normal_transcript_not_flagged():
    """Transcript with real dialogue from both sides should not be flagged."""
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
    transcript = "Hello. Yep. Mm-hmm."
    result = check_one_sided(transcript)
    assert result is None, "Short transcript should not be flagged"


def test_edge_case_all_filler():
    """100% filler should definitely be flagged."""
    transcript = "Mm-hmm. Yep. Yeah. " * 20
    result = check_one_sided(transcript)
    assert result is not None, "All-filler transcript must be flagged"
    assert result >= 90, f"Expected near-100% filler, got {result}%"
