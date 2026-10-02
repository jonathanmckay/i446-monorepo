#!/usr/bin/env python3
"""Regression (2026-10-02): ctrl-d (defer) and ctrl-v/ctrl-k (block snooze)
were slow for the same reason alt-enter was: a chain of process spawns ran
synchronously inside fzf's binding before anything changed on screen, and
each spawn is a chance for an exec-scan stall on this machine.

  * defer.sh resolved EVERY marked id with its own python3 + sed (~0.2s per
    task; 0.6s for three, 1.2s under load) before the prompt could appear.
  * blockarm.sh ran a python3 option-count script, then python3 resolve +
    sed for the label, before the picker could appear.

Fix: both scripts do all of that in ONE jq pass (measured: defer 3 ids
0.03-0.05s vs 0.2-1.2s; blockarm 0.05s). ctrl-d also leads its post-prompt
chain with fzf's `exclude-multi` so the deferred rows vanish instantly, and
all three bindings use `reload-sync` so the list is not blanked while the
regen runs. The generated scripts are exercised through real zsh heredocs
(not a hand-simulated expansion), since the heredoc's backslash rules are
exactly where a jq-in-heredoc rewrite goes wrong.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
LINES = DTD.split("\n")


def _heredoc(var, eof):
    i0 = next(i for i, l in enumerate(LINES) if l == f'cat > "${var}" << {eof}')
    i1 = next(i for i in range(i0, len(LINES)) if LINES[i] == eof)
    return "\n".join(LINES[i0 + 1:i1])


def _bind(key):
    m = re.search(rf'--bind "{re.escape(key)}:[^"]*"', DTD)
    assert m, key
    return m.group(0)


# --- structural ------------------------------------------------------------

def test_defer_resolves_all_ids_in_one_jq_pass():
    body = _heredoc("DTD_DEFER", "DEFEREOF")
    body = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith("#"))
    pre_prompt = body[:body.index("read days < /dev/tty")]
    assert "$DTD_RESOLVE" not in pre_prompt and "python3" not in pre_prompt
    assert "sed -E" not in pre_prompt
    assert re.search(r"jq -r --args '", pre_prompt) and "| @tsv'" in pre_prompt
    assert '"\\${_want[@]}" < "$DTD_CACHE_FILE"' in pre_prompt, "ids via --args, cache via stdin"


def test_blockarm_is_a_single_jq_pass():
    body = _heredoc("DTD_BLOCKARM", "ARMEOF")
    code = "\n".join(l for l in body.split("\n") if not l.lstrip().startswith("#"))
    assert "python3" not in code and "$DTD_RESOLVE" not in code and "sed -E" not in code
    assert "PYCOUNT" not in code
    assert "now | localtime" in code, "option count must still be hour-aware"
    assert "--slurpfile sn" in code and "| @tsv'" in code
    assert "n=3; clean=" in code, "jq failure must leave the picker usable"
    # pinned by test_dtd_id_based_ops: the arm file write is unchanged
    assert r"""printf '%s\n' "\$@" > "\$BLOCKPICK\"""" in body


def test_ctrl_d_excludes_then_reload_syncs():
    b = _bind("ctrl-d")
    assert "execute($DTD_DEFER {+2})+exclude-multi+deselect-all+reload-sync($DTD_RELOAD)" in b, b


def test_block_arm_bindings_reload_sync():
    for k in ("ctrl-v", "ctrl-k"):
        b = _bind(k)
        assert f"execute-silent($DTD_BLOCKARM {{+2}})+deselect-all+reload-sync($DTD_RELOAD)" in b, b


# --- functional: real zsh heredoc generation ---------------------------------

TASKS = [
    {"id": "A1", "content": "review PR for auth (20) [15]"},
    {"id": "B2", "content": "xk26 9.22 (10) [10]"},
    {"id": "C3", "content": "a very long name that got… truncated (5) [5]"},
    {"id": "D4", "content": "it's (weird) [10] $x; y) {20}"},
]


