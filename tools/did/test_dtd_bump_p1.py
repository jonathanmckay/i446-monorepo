#!/usr/bin/env python3
"""dtd ⌥↑ (alt-up) bumps a task to Todoist p1 and to the top of the plain-task
area (2026-10-04, user request "a hotkey to move a task up to the highest
priority I can").

Covers: the binding, the generated bump script (patches the list snapshot,
fans out over marked rows, skips picker rows), p1-first ordering in the list
generator, and bump-fast.py (API call + live-cache patch, failure exit).
"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
LINES = DTD.split("\n")


def _load_bump():
    spec = importlib.util.spec_from_file_location("bump_fast_t", HERE / "bump-fast.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_alt_up_binding_and_hint():
    m = re.search(r'--bind "alt-up:[^"]*"', DTD)
    assert m and "execute-silent($DTD_BUMP {+2})" in m.group(0) and "reload-sync($DTD_RELOAD)" in m.group(0)
    assert "⌥↑: p1 top" in DTD
    assert '"$DTD_BUMP"' in DTD.split("# --- Background worker ---")[0] or '"$DTD_BUMP" "$DTD_UNDO"' in DTD


def test_bump_script_patches_snapshot_and_skips_picker_rows():
    s = next(i for i, l in enumerate(LINES) if l.startswith("DTD_BUMP="))
    e = next(i for i in range(s, len(LINES)) if LINES[i].strip() == "BUMPEOF")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cache = tmp / "cache.json"
        cache.write_text(json.dumps({"updated": "t",
                                     "today": [{"id": "A", "content": "a", "priority": 1},
                                               {"id": "B", "content": "b", "priority": None}],
                                     "关键路径": [{"id": "C", "content": "c", "priority": 2}]}))
        gen = tmp / "g.zsh"
        gen.write_text(f'#!/bin/zsh\nDTD_ID="tbumpt"; DTD_CACHE_FILE="{cache}"; DTD_HDR="{tmp}/hdr"; DTD_TIMING="/dev/null"\n'
                       + "\n".join(LINES[s:e + 2]) + "\n")
        subprocess.run(["zsh", str(gen)], check=True)
        b = Path("/tmp/dtd-tbumpt.bump.sh")
        b.write_text(b.read_text().replace('"$HOME/i446-monorepo/tools/did/bump-fast.py"', "/usr/bin/true"))
        try:
            t0 = time.time()
            r = subprocess.run(["zsh", str(b), "B", "C", "BLOCK:戌"], capture_output=True, text=True)
            assert r.returncode == 0 and time.time() - t0 < 3
            pr = {t["id"]: t["priority"] for v in json.loads(cache.read_text()).values()
                  if isinstance(v, list) for t in v}
            assert pr == {"A": 1, "B": 4, "C": 4}
            assert (tmp / "hdr").read_text().strip() == "⬆ p1: 2 task(s)"
        finally:
            b.unlink()


def test_p1_rises_to_top_of_plain_tasks_stably():
    spec = importlib.util.spec_from_file_location("ds", HERE / "test_dtd_domain_search.py")
    ds = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ds)
    def t(i, p):
        return {"id": i, "content": f"{i} (10) [5]", "labels": ["i9"], "priority": p,
                "due": "2026-09-16", "recurring": False}
    with tempfile.TemporaryDirectory() as d:
        ids = ds._ids_in_order(ds._run_listgen(Path(d), {"updated": "t",
                                                         "today": [t("R1", 1), t("R2", 4)],
                                                         "关键路径": [t("C1", 1), t("C2", 4)]}))
    assert ids == ["C2", "R2", "C1", "R1"], ids


def test_bump_fast_posts_p1_and_patches_live_cache(tmp_path):
    m = _load_bump()
    calls = []
    ok = m.bump(["X", "Y"], lambda meth, path, body: calls.append((meth, path, body)))
    assert ok == ["X", "Y"] and calls == [("POST", "/tasks/X", {"priority": 4}), ("POST", "/tasks/Y", {"priority": 4})]
    live = tmp_path / "task-queue.json"
    live.write_text(json.dumps({"today": [{"id": "X", "priority": 1}, {"id": "Z", "priority": 1}]}))
    assert m.patch_live_cache(["X"], live) == 1
    assert {t["id"]: t["priority"] for t in json.loads(live.read_text())["today"]} == {"X": 4, "Z": 1}


def test_bump_fast_reports_failure():
    m = _load_bump()
    def boom(*a):
        raise RuntimeError("503")
    assert m.bump(["X"], boom) == []


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
