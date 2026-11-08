#!/usr/bin/env python3
"""Regression (2026-11-05 06:01, Straylight): "both dtd and janus are frozen
and looks like I'll have to manually kill the tab to restart."

The laptop woke with DNS down. ctrl-r's refresh.sh runs under fzf's
execute-silent (fzf takes no keys until it exits) and ran
`did-fast --refresh-cache` inline: up to 20 s waiting on the task-queue lock,
then Todoist reads that each sat ~30 s in getaddrinfo, with retries. dtd was
input-dead for minutes. (Its header read "🔄 refreshed" in JM's screenshot:
an earlier ctrl-r had finished; the next one hung.)

Fix: refresh.sh launches $DTD_BGREFRESH detached, waits at most ~3 s, and
returns; the background worker writes the header and reload-syncs fzf when
the refresh lands. One refresh at a time (pid file).

These tests render the two real heredocs out of dtd.sh with a stub did-fast
and time refresh.sh. Every stub process is killed in teardown.
"""
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _heredoc(var: str, tag: str) -> str:
    m = re.search(r'^cat > "\$%s" << %s\n.*?^%s\n' % (var, tag, tag), DTD, re.S | re.M)
    assert m, f"{tag} heredoc not found in dtd.sh"
    return m.group(0)


@pytest.fixture
def rig(tmp_path):
    stub = tmp_path / "did-fast-stub.py"
    marker = tmp_path / "stub.pids"
    env_pre = f"""
DTD_ID=t{os.getpid()}
DID_FAST={stub}
CACHE={tmp_path}/task-queue.json
DTD_CACHE_FILE={tmp_path}/cache.json
DTD_HDR={tmp_path}/hdr
DTD_PORT={tmp_path}/no-such-port
DTD_DOMAIN={tmp_path}/domain
DTD_EVENTS={tmp_path}/events
DTD_LIST=/bin/true DTD_DONE_FILE=x DTD_REMOVED=x DTD_SKIPPED=x DTD_TIMER=x DTD_VIEW=x DTD_BLOCKPICK=x
DTD_HDRGEN=/bin/true
DTD_BGREFRESH={tmp_path}/bgrefresh.sh
DTD_REFRESH={tmp_path}/refresh.sh
"""
    gen = tmp_path / "gen.zsh"
    gen.write_text(env_pre + _heredoc("DTD_BGREFRESH", "BGREFRESHEOF")
                   + 'chmod +x "$DTD_BGREFRESH"\n'
                   + _heredoc("DTD_REFRESH", "REFRESHEOF") + 'chmod +x "$DTD_REFRESH"\n')
    subprocess.run(["zsh", str(gen)], check=True, timeout=10)
    (tmp_path / "task-queue.json").write_text('{"fresh": true}')

    def set_stub(seconds, rc=0):
        stub.write_text(
            "import os, sys, time\n"
            f"open({str(marker)!r}, 'a').write(str(os.getpid()) + '\\n')\n"
            f"time.sleep({seconds})\n"
            f"sys.exit({rc})\n")

    def run_refresh():
        t0 = time.monotonic()
        subprocess.run([str(tmp_path / "refresh.sh")], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        return time.monotonic() - t0

    yield tmp_path, set_stub, run_refresh
    # Teardown: never leak a harness process (v179 found 53 on ix).
    if marker.exists():
        for pid in marker.read_text().split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def _wait_for(pred, secs=8.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_ctrl_r_returns_fast_when_refresh_hangs(rig):
    """The 11-05 case: did-fast stalls on a dead network. fzf must get its
    keys back within the ~3 s grace, with an honest header."""
    tmp, set_stub, run_refresh = rig
    set_stub(30)
    took = run_refresh()
    assert took < 6, f"refresh.sh blocked fzf for {took:.1f}s with the network down"
    assert "still running" in (tmp / "hdr").read_text()
    assert (tmp / "bgrefresh.sh.pid").exists()


def test_ctrl_r_mashed_offline_does_not_stack_refreshes(rig):
    tmp, set_stub, run_refresh = rig
    set_stub(30)
    run_refresh()
    run_refresh()
    assert run_refresh() < 2, "a second ctrl-r while one is in flight must return at once"
    pids = (tmp / "stub.pids").read_text().split()
    assert len(pids) == 1, f"{len(pids)} did-fast refreshes stacked up"


def test_ctrl_r_online_still_refreshes_inline(rig):
    """A healthy refresh lands inside the grace: cache copied, header says so."""
    tmp, set_stub, run_refresh = rig
    set_stub(0.3)
    run_refresh()
    assert _wait_for(lambda: (tmp / "hdr").read_text().startswith("🔄 refreshed"))
    assert (tmp / "cache.json").read_text() == '{"fresh": true}'
    assert not (tmp / "bgrefresh.sh.pid").exists()


def test_failed_refresh_says_so(rig):
    tmp, set_stub, run_refresh = rig
    set_stub(0.1, rc=1)
    run_refresh()
    assert _wait_for(lambda: "refresh failed" in (tmp / "hdr").read_text())
    assert not (tmp / "cache.json").exists(), "a failed refresh must not copy the cache"


def test_refresh_script_has_no_inline_did_fast():
    blk = _heredoc("DTD_REFRESH", "REFRESHEOF")
    assert '"$DID_FAST"' not in blk, "ctrl-r must not run did-fast inline under execute-silent"
