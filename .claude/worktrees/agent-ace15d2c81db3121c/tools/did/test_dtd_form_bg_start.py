#!/usr/bin/env python3
"""Regression: DTD_FORM must start the task timer in the BACKGROUND.

Bug (2026-10-18, "when I hit enter on -1g (and presumably other surveys) it
flashes through a bunch of screens and is not fast"): the form wrapper ran
DTD_ENTER -> start.sh synchronously -- ~4 python spawns plus two serial Toggl
HTTP calls -- holding a blank cleared screen for seconds before the
prompt_toolkit form painted.

Fix shape (three parts, all structural here):
  * FORMEOF backgrounds the enter call with `&!` (zsh HUP applies to
    non-interactive shells: a fast Esc exiting the wrapper would SIGHUP a
    plain `&` job mid-start and the timer would silently never start) and
    redirects stdin from /dev/null (the bg job must never read the tty the
    form owns -- SIGTTIN stops it silently otherwise).
  * STARTEOF honors DTD_SKIP_TTY_DRAIN with an early exit placed BEFORE its
    /dev/tty block -- the drain loop would eat form keystrokes and the mouse
    reset would kill the form's mouse support if it ran under the form.
  * The cleared screen shows a one-line placeholder while python starts.
"""
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()


def _heredoc_body(name: str) -> str:
    m = re.search(r"<<\s*" + name + r"\n(.*?)\n" + name + r"\n", DTD, re.S)
    assert m, f"{name} heredoc not found in dtd.sh"
    return m.group(1)


FORM = _heredoc_body("FORMEOF")
START = _heredoc_body("STARTEOF")

BG_ENTER = 'DTD_SKIP_TTY_DRAIN=1 "$DTD_ENTER" "\\$id" </dev/null >/dev/null 2>&1 &!'
SKIP_GUARD = '[[ -n "\\$DTD_SKIP_TTY_DRAIN" ]] && exit 0'


def test_form_wrapper_backgrounds_enter_with_disown_and_null_stdin():
    assert BG_ENTER in FORM, (
        "FORMEOF must run DTD_ENTER backgrounded with DTD_SKIP_TTY_DRAIN=1, "
        "</dev/null and &! -- a synchronous start holds a blank screen for "
        "the two Toggl calls before the form paints"
    )


def test_form_wrapper_never_runs_enter_synchronously():
    for ln in FORM.split("\n"):
        if '"$DTD_ENTER"' in ln:
            assert "&!" in ln, f"synchronous DTD_ENTER call in FORMEOF: {ln!r}"


def test_start_skip_guard_exists_and_precedes_tty_block():
    assert SKIP_GUARD in START, (
        "STARTEOF must exit early on DTD_SKIP_TTY_DRAIN -- its drain loop "
        "steals keystrokes from a concurrently running form otherwise"
    )
    mouse_reset = START.index("printf '\\033[?1002l")
    assert START.index(SKIP_GUARD) < mouse_reset, (
        "the DTD_SKIP_TTY_DRAIN guard must come BEFORE the mouse-reset/stty/"
        "drain block, or the bg start touches the form's tty before exiting"
    )


def test_start_skip_guard_is_unindented_single_line():
    # test_dtd_drain_no_echo.py anchors the drain block lines at column 0; an
    # if...fi wrapper (indenting them) would silently break those regexes.
    assert "\n" + SKIP_GUARD + "\n" in START, "guard must be a bare column-0 line"


def test_form_clear_paints_a_placeholder():
    assert re.search(r'printf "\\033\[2J\\033\[H[^"]+" > /dev/tty', FORM), (
        "the clear-screen printf should include a placeholder so the ~1s of "
        "python/prompt_toolkit startup does not look like a dead terminal"
    )
