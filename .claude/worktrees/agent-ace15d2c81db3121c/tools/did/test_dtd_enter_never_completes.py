#!/usr/bin/env python3
"""Regression (2026-07-31): "let's change the hotkeys so that I always have
to hit opt+enter to mark a task done (not enter twice)".

DTD_ENTER used to complete the selected task (push to the FIFO -> did-fast
--ritual/generic completion) whenever a Toggl timer matching its name was
already running, or unconditionally for -1neon ritual cards (2026-07-29 fix,
now superseded). That made a second Enter press on an already-timing item
complete it — easy to trigger by accident, since Enter's other, far more
common job is simply starting a timer.

Fix: enter.sh does exactly one thing now — resolve the id to its canonical
task and call $START. Completion (the FIFO push, optimistic id-hide,
quick-close, session/removed journaling) lives ONLY in the alt-enter path
($DTD_DONE_ROUTER / done.sh) now. This supersedes
test_dtd_ritual_enter_always_completes.py's premise entirely — ritual cards
no longer get special-cased in enter.sh; $DTD_START already resolves a
😈-prefixed name to its correct Toggl project, so pressing Enter on one just
starts a properly-labeled timer.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _enter_body() -> str:
    m = re.search(r"cat > \"\$DTD_ENTER\" << ENTEREOF\n(.*?)\nENTEREOF", SRC, re.S)
    assert m, "could not find the DTD_ENTER heredoc in dtd.sh"
    return m.group(1)


def test_enter_body_has_no_completion_branch():
    body = _enter_body()
    # None of the completion side-effects (FIFO push, optimistic hide,
    # quick-close, session journaling) may appear in enter.sh at all.
    for marker in ("$FIFO", "$SESSION", "$REMOVED", "quick-close.py", "$PUSHED"):
        assert marker.replace("$", "\\$") not in body, (
            f"enter.sh must not reference {marker} -- that's completion "
            "machinery, and enter.sh no longer completes anything")


def test_enter_body_unconditionally_starts():
    body = _enter_body()
    # Exactly one statement of substance after the block-picker early exit:
    # resolve the id, then always call $START. No if/else branching on
    # ritual-card markers or timer-match state.
    assert '"\\$START" "\\$task"' in body
    assert "is_ritual_card" not in body
    assert "cur_desc" not in body and "timer_desc" not in body


def test_enter_still_handles_block_picker_mode():
    """The unrelated block-row snooze special case (2026-07-27) must survive
    -- it's not a task-completion path, just a different picker mode."""
    body = _enter_body()
    assert 'BLOCK:*' in body and "DTD_BLOCKAPPLY" in body


def test_alt_enter_is_the_only_completion_entry_point():
    """Structural: exactly one heredoc (done.sh's, reached via
    $DTD_DONE_ROUTER / alt-enter) may push to the FIFO -- enter.sh's heredoc
    must not."""
    assert SRC.count('printf \'%s\\t%s\\n\' "\\$1" "\\$clean" > "\\$FIFO"') <= 1, (
        "only one completion path (done.sh, via alt-enter) may push to the FIFO")


def test_dtd_keys_hint_reflects_the_new_split():
    assert "enter: start" in SRC
    assert "⌥⏎: done" in SRC or "alt-enter: done" in SRC
    assert "start/complete" not in SRC, (
        "the header hint must not still advertise enter as a completion key")
