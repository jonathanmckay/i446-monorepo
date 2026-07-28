"""Multi-select defer/skip/delete (2026-07-23): shift-arrow marks rows in
fzf (--multi), and ctrl-d/ctrl-x/ctrl-k pass {+2} — every marked row's id,
falling back to the cursor row when nothing is marked. The defer script
prompts ONCE and fans the batch out to per-task workers; skip/delete re-run
themselves per id. Marks are cleared after every consuming action so a stale
selection can't feed a later keypress."""
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "dtd.sh").read_text()


# ── binding wiring ──────────────────────────────────────────────────────────

def test_fzf_multi_and_shift_binds():
    assert "--multi" in SRC
    assert "shift-down:toggle+down" in SRC and "shift-up:toggle+up" in SRC


def test_batch_bindings_use_plus_placeholder_and_clear_marks():
    # ctrl-k re-bound to the block picker 2026-07-27 (skip is keyless now);
    # both picker keys must still batch the {+2} marked set.
    for key, script in (("ctrl-d", "$DTD_DEFER"), ("ctrl-x", "$DTD_DELETE"),
                        ("ctrl-k", "$DTD_BLOCKARM"), ("ctrl-v", "$DTD_BLOCKARM")):
        m = re.search(rf'--bind "{key}:execute(?:-silent)?\(({re.escape(script)}[^)]*)\)([^"]*)"', SRC)
        assert m, f"{key} binding not found"
        assert "{+2}" in m.group(1), f"{key} must pass all marked ids via {{+2}}"
        assert "deselect-all" in m.group(2), f"{key} must clear marks after acting"


def test_single_target_bindings_keep_cursor_only_but_clear_marks():
    for key in ("enter", "ctrl-s", "ctrl-p"):
        m = re.search(rf'--bind "{key}:[^"]*"', SRC)
        assert m and "{+2}" not in m.group(0), f"{key} stays cursor-row-only"
        assert "deselect-all" in m.group(0), f"{key} must clear stale marks"


def test_skip_and_delete_have_fanout_preamble():
    for var in ("DTD_SKIP", "DTD_DELETE"):
        m = re.search(rf'cat > "\${var}" << \w+EOF\n(.*?)\n\w+EOF', SRC, re.S)
        assert m, f"{var} heredoc not found"
        assert re.search(r'for _tid in "\\\$@"; do "\\\$0" "\\\$_tid"; done', m.group(1)), \
            f"{var} must fan out multiple ids by re-running itself"


# ── defer batch behavior (extracted script, same harness as the async test) ─

def _defer_script(tmp, paths, stub_body):
    m = re.search(r'cat > "\$DTD_DEFER" << DEFEREOF\n(.*?)\nDEFEREOF', SRC, re.S)
    body = m.group(1)
    body = body.replace("$DTD_HDR", paths["hdr"]).replace("$DTD_REMOVED", paths["removed"])
    body = body.replace("$DTD_PUSHED", paths["pushed"]).replace("$DTD_PROCESSED", paths["processed"])
    body = body.replace("$UNDO_FAST", "/usr/bin/true").replace("$DTD_JOURNAL", paths["journal"])
    cache_stub = os.path.join(tmp, "cache.json")
    open(cache_stub, "w").write("{}")
    body = body.replace("$DTD_RESOLVE", str(HERE / "dtd_resolve.py"))
    body = body.replace("$DTD_CACHE_FILE", cache_stub)
    body = body.replace("\\$", "$")
    stub = os.path.join(tmp, "defer_stub.py")
    open(stub, "w").write(stub_body)
    body = body.replace('DEFER_FAST="$HOME/i446-monorepo/tools/did/defer-fast.py"',
                        f'DEFER_FAST="{stub}"')
    script = os.path.join(tmp, "defer.sh")
    open(script, "w").write(body)
    os.chmod(script, 0o755)
    return script


def test_defer_batch_hides_and_processes_every_id():
    tmp = tempfile.mkdtemp()
    paths = {k: os.path.join(tmp, k) for k in ("hdr", "removed", "pushed", "processed", "journal")}
    for p in paths.values():
        open(p, "w").close()
    open(paths["removed"] + ".ids", "w").close()
    script = _defer_script(
        tmp, paths,
        'import json\n'
        'print(json.dumps({"target_date": "2026-07-24", "claimed_points": 2, "remaining_points": 10}))\n')
    t0 = time.time()
    subprocess.run([script, "id-AAA", "id-BBB", "id-CCC"], timeout=10)
    assert time.time() - t0 < 1.5, "batch defer must not block"
    ids = open(paths["removed"] + ".ids").read()
    assert {"id-AAA", "id-BBB", "id-CCC"} <= set(ids.split()), "every id optimistically hidden"
    assert open(paths["pushed"]).read().count("x") == 3, "one in-flight counter per task"
    time.sleep(2)
    assert open(paths["processed"]).read().count("x") == 3, "every worker completed"


def test_defer_single_id_still_works():
    tmp = tempfile.mkdtemp()
    paths = {k: os.path.join(tmp, k) for k in ("hdr", "removed", "pushed", "processed", "journal")}
    for p in paths.values():
        open(p, "w").close()
    open(paths["removed"] + ".ids", "w").close()
    script = _defer_script(
        tmp, paths,
        'import json\n'
        'print(json.dumps({"target_date": "2026-07-24", "claimed_points": 2, "remaining_points": 10}))\n')
    subprocess.run([script, "solo-id"], timeout=10)
    assert "solo-id" in open(paths["removed"] + ".ids").read()
    assert open(paths["pushed"]).read().strip() == "x"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
