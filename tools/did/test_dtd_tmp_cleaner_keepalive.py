#!/usr/bin/env python3
"""Regression (2026-11-14): "I can't complete tasks on dtd?" + "it also has the
restart warning even though I restarted".

Root cause: macOS removes files under /tmp whose atime, mtime and ctime are
all older than 3 days. A dtd session left open on Ix since Nov 9 (attached
from the phone; no keystrokes on this host after Nov 11) lost every baked
action script -- done.sh, done-router.sh, enter.sh, form.sh, $DTD_HDR, the
hide files -- while list.sh / hdrgen / the cache snapshot, which every
repaint reads or rewrites, survived. fzf's execute()/transform() on a missing
path fails silently, so alt-enter (and everything else) did nothing. The
"restart" was a mosh reconnect: the launcher's `tmux new -A -s dtd`
re-attaches to the live session, so the stale-code header was right.

Fix: the watcher refreshes atime (touch -a) on /tmp/dtd-$DTD_ID.* hourly, and
hdrgen names the cleaned state (and the real restart recipe) in red.
"""
import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _stale_mod():
    spec = importlib.util.spec_from_file_location("stale_hdr", HERE / "test_dtd_stale_code_header.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ── keep-alive in the watcher ────────────────────────────────────────────────

# the array form: a bare `touch -a glob(N)` with no match runs touch with no
# args and exits 1 (caught by the functional test below)
KEEPALIVE = '_ka=(/tmp/dtd-$DTD_ID.*(N)); (( ${#_ka} )) && touch -a "${_ka[@]}" 2>/dev/null'


def test_watcher_touches_runtime_files_hourly_access_time_only():
    assert KEEPALIVE in DTD, "watcher must refresh atime on every baked /tmp/dtd-<id>.* file"
    loop = DTD.index('while [[ -f "$DTD_PORT" ]]; do')
    ka = DTD.index(KEEPALIVE)
    assert loop < ka, "the keep-alive must run inside the watcher's main loop"
    gate = DTD[DTD.rindex("if ((", 0, ka):ka]
    assert "last_keepalive" in gate and "3600" in gate, "hourly gate, not every 0.5s tick"
    assert "touch \"${_ka[@]}\"" not in DTD and "touch /tmp/dtd-$DTD_ID" not in DTD, (
        "must be touch -a: bumping mtime would trip mtime-keyed logic (.formexit 10s window)")


def test_keepalive_command_advances_atime_and_leaves_mtime(tmp_path):
    """Run the exact command dtd uses against fixtures whose atime is a week
    old: atime moves to now, mtime stays. (-a alone defeats the cleaner, which
    requires atime AND mtime AND ctime to all be stale.)"""
    old = time.time() - 7 * 86400
    files = [tmp_path / f"dtd-X.{suf}" for suf in ("done.sh", "done-router.sh", "hdr", "removed.ids")]
    for f in files:
        f.write_text("x")
        os.utime(f, (old, old))
    cmd = KEEPALIVE.replace("/tmp/dtd-$DTD_ID.", f"{tmp_path}/dtd-X.")
    subprocess.run(["zsh", "-c", cmd], check=True, timeout=10)
    for f in files:
        st = f.stat()
        assert st.st_atime > time.time() - 60, f
        assert abs(st.st_mtime - old) < 2, f"mtime must be untouched: {f}"
    # (N) + the array gate: no match prints nothing and creates nothing (a bare
    # `touch -a glob(N)` would run touch with no args and print its usage).
    # The `(( 0 )) &&` short-circuit leaves status 1, which the watcher ignores.
    r = subprocess.run(["zsh", "-c", KEEPALIVE.replace("/tmp/dtd-$DTD_ID.", f"{tmp_path}/none.")],
                       capture_output=True, text=True, timeout=10)
    assert r.stderr == "" and not list(tmp_path.glob("none.*"))


# ── hdrgen names the cleaned state and the real restart recipe ───────────────

def test_hdrgen_checks_the_baked_router_still_exists():
    body = _stale_mod()._hdrgen_body()
    assert '[[ ! -x "$DTD_DONE_ROUTER" ]]' in body
    assert "cleaned from /tmp" in body
    # after the code-stale check, before the normal header composition
    assert body.index("RESTART DTD") < body.index('"$DTD_DONE_ROUTER"') < body.index('ws=""; [[ -r "$DTD_HDR" ]]')


def test_missing_router_shows_red_restart_header(tmp_path):
    m = _stale_mod()
    now = time.time()
    out = m._run_hdrgen(tmp_path, self_mtime=now, live_mtime=now,
                        env_extra={"ROUTER": str(tmp_path / "gone-router.sh")})
    assert "RESTART DTD" in out and "cleaned from /tmp" in out and "\033[1;91m" in out
    assert "enter: start" not in out, "must replace the whole header, like the stale-code warning"


def test_present_router_and_fresh_code_show_normal_header(tmp_path):
    m = _stale_mod()
    now = time.time()
    out = m._run_hdrgen(tmp_path, self_mtime=now, live_mtime=now, dtd_hdr="✓ done\n")
    assert "RESTART" not in out and "enter: start" in out


def test_restart_warnings_say_how_to_actually_restart():
    """A mosh/ssh reconnect runs `tmux new -A -s dtd`, which RE-ATTACHES; JM
    read that as a restart. Both red headers must carry the real recipe."""
    body = _stale_mod()._hdrgen_body()
    msgs = re.findall(r"RESTART DTD[^\\]*", body)
    assert len(msgs) == 2, msgs
    for msg in msgs:
        assert "esc to quit, then relaunch" in msg, msg