def _generate(tmp, dtd_id):
    cache = tmp / "cache.json"
    cache.write_text(json.dumps({"updated": "t", "today": TASKS, "关键路径": []}))
    stub = tmp / "stub.py"
    stub.write_text('import json;print(json.dumps({"target_date":"2026-10-03","claimed_points":1,"remaining_points":9}))')

    def block(var, eof):
        s = next(i for i, l in enumerate(LINES) if l.startswith(f"{var}="))
        e = next(i for i in range(s, len(LINES)) if LINES[i].strip() == eof)
        return "\n".join(LINES[s:e + 1])
    setup = (f'#!/bin/zsh\nDTD_ID="{dtd_id}"; DTD_HDR="{tmp}/hdr"; DTD_REMOVED="{tmp}/removed"; '
             f'DTD_PUSHED="{tmp}/pushed"; DTD_PROCESSED="{tmp}/processed"; UNDO_FAST="/usr/bin/true"; '
             f'DTD_JOURNAL="{tmp}/journal"; DTD_CACHE_FILE="{cache}"; DTD_RESOLVE="/nonexistent"; '
             f'DTD_BLOCKPICK="{tmp}/blockpick"; STATE_DIR="{tmp}"\n')
    (tmp / "gen.zsh").write_text(setup + block("DTD_DEFER", "DEFEREOF") + "\n" + block("DTD_BLOCKARM", "ARMEOF") + "\n")
    subprocess.run(["zsh", str(tmp / "gen.zsh")], check=True, capture_output=True)
    for f in ("hdr", "removed", "pushed", "processed", "journal", "blockpick", "removed.ids"):
        (tmp / f).write_text("")
    d = Path(f"/tmp/dtd-{dtd_id}.defer.sh")
    d.write_text(d.read_text().replace('DEFER_FAST="$HOME/i446-monorepo/tools/did/defer-fast.py"', f'DEFER_FAST="{stub}"'))
    return d, Path(f"/tmp/dtd-{dtd_id}.blockarm.sh")


def _env():
    return {k: v for k, v in os.environ.items() if k != "DTD_DEFER_PROMPT"}


def test_defer_batch_resolves_every_id_fast_and_skips_picker_rows():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        d, a = _generate(tmp, "tdefjq")
        try:
            t0 = time.time()
            subprocess.run(["zsh", str(d), "A1", "C3", "D4", "nope", "BLOCK:戌"], env=_env(), timeout=10, capture_output=True)
            assert time.time() - t0 < 1.0
            time.sleep(1.0)
            assert set((tmp / "removed.ids").read_text().split()) == {"A1", "C3", "D4", "nope"}
            assert (tmp / "processed").read_text().count("x") == 4
            hdr = (tmp / "hdr").read_text()
            assert "nope" in hdr, "unknown id falls back to itself as the name"
        finally:
            d.unlink(); a.unlink()


def test_defer_label_strips_annotations_and_ellipsis_tail():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        d, a = _generate(tmp, "tdefjq2")
        try:
            subprocess.run(["zsh", str(d), "C3"], env=_env(), timeout=10, capture_output=True)
            time.sleep(0.8)
            hdr = (tmp / "hdr").read_text()
            assert "a very long name that got" in hdr and "…" not in hdr and "(5)" not in hdr, hdr
            subprocess.run(["zsh", str(d), "D4"], env=_env(), timeout=10, capture_output=True)
            time.sleep(0.8)
            assert "it's (weird) $x; y)" in (tmp / "hdr").read_text(), "metachars survive the jq pass"
        finally:
            d.unlink(); a.unlink()


def test_blockarm_label_count_and_missing_snooze_file():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        d, a = _generate(tmp, "tarmjq")
        try:
            t0 = time.time()
            subprocess.run(["zsh", str(a), "C3", "A1"], timeout=10, capture_output=True)
            assert time.time() - t0 < 1.0
            assert (tmp / "blockpick").read_text().split() == ["C3", "A1"]
            hdr = (tmp / "hdr").read_text()
            assert "⏰ delay a very long name that got +1 more until" in hdr, hdr
            assert json.loads((tmp / "dtd-block-snooze.json").read_text()) == {}, "missing snooze file is created empty, not fatal"
            subprocess.run(["zsh", str(a), "B2"], timeout=10, capture_output=True)
            assert "⏰ delay xk26 9.22 until" in (tmp / "hdr").read_text(), "dated copy keeps its stamp in the label"
            subprocess.run(["zsh", str(a), "BLOCK:戌"], timeout=10, capture_output=True)
            assert "picker closed" in (tmp / "hdr").read_text()
        finally:
            d.unlink(); a.unlink()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
