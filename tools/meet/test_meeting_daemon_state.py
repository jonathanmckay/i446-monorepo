#!/usr/bin/env python3
"""Regression: meeting-daemon.py must share the /d357 skill's state file.

Bug (2026-07-12): "d357 should start the toggl timer, but doesn't seem to be
doing that." The auto-recorder daemon (meeting-daemon.py) decides whether to
launch a recording + Toggl timer by reading the shared d357 state and checking
`is_recording()`. Its docstring claims the state lives at
`~/.claude/skills/d357/state.json` "(shared with /d357 skill)", but the skill
actually reads/writes `~/.local/state/jm/d357-state.json`. The paths had
diverged, so the daemon could never see a recording the skill had started (and
vice versa) — it would start a DUPLICATE meet.py + Toggl timer for the same
meeting, producing competing timers so the skill's timer never "stuck".

Fix: point the daemon's STATE_FILE (and PENDING_FILE) at the skill's canonical
`~/.local/state/jm/` path. This test pins that the two agree.

Note: the separate live symptom in that session (a lowercase, event-titled entry
backdated to the calendar start superseding d357's timer) is Toggl Track's own
Google-Calendar auto-tracking, external to this repo — not something these files
can fix. This regression covers only the in-repo coordination bug.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "tools" / "meet" / "meeting-daemon.py"

# The /d357 skill's canonical state path (SKILL.md header + the skill's own
# start/stop flow both use this).
SKILL_STATE = "~/.local/state/jm/d357-state.json"
# The actual old *assignment* (not a doc/comment mention of the path).
STALE_ASSIGN = 'Path.home() / ".claude/skills/d357/state.json"'


def test_daemon_state_file_matches_skill_canonical_path():
    src = DAEMON.read_text()
    assert 'STATE_FILE = Path.home() / ".local/state/jm/d357-state.json"' in src, (
        "meeting-daemon STATE_FILE must point at the skill's canonical "
        f"{SKILL_STATE} so is_recording() sees skill-started recordings")


def test_daemon_no_longer_uses_stale_state_path():
    src = DAEMON.read_text()
    assert STALE_ASSIGN not in src, (
        "the stale STATE_FILE assignment must be gone — sharing the skill's "
        "state file is the whole point")


def test_daemon_docstring_documents_shared_path():
    src = DAEMON.read_text()
    # The docstring's advertised path must match the real STATE_FILE constant.
    assert "~/.local/state/jm/d357-state.json (shared with /d357 skill)" in src


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
