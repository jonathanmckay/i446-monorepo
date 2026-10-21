#!/usr/bin/env python3
"""dtd ctrl-p (split) lives on the ctrl-o/ctrl-g detail+edit screen
(2026-10-21, "combine the 'more info / change name' view with the split
task view"). The edit line gains a trailing  ✓ <pts> [what I did]  segment;
ctrl-p from the list opens the edit screen with the marker pre-appended, and
ctrl-p inside the line appends it too. edit-fast applies the ordinary edits,
then prints one `SPLIT pts=… done_name=…` line that the edit script evals and
hands to the split script as arguments. split-form.py (the curses form,
2026-10-04) is gone.

Structural checks on dtd.sh's generated scripts, plus a pty run of the real
seed/widget lines around vared.
"""
import os
import pty
import re
import select
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _edit() -> str:
    m = re.search(r'cat > "\$DTD_EDIT" << EDITEOF\n(.*?)\nEDITEOF\n', DTD, re.S)
    assert m
    return m.group(1)


def _split() -> str:
    m = re.search(r"cat > \"\$DTD_SPLIT\" << 'SPLITEOF'\n(.*?)\nSPLITEOF", DTD, re.S)
    assert m
    return m.group(1)


def _unescape(t: str) -> str:
    return re.sub(r"\\([\\$`])", r"\1", t)


def test_ctrl_p_opens_the_edit_screen_seeded():
    bind = next(l for l in DTD.splitlines() if '--bind "ctrl-p:' in l)
    assert "DTD_SPLIT_SEED=1 $DTD_EDIT {2}" in bind, bind
    assert "$DTD_SPLIT" not in bind
    assert not (HERE / "split-form.py").exists()
    assert "DTD_SPLIT_PROMPT" not in DTD


def test_edit_screen_seeds_marker_binds_ctrl_p_and_dispatches_split():
    body = _edit()
    assert '[[ -n "\\${DTD_SPLIT_SEED:-}" ]] && f_line="\\$f_line ✓ "' in body
    assert "zle -N _dtd_split_seed" in body
    for km in ("emacs", "viins"):
        assert f"bindkey -M {km} '^P' _dtd_split_seed" in body
    # the widget binds come after the ^C ones (the ctrl-c test slices on vicmd ^C)
    assert body.index("bindkey -M vicmd '^C'") < body.index("_dtd_split_seed()")
    assert "grep '^SPLIT ' | tail -n 1" in body
    assert '"/tmp/dtd-$DTD_ID.split.sh" "\\$1" "\\$pts" "\\$done_name"' in body
    # header: the split script owns it on the split path, the edit summary otherwise
    assert body.index("rm -f") < body.index("_split_line=")
    assert 'else\n  echo "\\${out:-✗ edit failed}" > "\\$HDR"' in body
    assert "✓ pts-done what-you-did" in body


def test_split_script_takes_answers_as_arguments_and_never_prompts():
    body = _split()
    assert 'pts_today="${2:-}"; done_desc="${3:-}"; remaining_desc="${4:-}"; remaining_override="${5:-}"' in body
    assert "split-form" not in body.replace("# form (split-form.py, 2026-10-04)", "")
    assert "vared" not in body and "read -r pts_today" not in body
    assert '"${remaining_override:-}"' in body and "remaining_pts = int(sys.argv[12].strip())" in body
    assert "split cancelled" in body


# ── pty: the real seed + widget lines around a vared ─────────────────────────

def _script(seed: bool) -> str:
    body = _unescape(_edit()).splitlines()
    widget = [l for l in body if "_dtd_split_seed" in l and not l.startswith("#")]
    seed_line = next(l for l in body if l.startswith('[[ -n "${DTD_SPLIT_SEED:-}" ]]'))
    return "\n".join([
        "#!/bin/zsh", "zmodload zsh/zle", *widget,
        'f_line="write memo @i9 [20]"',
        seed_line,
        'vared -p "> " f_line < /dev/tty > /dev/tty',
        'print -r -- "LINE=<<$f_line>>"', "",
    ])


def _run(tmp_path, seed: bool, keys: list[str]) -> str:
    script = tmp_path / "edit.zsh"
    script.write_text(_script(seed))
    env = dict(os.environ, TERM="xterm-256color", LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
    if seed:
        env["DTD_SPLIT_SEED"] = "1"
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("zsh", ["zsh", "-f", str(script)], env)
    buf = b""

    def pump(t):
        nonlocal buf
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return
    pump(0.8)
    for k in keys:
        os.write(fd, k.encode())
        pump(0.25)
    pump(0.5)
    os.waitpid(pid, 0)
    m = re.search(rb"LINE=<<(.*?)>>", buf, re.S)
    assert m, buf[-400:]
    return m.group(1).decode(errors="replace")


def test_pty_ctrl_p_from_the_list_prefills_the_marker(tmp_path):
    assert _run(tmp_path, True, ["15", "\r"]) == "write memo @i9 [20] ✓ 15"


def test_pty_ctrl_p_inside_the_line_appends_the_marker_at_the_end(tmp_path):
    # cursor starts at the end of the prefill; ^P appends " ✓ " and typing continues after it
    assert _run(tmp_path, False, ["\x10", "5 did x", "\r"]) == "write memo @i9 [20] ✓ 5 did x"
    # from mid-line too: ^A to the start, then ^P still lands the marker at the END
    assert _run(tmp_path, False, ["\x01", "\x10", "7", "\r"]) == "write memo @i9 [20] ✓ 7"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
