#!/usr/bin/env python3
"""Regression: after a task split (ctrl-p), touchpad-scroll arrows must not
flood fzf's query.

Bug (2026-07-14): the split action asks 3 questions via osascript GUI dialogs.
While those dialogs hold focus the terminal is idle and buffers two-finger
touchpad scroll as ESC[A/ESC[B arrow bursts; on return fzf dumped the whole
buffer into its query as literal ^[[A^[[B text. Fix: the split script drains the
tty input buffer (and resets mouse modes) before handing control back to fzf.
"""
import re
from pathlib import Path

DTD = Path(__file__).resolve().parent / "dtd.sh"
SRC = DTD.read_text()


def _split_script() -> str:
    m = re.search(r"cat > \"\$DTD_SPLIT\" << 'SPLITEOF'\n(.*?)\nSPLITEOF", SRC, re.S)
    assert m, "split script block not found"
    return m.group(1)


def test_split_drains_tty_input_buffer():
    body = _split_script()
    assert "< /dev/tty" in body, "must read from the controlling terminal"
    assert re.search(r"while read -t [0-9.]+ -k 1 .*; do .* done < /dev/tty", body), (
        "split must drain buffered tty input (scroll-arrow burst) before fzf resumes")


def test_split_resets_mouse_modes_like_siblings():
    # Current sequence (2026-07-15 design): motion OFF, click+SGR scroll ON —
    # matching fzf's own subscription (this fzf build errors on --no-mouse,
    # so all-off `1000l/1006l` is no longer the spec).
    body = _split_script()
    assert "1002l" in body and "1003l" in body, (
        "split must strip motion modes like the defer/edit/done action scripts")
    assert "1000h" in body and "1006h" in body, (
        "split must restore fzf's click+scroll subscription")


def test_drain_runs_after_the_dialogs():
    body = _split_script()
    assert body.index("display dialog") < body.index("< /dev/tty"), (
        "the drain must run after the GUI dialogs, not before")


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
