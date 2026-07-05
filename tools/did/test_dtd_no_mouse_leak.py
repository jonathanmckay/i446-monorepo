#!/usr/bin/env python3
"""Regression: SGR mouse escape sequences must never leak into dtd's input box.

Bug (2026-07-05): the query filled with literal `^[[<34;41;15M...` — SGR
mouse-MOTION events typed as text. fzf (mouse on by default) subscribes to
click/scroll only; motion events — forwarded by cmux once any mouse mode is
active, or left enabled (1002/1003) by a child program run from an execute()
binding — fall through fzf's parser into the query.

Fix: dtd is keyboard-driven, so (1) fzf runs with --no-mouse (never subscribes
to any mouse mode), and (2) every fzf launch plus each interactive execute()
script (defer/points/edit) resets all tracking modes (1000/1002/1003/1006) on
the tty, killing stray modes a child left behind mid-session.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()

RESET_SEQ = r"\033[?1000l\033[?1002l\033[?1003l\033[?1006l"


def test_fzf_runs_with_no_mouse():
    m = re.search(r"fzf --prompt.*?--header-first", DTD, re.DOTALL)
    assert m, "fzf invocation not found"
    assert "--no-mouse" in m.group(0), "fzf must not subscribe to mouse events"


def test_mouse_modes_reset_before_each_fzf_launch():
    # The reset printf must appear in the UI loop before the fzf invocation.
    i_fzf = DTD.index("fzf --prompt")
    loop_start = DTD.rindex("while true; do", 0, i_fzf)
    pre_launch = DTD[loop_start:i_fzf]
    assert RESET_SEQ in pre_launch, (
        "each fzf launch must first disable stray mouse-tracking modes")


def test_interactive_execute_scripts_reset_mouse_modes():
    # defer / points / edit run interactively in the tty while fzf is suspended;
    # each must reset tracking modes before fzf resumes.
    for eof in ("DEFEREOF", "POINTSEOF", "EDITEOF"):
        start = DTD.index(f"<< {eof}")
        end = DTD.index(f"\n{eof}\n", start)
        body = DTD[start:end]
        assert RESET_SEQ in body, f"{eof} script must reset mouse modes on exit"
