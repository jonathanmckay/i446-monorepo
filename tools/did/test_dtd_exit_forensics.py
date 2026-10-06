#!/usr/bin/env python3
"""dtd records WHY it exited (2026-10-06, "now dtd is randomly exiting?").

A clean exit (fzf returned nothing) left no trace in the timing log, so a
stray-Esc abort, an fzf error, and a deliberate quit were indistinguishable.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_fzf_exit_code_logged_before_break():
    m = re.search(r'fzf_output=\$\(eval .*?\n  _fzf_rc=\$\?\n  task="\$fzf_output"', DTD, re.S)
    assert m, "_fzf_rc must capture fzf's status immediately after the picker returns"
    blk = DTD[DTD.index('if [[ -z "$task" ]]; then', m.end()):][:400]
    assert 'fzf-exit\\trc=$_fzf_rc' in blk and blk.index("fzf-exit") < blk.index("break")


def test_back_router_logs_abort():
    s = DTD.index('cat > "$DTD_BACK"')
    body = DTD[s:DTD.index("BACKEOF", s + 30)]
    assert "back-abort" in body and body.index("back-abort") < body.index("print -n abort")
