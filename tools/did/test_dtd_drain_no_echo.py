#!/usr/bin/env python3
"""Regression: the tty-input drain in dtd's action scripts must not ECHO the
bytes it discards.

Bug (2026-09-24, "when dtd is busy and I give entry, the input box goes
weird"): the list rows and the input box filled with suffix fragments of SGR
click events -- `;18;19M[<0;18;19M<0;18;19M0;18;19M;18;19M...` -- after an
enter/ctrl-s START (two Toggl calls, >1s).

Mechanism (fzf 0.72 src/terminal.go executeCommand + tui/light.go):
  * execute-silent / transform / reload block fzf's input goroutine. After
    `blockDuration` (1s) fzf calls Pause(false): disableMouse + Restore() the
    tty to its ORIGINAL cooked+ECHO state. Resume(false) afterwards only
    re-applies raw mode and reprints the prompt line -- no full redraw.
  * Clicks that landed in the first second were queued raw. On the switch to
    canonical mode they become PENDIN input, re-processed by the line
    discipline on the next read / mode change -- WITH echo.
  * The drain loop `while read -t 0.05 -k 1 _discard` flips the tty
    raw->cooked->raw for EVERY byte (zsh's `read -k` sets raw and restores),
    so the unread tail is re-processed and re-echoed once per byte: 3 clicks
    (36 bytes) painted ~100 fragments straight over fzf's frame.

Fix: `stty -echo < /dev/tty` immediately before each drain loop. Echo is
off for the re-processing, the loop still consumes everything, and fzf's
Resume() -> MakeRaw() owns the tty state afterwards anyway.

Two layers here: a structural check that every drain site carries the stty
line, and a pty harness that models fzf's raw -> Pause(cooked+echo) handoff
with queued clicks and asserts the real drain block from dtd.sh echoes
nothing. A control test proves the harness reproduces the bug with the
pre-fix drain, so a silent harness cannot mask a regression.
"""
import os
import pty
import re
import select
import termios
import time
import tty
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()

DRAIN = "while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty"
STTY_NOECHO = "stty -echo < /dev/tty"
CLICK = b"\x1b[<0;18;19M"  # SGR left-button press, col 18 row 19 (from the screenshot)

# The pre-fix drain block, kept verbatim as the harness control.
OLD_DRAIN = (
    "printf '\\033[?1002l\\033[?1003l\\033[?1000h\\033[?1006h' > /dev/tty 2>/dev/null || true\n"
    + DRAIN + "\n"
)


# ── structural: every drain site has echo turned off first ───────────────────

def test_every_drain_loop_is_preceded_by_stty_noecho():
    lines = DTD.split("\n")
    sites = [i for i, ln in enumerate(lines) if ln.strip() == DRAIN]
    assert len(sites) >= 14, f"expected the 14 known drain sites, found {len(sites)}"
    missing = [i + 1 for i in sites if STTY_NOECHO not in lines[i - 1]]
    assert not missing, f"drain loops without a preceding `{STTY_NOECHO}` at dtd.sh lines {missing}"


def test_stty_noecho_never_stands_alone():
    # The stty line only makes sense right before a drain; a stray one would
    # mean a drain loop got deleted/moved without its guard.
    lines = DTD.split("\n")
    for i, ln in enumerate(lines):
        if STTY_NOECHO in ln:
            assert lines[i + 1].strip() == DRAIN, f"stty -echo at line {i+1} not followed by the drain loop"


# ── behavioral: pty harness modelling fzf's Pause(false) handoff ─────────────

def _drain_block_from_dtd() -> str:
    """The fixed drain block exactly as STARTEOF (enter/ctrl-s path) runs it."""
    m = re.search(r"printf '\\033\[\?1002l[^\n]*\n" + re.escape(STTY_NOECHO) + r"[^\n]*\n" + re.escape(DRAIN) + r"\n", DTD)
    assert m, "fixed drain block not found in dtd.sh"
    return m.group(0)


def _run_in_pty(drain_block: str, clicks: int = 3) -> tuple[bytes, str]:
    """Child: raw tty (fzf running) -> clicks queue -> cooked+echo (fzf
    Pause(false) after blockDuration) -> exec the drain -> probe leftovers.
    Parent: the terminal emulator; returns (everything the child painted,
    'CLEAN'|'LEFTOVER')."""
    script = drain_block + (
        "if read -t 0.3 -k 1 _x 2>/dev/null < /dev/tty; then echo LEFTOVER; else echo CLEAN; fi\n"
    )
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover - child
        orig = termios.tcgetattr(0)
        tty.setraw(0)
        time.sleep(0.3)
        termios.tcsetattr(0, termios.TCSANOW, orig)
        os.execvp("zsh", ["zsh", "-c", script])
    time.sleep(0.1)
    for _ in range(clicks):
        os.write(fd, CLICK)
    out = b""
    deadline = time.time() + 2.5
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if not r:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    os.close(fd)
    verdict = "CLEAN" if b"CLEAN" in out else ("LEFTOVER" if b"LEFTOVER" in out else "UNKNOWN")
    return out, verdict


def test_harness_reproduces_bug_with_old_drain():
    # Control: the pre-fix drain must paint the click fragments, otherwise the
    # harness isn't exercising the echo path and the test below proves nothing.
    out, verdict = _run_in_pty(OLD_DRAIN)
    assert out.count(b";18;19") > 3, f"harness failed to reproduce the echo storm: {out!r}"
    assert verdict == "CLEAN"


def test_fixed_drain_paints_nothing_and_consumes_everything():
    out, verdict = _run_in_pty(_drain_block_from_dtd())
    painted = out.replace(b"CLEAN\r\n", b"").replace(b"CLEAN\n", b"")
    assert b";18;19" not in painted and b"\x1b[<" not in painted, f"drain echoed click bytes: {out!r}"
    assert verdict == "CLEAN", f"drain left bytes queued for fzf: {out!r}"
