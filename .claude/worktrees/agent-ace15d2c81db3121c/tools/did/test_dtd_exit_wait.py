#!/usr/bin/env python3
"""Regression: dtd's close-time wait reflects the actual remaining backlog, not
the whole session, and exits fast/silently when the queue already drained.

Bug (2026-07-14): on close dtd printed 'Waiting for <session_count> tasks...'
(the entire session's completions) and always slept on a 1s cadence, even when
the background worker had already drained the queue — so a fully-processed
session still showed a scary 'Waiting for 12 tasks' and a ~1s pause. The worker
must still be joined before cleanup (never drop a completion), but the count and
messaging should track pushed-minus-processed.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_exit_waits_on_remaining_not_session_count():
    assert "remaining=$(( pushed - processed ))" in SRC
    assert 'echo "Waiting for $remaining task(s)..."' in SRC
    assert 'echo "Waiting for $session_count tasks..."' not in SRC


def test_exit_message_and_dots_only_when_backlog_remains():
    # message + progress dots are guarded by remaining>0 (silent when drained)
    assert re.search(r'if \(\( remaining > 0 \)\); then\n\s*echo ""\n\s*echo "Waiting for \$remaining', SRC)
    assert "(( remaining > 0 )) && printf" in SRC


def test_exit_still_joins_worker_and_polls_fast():
    assert "while kill -0 $WORKER_PID 2>/dev/null; do" in SRC, "must still join the worker"
    assert "sleep 0.2" in SRC, "poll fast so it exits the moment the worker catches up"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
