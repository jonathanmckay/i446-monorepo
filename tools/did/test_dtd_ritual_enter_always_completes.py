#!/usr/bin/env python3
"""Regression (2026-07-29): "I marked -1l, -1t as done, didn't get the -1n
points (as shown in Janus)".

DTD_ENTER only completes the selected task (push to the FIFO -> did-fast
--ritual stamping) when a Toggl timer matching its name is ALREADY running;
otherwise it silently STARTS one instead ("$START" "$task") and the
completion never happens at all -- no Todoist close, no header stamp, no
points. Manual rituals (-1g/-1ibx/سمش) often coincide with an
already-running matching timer from other usage, masking the bug. Auto
rituals (-1t/-1l) are passive retrospective checks with no natural
corresponding activity to time, so a plain Enter on them almost never finds
a match and reliably falls into the "start a timer" branch instead of
completing -- confirmed by a real orphaned "-1t @n156 (10min)" Toggl entry
left over from an Enter press that was never followed up.

Fix: -1neon ritual cards (😈-prefixed) always take the completion branch,
regardless of any running timer.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _enter_body():
    m = re.search(r"cat > \"\$DTD_ENTER\" << ENTEREOF\n(.*?)\nENTEREOF", SRC, re.S)
    assert m, "could not find the DTD_ENTER heredoc in dtd.sh"
    return m.group(1)


def test_enter_detects_ritual_cards_by_marker():
    body = _enter_body()
    assert re.search(r'clean.*==\s*😈\*', body), (
        "DTD_ENTER must detect -1neon ritual cards (😈-prefixed content) "
        "before deciding whether to complete or start a timer"
    )


def test_enter_completion_branch_ors_in_ritual_detection():
    body = _enter_body()
    # The branch that pushes to the FIFO (completion) must fire for ritual
    # cards unconditionally, not only when a timer happens to match.
    branch_re = re.search(
        r'if \[\[ .*is_ritual_card.*(cur_desc|timer_desc).*\]\]; then', body)
    assert branch_re, (
        "the completion branch's condition must OR in the ritual-card "
        "check, not gate ritual cards behind the timer-match condition alone"
    )


def test_enter_still_starts_timer_for_non_ritual_non_matching_task():
    """The fix must be additive -- ordinary one-off tasks with no running
    timer should still fall to the "start a timer" branch, not be forced to
    complete (that's the normal, intended two-step start-then-complete UX)."""
    body = _enter_body()
    assert '"$START" "$task"' in body.replace("\\$", "$"), (
        "non-ritual tasks with no matching timer must still start a timer, "
        "not be force-completed"
    )
    # The "else" starting branch must remain reachable -- i.e. the ritual
    # check must be a condition INSIDE the if, not something that replaces
    # the if/else structure entirely.
    assert re.search(r'if \[\[ .*is_ritual_card.* \]\]; then.*\nelse\n', body, re.S), (
        "the start-a-timer else-branch must still exist for non-ritual, "
        "non-matching-timer tasks"
    )
