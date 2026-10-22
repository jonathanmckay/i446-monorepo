#!/usr/bin/env python3
"""Regression (2026-10-22): "tried to change the project of a -1g and dtd
died."

Enter on the edit form's last field (project) saves and closes the form. An
Esc meant for the form then reached the bare list, where Esc quits dtd
(session 55312: form exit +5.0 s, back-abort +9.8 s). The edit script now
stamps /tmp/dtd-<id>.formexit when the form closes, and the back router
treats an Esc on the bare list within 10 s of that as a no-op with an
"esc again to quit" header, so only a second Esc quits.
"""
import os
import subprocess
import time
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _heredoc_block(var: str, eof: str) -> str:
    lines = DTD.split("\n")
    i0 = next(i for i, l in enumerate(lines) if l == f'cat > "${var}" << {eof}')
    i1 = next(i for i in range(i0, len(lines)) if lines[i] == eof)
    return "\n".join(lines[i0:i1 + 1])


def _back_script(tmp_path):
    """Generate back.sh exactly as dtd.sh does (unquoted heredoc expansion)."""
    sid = f"t{os.getpid()}{int(time.time() * 1000) % 100000}"
    env = dict(os.environ, DTD_ID=sid,
               DTD_BACK=str(tmp_path / "back.sh"),
               DTD_BLOCKPICK=str(tmp_path / "blockpick"),
               DTD_HDR=str(tmp_path / "hdr"),
               DTD_EVENTS=str(tmp_path / "events.tsv"),
               DTD_TIMING=str(tmp_path / "timing.log"))
    gen = (f'DTD_ID={sid}; DTD_BACK="$DTD_BACK"; DTD_BLOCKPICK="$DTD_BLOCKPICK"; '
           f'DTD_HDR="$DTD_HDR"; DTD_EVENTS="$DTD_EVENTS"\n'
           + _heredoc_block("DTD_BACK", "BACKEOF"))
    subprocess.run(["zsh", "-c", gen], env=env, check=True)
    return tmp_path / "back.sh", Path(f"/tmp/dtd-{sid}.formexit"), env


def _esc(script, env):
    return subprocess.run(["zsh", str(script), ""], env=env,
                          capture_output=True, text=True).stdout


def test_esc_right_after_edit_form_does_not_quit(tmp_path):
    script, stamp, env = _back_script(tmp_path)
    try:
        stamp.touch()
        out = _esc(script, env)
        assert out != "abort" and "transform-header" in out, out
        assert (tmp_path / "hdr").read_text().strip() == "esc again to quit dtd"
        # the stamp is consumed: a second Esc quits as before
        assert _esc(script, env) == "abort"
    finally:
        stamp.unlink(missing_ok=True)


def test_esc_long_after_edit_form_still_quits(tmp_path):
    script, stamp, env = _back_script(tmp_path)
    try:
        stamp.touch()
        old = time.time() - 30
        os.utime(stamp, (old, old))
        assert _esc(script, env) == "abort"
    finally:
        stamp.unlink(missing_ok=True)


def test_esc_with_no_edit_quits(tmp_path):
    script, stamp, env = _back_script(tmp_path)
    stamp.unlink(missing_ok=True)
    assert _esc(script, env) == "abort"


def test_edit_script_stamps_form_exit_on_both_paths():
    edit = _heredoc_block("DTD_EDIT", "EDITEOF")
    stamp_at = edit.index('.formexit"')
    # before the cancel/no-output early exit, so a cancelled edit stamps too
    assert stamp_at < edit.index('if [[ ! -s "\\$_out" ]]; then'), "stamp must precede the cancel branch"
