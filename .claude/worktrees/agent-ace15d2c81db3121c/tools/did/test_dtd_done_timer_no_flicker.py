#!/usr/bin/env python3
"""Regression (2026-08-13): "when I mark a task as done, the current timer
goes blank for 5 seconds before resuming ... if I'm not changing the timer,
it shouldn't flash."

Root cause: done.sh (alt-enter) unconditionally truncated $DTD_TIMER
(`: > "$TIMER"`) on every completion, regardless of whether the completed
task was the one the timer file was tracking. dtd-ticker.py watches
$DTD_TIMER's mtime every 0.1s (see dtd-ticker.py's _read_timer_file) and
treats an emptied file as "dtd went idle," blanking the footer immediately.
The footer only recovered once the next periodic Toggl poll (POLL=12s in
dtd-ticker.py) reconciled it back to the still-running entry -- the
"blank for 5 seconds" the user saw.

Fix: only clear $DTD_TIMER when the completed task matches the entry it's
tracking -- by id (field 3, the same field the list generator's own
running-highlight logic keys off, see test_dtd_running_highlight_by_id.py),
falling back to a name match when id-less.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()


def _done_body() -> str:
    start = SRC.index("<< DONEEOF")
    return SRC[start:SRC.index("\nDONEEOF", start)]


# ── Structural ──────────────────────────────────────────────────────────────

def test_timer_clear_is_no_longer_unconditional():
    body = _done_body()
    assert ': > "\\$TIMER"' not in body.split(
        '_timer_id=\\$(cut -f3 "\\$TIMER"'
    )[0], "the OLD unconditional clear (before the id guard) must be gone"


def test_timer_clear_is_gated_on_matching_the_running_entry():
    body = _done_body()
    assert '_timer_id=\\$(cut -f3 "\\$TIMER"' in body
    assert '"\\$_timer_id" == "\\$1"' in body, \
        "must compare the timer file's task-id field against the completed task's id"
    assert '"\\$_timer_desc" == "\\$clean_lower"' in body, \
        "id-less completions must still fall back to a name match, not clear blindly"


# ── Functional: extract the exact guard and exercise it standalone ─────────

def _timer_guard_snippet() -> str:
    start_anchor = ('printf \'%s\\tdone\\t%s\\t%s\\n\' '
                     '"\\$(date +%Y-%m-%dT%H:%M:%S)" "\\$1" "\\$clean" >> "\\$PUSHED.log"')
    end_anchor = 'echo "\N{HOURGLASS WITH FLOWING SAND} completing: \\$clean_for_filter" > "\\$HDR"'
    body = _done_body()
    start = body.index(start_anchor) + len(start_anchor)
    end = body.index(end_anchor, start)
    return body[start:end].strip("\n").replace("\\$", "$")


def _run_guard(tmp_path, timer_content, task_id, clean_lower):
    timer = tmp_path / "timer"
    timer.write_text(timer_content)
    script = tmp_path / "guard.sh"
    script.write_text(
        "#!/bin/zsh\nclean_lower=\"$CLEAN_LOWER\"\n" + _timer_guard_snippet()
        + "\nexit 0\n")
    script.chmod(0o755)
    env = {**os.environ, "TIMER": str(timer), "CLEAN_LOWER": clean_lower}
    args = ["zsh", str(script)]
    if task_id:
        args.append(task_id)
    r = subprocess.run(args, capture_output=True, text=True, timeout=5, env=env)
    assert r.returncode == 0, f"guard script errored: {r.stderr}"
    return timer.read_text()


def test_completing_unrelated_task_leaves_running_timer_untouched(tmp_path):
    """The exact bug: a DIFFERENT task (id 'OTHER') is completed while
    task 'RUNNING123' is timing -- the footer must not flicker."""
    running = "some task\t1700000000\tRUNNING123\ti9\n"
    result = _run_guard(tmp_path, running, task_id="OTHER", clean_lower="some other task")
    assert result == running, "completing an unrelated task must not touch the timer file"


def test_completing_the_running_task_clears_the_timer(tmp_path):
    running = "some task\t1700000000\tRUNNING123\ti9\n"
    result = _run_guard(tmp_path, running, task_id="RUNNING123", clean_lower="some task")
    assert result == "", "completing the task that IS running must still stop the timer display"


def test_idless_completion_matching_running_name_clears_timer(tmp_path):
    running = "some task\t1700000000\t\ti9\n"  # no id field (e.g. ad-hoc entry)
    result = _run_guard(tmp_path, running, task_id="", clean_lower="some task")
    assert result == "", "id-less completion of the same-named running task must still clear it"


def test_idless_completion_of_different_name_leaves_timer(tmp_path):
    running = "some task\t1700000000\t\ti9\n"
    result = _run_guard(tmp_path, running, task_id="", clean_lower="a different task")
    assert result == running, "id-less completion of an unrelated task must not touch the timer"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
