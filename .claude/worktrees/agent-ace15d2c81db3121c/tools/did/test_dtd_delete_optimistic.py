"""ctrl-x was slow (user report 2026-10-07): the delete script ran the
pre-image GET + DELETE (0.3-1s each from Ix) inline under fzf's
execute-silent, freezing dtd 1-2s per task. It must now hide the row by id at
once and do the Todoist work in a detached worker, rolling the hide back if
the DELETE fails."""
import json
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LINES = (HERE / "dtd.sh").read_text().splitlines()


def _gen(tmp: Path, http_code: str):
    s = next(i for i, l in enumerate(LINES) if l.startswith('cat > "$DTD_DELETE"'))
    e = next(i for i in range(s, len(LINES)) if LINES[i] == "DELETEEOF")
    bindir = tmp / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(f'#!/bin/zsh\nsleep 2\n[[ "$*" == *"-X DELETE"* ]] && print -n {http_code} || print -n "{{}}"\n')
    curl.chmod(0o755)
    cache = tmp / "cache.json"
    cache.write_text(json.dumps({"today": [{"id": "T1", "content": "junk task (5) [1]", "labels": []}]}))
    (tmp / "removed.ids").write_text("")
    out = tmp / "delete.sh"
    gen = (f'#!/bin/zsh\nDTD_HDR="{tmp}/hdr"; DTD_CACHE_FILE="{cache}"; DTD_REMOVED="{tmp}/removed"; '
           f'DTD_JOURNAL="{tmp}/journal"; UNDO_FAST="{HERE}/undo-fast.py"; '
           f'DTD_RESOLVE="{HERE}/dtd_resolve.py"; DTD_DELETE="{out}"\n'
           + "\n".join(LINES[s:e + 1]) + "\n")
    (tmp / "gen.zsh").write_text(gen)
    subprocess.run(["zsh", str(tmp / "gen.zsh")], check=True)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    return out, env


def _wait_for(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_ctrl_x_returns_before_todoist_and_hides_row(tmp_path):
    script, env = _gen(tmp_path, "204")
    t0 = time.time()
    subprocess.run(["zsh", str(script), "T1"], env=env, timeout=10)  # exit code: no /dev/tty here
    assert time.time() - t0 < 1.0, "ctrl-x must not wait on Todoist (fake curl sleeps 2s)"
    assert (tmp_path / "removed.ids").read_text().split() == ["T1"]
    assert _wait_for(lambda: "Deleted" in (tmp_path / "hdr").read_text())
    assert (tmp_path / "removed.ids").read_text().split() == ["T1"]


def test_failed_delete_restores_the_row(tmp_path):
    script, env = _gen(tmp_path, "500")
    subprocess.run(["zsh", str(script), "T1"], env=env, timeout=10)  # exit code: no /dev/tty here
    assert (tmp_path / "removed.ids").read_text().split() == ["T1"]
    assert _wait_for(lambda: "restored" in (tmp_path / "hdr").read_text())
    assert (tmp_path / "removed.ids").read_text().split() == []
