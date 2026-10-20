#!/usr/bin/env python3
"""Regression (2026-09-03): "dtd is locked on the text input screen, and I
can't navigate items in the fzf".

Root cause: fzf leaves the alternate screen for execute(), but what the
terminal shows then is not guaranteed -- cmux keeps the STALE fzf frame on
screen, so a `read ... < /dev/tty` prompt is invisible and arrow keys land
in the blind read (which doesn't interpret them as navigation) instead of
fzf's list. This was diagnosed and fixed for done.sh's value-prompt (cpap /
xk20 / xk22 / xk26 / i444 / hiit / ... completion) on 2026-07-21 via
`stty sane` + a clear-to-home escape (\033[2J\033[H) before the prompt, but
the identical `read < /dev/tty` pattern in the ctrl-d defer prompt
(DEFEREOF) and the ctrl-g edit prompt (EDITEOF) never got the same fix --
so pressing either key still reproduced the exact "locked, can't navigate"
symptom.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()

STTY_SANE = "stty sane < /dev/tty"
CLEAR_HOME = r"\033[2J\033[H"

# Every heredoc that opens an interactive `read ... < /dev/tty` prompt while
# fzf is suspended in execute() must make that prompt actually visible.
PROMPT_SCRIPTS = ("DONEEOF", "DEFEREOF", "EDITEOF")


def _heredoc(eof: str) -> str:
    m = re.search(rf"<< '?{eof}'?\n", DTD)
    assert m, f"heredoc {eof} not found"
    end = DTD.index(f"\n{eof}\n", m.start())
    return DTD[m.start():end]


def test_prompt_scripts_actually_have_a_read_prompt():
    # Sanity: confirm these are still the prompt-bearing scripts we think
    # they are, so this test can't silently stop covering anything.
    for eof in PROMPT_SCRIPTS:
        body = _heredoc(eof)
        assert "read " in body and "/dev/tty" in body, \
            f"{eof} no longer looks like an interactive tty-read prompt"


def test_prompt_scripts_force_sane_tty_before_reading():
    for eof in PROMPT_SCRIPTS:
        body = _heredoc(eof)
        read_pos = body.index("read ")
        pre = body[:read_pos]
        assert STTY_SANE in pre, (
            f"{eof} must `stty sane < /dev/tty` before its read prompt, or "
            "a raw mode inherited from fzf can leave Enter not terminating "
            "the read")


def test_prompt_scripts_clear_stale_fzf_frame_before_reading():
    for eof in PROMPT_SCRIPTS:
        body = _heredoc(eof)
        read_pos = body.index("read ")
        pre = body[:read_pos]
        assert CLEAR_HOME in pre, (
            f"{eof} must clear to home (\\033[2J\\033[H) before its read "
            "prompt -- otherwise cmux can leave the stale fzf frame on "
            "screen, the prompt is invisible, and arrow keys land in the "
            "blind read instead of navigating fzf's list")


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
