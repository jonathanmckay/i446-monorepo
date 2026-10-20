#!/usr/bin/env python3
"""Regression: ctrl-c on dtd's ctrl-o/ctrl-g detail+edit screen cancels the
edit and returns to the list; it must never take dtd down with it.

Bug (2026-10-17, "ctrl-c from the details dialog on a task goes to the main
screen, not exit all the way"): the edit screen is an fzf `execute()` child.
fzf's Pause() hands the child the tty in cooked mode with ISIG on, and the
line discipline delivers SIGINT to the WHOLE foreground process group -- the
zsh action script AND fzf, its parent. fzf dies, so dtd exits.

Fix (dtd.sh, DTD_EDIT heredoc): undefine the tty intr/quit chars as the
first thing the script does (before the resolve step and the multi-second
task-detail fetch), repeat it on the `stty sane` line (sane resets intr),
and bind ^C inside vared to a widget that empties the buffer and accepts --
the existing blank-line "edit cancelled" path. The chars are restored right
after the tty drain, which both exit paths pass through.

Two layers: structural checks on the generated script's text, and a pty
harness that runs the real guard/restore lines around a vared with a Python
parent in the same process group standing in for fzz (it records whether it
received SIGINT). A control run without the guard proves the harness
reproduces the bug, so a silent harness cannot mask a regression.
"""
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
DTD = (_HERE / "dtd.sh").read_text()

DRAIN = "while read -t 0.05 -k 1 _discard 2>/dev/null; do : ; done < /dev/tty"


def _edit_heredoc() -> str:
    m = re.search(r'cat > "\$DTD_EDIT" << EDITEOF\n(.*?)\nEDITEOF\n', DTD, re.S)
    assert m, "DTD_EDIT heredoc not found in dtd.sh"
    return m.group(1)


def _unescape(heredoc_text: str) -> str:
    """What zsh does to an unquoted heredoc: \\$ -> $, \\\\ -> \\, \\` -> `."""
    return re.sub(r"\\([\\$`])", r"\1", heredoc_text)


# ── structural ───────────────────────────────────────────────────────────────

def test_intr_is_undefined_before_the_first_python_step():
    body = _edit_heredoc()
    guard = body.find("stty intr undef quit undef < /dev/tty")
    first_py = body.find("python3")
    assert guard != -1, "edit script no longer undefines the intr char"
    assert guard < first_py, "intr guard must precede the resolve/detail steps"


def test_stty_sane_in_edit_script_is_followed_by_intr_undef():
    # `stty sane` resets intr to ^C; the very next line must undefine it again.
    lines = _edit_heredoc().splitlines()
    idx = [i for i, l in enumerate(lines) if l.startswith("stty sane")]
    assert idx, "edit script lost its stty sane line"
    for i in idx:
        assert lines[i + 1].startswith("stty intr undef quit undef"), lines[i:i + 2]


def test_ctrl_c_widget_bound_in_every_vared_keymap():
    body = _edit_heredoc()
    assert '_dtd_cancel() { BUFFER=""; zle accept-line; }' in body
    assert "zle -N _dtd_cancel" in body
    for km in ("emacs", "viins", "vicmd"):
        assert f"bindkey -M {km} '^C' _dtd_cancel" in body, km


def test_intr_restored_after_drain_before_cancel_check():
    body = _edit_heredoc()
    drain = body.find(DRAIN)
    restore = body.find("stty intr '^C' quit")
    cancel = body.find('echo "edit cancelled"')
    assert -1 not in (drain, restore, cancel)
    assert drain < restore < cancel


# ── pty harness ──────────────────────────────────────────────────────────────

def _lines(prefix: str) -> list[str]:
    return [l for l in _unescape(_edit_heredoc()).splitlines() if l.startswith(prefix)]


def _guard_block() -> str:
    body = _unescape(_edit_heredoc()).splitlines()
    start = next(i for i, l in enumerate(body) if l.startswith("stty intr undef"))
    end = next(i for i, l in enumerate(body) if l.startswith("bindkey -M vicmd"))
    return "\n".join(body[start:end + 1])


def _script(guarded: bool) -> str:
    body = _unescape(_edit_heredoc()).splitlines()
    i = next(i for i, l in enumerate(body) if l.startswith("stty sane"))
    sane = "\n".join(body[i:i + 2]) if guarded else body[i]   # sane + its re-undef
    restore = _lines("stty intr '^C'")[0] if guarded else ""
    return "\n".join([
        "#!/bin/zsh",
        _guard_block() if guarded else "",
        sane,
        "sleep 0.6",              # stands in for the task-detail fetch
        'f_line="hello"',
        'vared -p "> " f_line < /dev/tty > /dev/tty',
        "rc=$?",
        "stty -echo < /dev/tty 2>/dev/null || true",
        DRAIN,
        restore,
        "print -- \"AFTER rc=$rc line=[$f_line] intr=$(stty -a < /dev/tty | grep -o 'intr = [^;]*')\"",
        "",
    ])


PARENT = r"""
import signal, subprocess, sys
got = []
signal.signal(signal.SIGINT, lambda *a: got.append(1))
rc = subprocess.call(["zsh", sys.argv[1]])
print(f"PARENT_SIGINT={len(got)} CHILD_RC={rc}", flush=True)
"""


def _run(tmp_path: Path, guarded: bool, ctrl_c_at: str) -> str:
    """ctrl_c_at: 'fetch' (during the sleep) or 'vared' (once the prompt shows)."""
    script = tmp_path / "edit.zsh"
    script.write_text(_script(guarded))
    parent = tmp_path / "parent.py"
    parent.write_text(PARENT)
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(sys.executable, [sys.executable, "-I", str(parent), str(script)])
    out = bytearray()

    def pump(until, stop=None):
        while time.time() < until:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    out.extend(os.read(fd, 4096))
                except OSError:
                    return
            if stop and stop in out:
                return

    if ctrl_c_at == "fetch":
        pump(time.time() + 0.3)
    else:
        pump(time.time() + 5, stop=b"> hello")
        assert b"> hello" in out, out.decode("utf-8", "replace")
    os.write(fd, b"\x03")
    pump(time.time() + 3, stop=b"CHILD_RC=")
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    return out.decode("utf-8", "replace")


needs_zsh = pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not on PATH")


@needs_zsh
def test_ctrl_c_in_vared_cancels_and_fzf_stand_in_gets_no_sigint(tmp_path):
    out = _run(tmp_path, guarded=True, ctrl_c_at="vared")
    assert "AFTER rc=0 line=[]" in out, out
    assert "intr=intr = ^C" in out, out          # restored for whoever is next
    assert "PARENT_SIGINT=0 CHILD_RC=0" in out, out


@needs_zsh
def test_ctrl_c_during_detail_fetch_still_cancels_cleanly(tmp_path):
    out = _run(tmp_path, guarded=True, ctrl_c_at="fetch")
    assert "AFTER rc=0 line=[]" in out, out
    assert "PARENT_SIGINT=0 CHILD_RC=0" in out, out


@needs_zsh
def test_control_without_guard_reproduces_the_bug(tmp_path):
    out = _run(tmp_path, guarded=False, ctrl_c_at="vared")
    assert "AFTER rc=0" not in out, out
    assert "PARENT_SIGINT=1" in out, out          # this is fzf dying
