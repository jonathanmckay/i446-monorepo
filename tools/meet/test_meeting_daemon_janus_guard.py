#!/usr/bin/env python3
"""Regression: meeting-daemon.py must never stop a janus-owned recording.

Bug (found 2026-08-02 while fixing the double-Ctrl-C race in d357_quick.py):
the daemon's main loop has three stop conditions for a live recording —
calendar event ended, call client closed, and "meeting changed" (Outlook
subject != state['name']). Only the "call client closed" branch checked
`state.get("source")`. The other two would fire against a janus-owned
recording with no coordination at all: no lock, no awareness of
d357_quick.py's per-session stop lock, just an unconditional Ctrl-C + later
an unconditional clear_state(). Given the daemon's meeting title comes from
polling Outlook (MSFT import calendar, known slow-sync/lag) while janus's
comes from Google Calendar, a title mismatch between the two is routine, not
hypothetical — this path was live and would eventually fire.

Fix: guard the whole "if recording:" stop-decision block on
`state.get("source") == "janus"` up front — janus owns that recording's
entire stop lifecycle itself.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "tools" / "meet" / "meeting-daemon.py"


def test_daemon_never_stops_janus_owned_recording():
    src = DAEMON.read_text()
    idx_if = src.index("if recording:")
    idx_janus_guard = src.index('state.get("source") == "janus"', idx_if)
    idx_calendar_ended = src.index("calendar_event_ended(state)", idx_if)
    idx_meeting_changed = src.index('meeting["subject"] != state.get("name")', idx_if)
    assert idx_janus_guard < idx_calendar_ended < idx_meeting_changed, (
        "the source==\"janus\" guard must be checked BEFORE calendar_event_ended "
        "and the meeting-changed check, so neither can fire against a "
        "janus-owned recording")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
