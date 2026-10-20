#!/usr/bin/env python3
"""Regression: a ctrl-z that lands while a completion is in flight must be
QUEUED, not dropped.

Bug (2026-07-12): "I hit ctrl z and dtd says task still processing, it should
be able to cache or do something with the command." The ctrl-z undo script
compared pushed/processed and, on any in-flight task (pushed > processed),
printed "still processing — retry ctrl-z in a moment" and `exit 0` immediately —
throwing the undo away and forcing the user to hit ctrl-z again. Completions run
Todoist + Excel-over-ssh, so the window is easily hit.

Fix: the undo script polls for the worker to settle (bounded, ~5s at 100ms
steps) BEFORE the bail, so the undo fires as soon as the journal entry lands.
The undo stays inline in the ctrl-z binding (not deferred to the worker) so the
binding's `+reload($DTD_RELOAD)+transform-header` still runs afterward and the
undone task reappears in the list.
"""
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _undo_block() -> str:
    start = DTD.index('cat > "$DTD_UNDO"')
    # Skip the opening `<< UNDOEOF` on the first line; match the closing
    # delimiter, which sits at the start of its own line.
    end = DTD.index("\nUNDOEOF", start)
    return DTD[start:end]


def test_undo_polls_before_bailing():
    block = _undo_block()
    i_loop = block.find("for _ in {1..50}")
    i_sleep = block.find("sleep 0.1")
    i_bail = block.find("retry ctrl-z in a moment")
    assert i_loop != -1, "undo must poll for the worker to settle, not bail at once"
    assert i_sleep != -1 and i_loop < i_sleep, "poll loop must sleep between checks"
    assert i_bail != -1
    assert i_loop < i_bail, (
        "the settle-wait loop must come BEFORE the give-up bail — otherwise a "
        "mid-completion ctrl-z is dropped instead of queued")


def test_undo_wait_is_bounded():
    """The wait must terminate if the worker is genuinely stuck — a bounded
    for-loop with a settle break, not an unbounded spin."""
    block = _undo_block()
    wait_region = block[:block.index("retry ctrl-z in a moment")]
    assert "for _ in {1..50}" in wait_region
    assert "(( pushed <= processed )) && break" in wait_region, (
        "loop must break as soon as the worker settles")


def test_undo_still_runs_inline_after_wait():
    """After settling, the undo must run in THIS script (so the fzf binding's
    reload fires), not be handed off to the background worker."""
    block = _undo_block()
    i_bail = block.index("retry ctrl-z in a moment")
    # The undo-fast invocation must sit after the bail guard, in the same script.
    assert block.index("--undo", i_bail) > i_bail, (
        "undo-fast must run inline after the settle-wait/guard")


def test_queued_message_shown_while_waiting():
    """The user should see the ctrl-z was accepted, not silently swallowed."""
    block = _undo_block()
    assert "undo queued" in block
