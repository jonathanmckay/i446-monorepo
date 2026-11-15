"""A task created and claimed on one host must reach dtd on another.

Bug 2026-11-15: `/d fix claiming bug [2]` on Ix created an unlabeled task due
today and claimed it. The claim reached Straylight in 2s (agent-claims-pull),
but the card never showed in Straylight's dtd: refresh-cache.py keeps the
`today` bucket verbatim, and nothing ran did-fast's full refresh there until
the next 2h block boundary. Two halves:
  1. dtd's watcher fires one --refresh-cache when a live claim names a task
     the local cache does not hold;
  2. that refresh unions claimed-but-missing tasks by direct GET /tasks/{id},
     because the today|overdue filter index lags minutes behind creation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
DTD = (HERE / "dtd.sh").read_text()


def _df():
    spec = importlib.util.spec_from_file_location("did_fast_claim_union", HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_claim_union"] = mod
    spec.loader.exec_module(mod)
    return mod


def _claim(root: Path, tid: str, state: str, sub: str = "") -> None:
    d = root / sub if sub else root
    (d).mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps({"task_id": tid, "task": tid}))
    (d / f"{tid}.state").write_text(f"{state} 1\n")


def test_refresh_unions_a_claimed_task_the_filter_has_not_indexed(tmp_path):
    df = _df()
    _claim(tmp_path, "NEWTASK0000001", "working", "remote/ix")   # created on Ix, claim pulled here
    _claim(tmp_path, "DONETASK000001", "done")                   # agent-finished backlog: not chased
    _claim(tmp_path, "RELTASK0000001", "released")
    _claim(tmp_path, "KNOWNTASK00001", "idle")                   # already in the cache
    _claim(tmp_path, "FUTURETASK0001", "idle")                   # claimed, due later: stays out of today
    _claim(tmp_path, "CLOSEDTASK0001", "working")                # closed in Todoist meanwhile
    (tmp_path / "panes.json").write_text("{}")
    live = {
        "NEWTASK0000001": {"id": "NEWTASK0000001", "content": "fix claiming bug [2]", "labels": [],
                           "priority": 1, "checked": False, "due": {"date": "2026-11-15", "string": "Nov 15"}},
        "FUTURETASK0001": {"id": "FUTURETASK0001", "content": "later", "checked": False,
                           "due": {"date": "2026-11-20"}},
        "CLOSEDTASK0001": {"id": "CLOSEDTASK0001", "content": "closed", "checked": True,
                           "due": {"date": "2026-11-15"}},
    }
    asked = []

    def get(url, method="GET"):
        tid = url.rsplit("/", 1)[1]
        asked.append(tid)
        return (200, json.dumps(live[tid])) if tid in live else (404, "")

    today = [{"id": "KNOWNTASK00001", "content": "known", "due": "2026-11-15"}]
    added = df.union_claimed_tasks(today, {"KNOWNTASK00001"}, "2026-11-15", root=tmp_path, get=get)
    assert [t["id"] for t in added] == ["NEWTASK0000001"]
    assert [t["id"] for t in today] == ["KNOWNTASK00001", "NEWTASK0000001"]
    assert today[1]["content"] == "fix claiming bug [2]" and today[1]["due"] == "2026-11-15"
    assert sorted(asked) == ["CLOSEDTASK0001", "FUTURETASK0001", "NEWTASK0000001"]   # done/released/known never fetched


def test_refresh_inner_calls_the_claim_union():
    src = (HERE / "did-fast.py").read_text()
    inner = src[src.index("def _refresh_task_queue_inner"):]
    inner = inner[:inner.index("\ndef ", 10)]
    assert "union_claimed_tasks(" in inner


def _snippet() -> str:
    a, b = DTD.index("# BEGIN claimed-unqueued refresh"), DTD.index("# END claimed-unqueued refresh")
    return DTD[a:b]


def test_watcher_refreshes_once_for_a_claim_the_cache_does_not_hold(tmp_path):
    home = tmp_path / "home"
    root = home / "vault" / "z_ibx" / "agent-claims"
    _claim(root, "NEWTASK0000001", "working", "remote/ix")
    _claim(root, "DONETASK000001", "done")
    _claim(root, "KNOWNTASK00001", "idle")
    cache = tmp_path / "task-queue.json"
    cache.write_text(json.dumps({"today": [{"id": "KNOWNTASK00001"}]}))
    fake = tmp_path / "fake_did_fast.py"
    fake.write_text("import sys, pathlib\n"
                    f"p = pathlib.Path({str(tmp_path / 'refreshes')!r})\n"
                    "p.write_text((p.read_text() if p.exists() else '') + ' '.join(sys.argv[1:]) + '\\n')\n")
    script = ("typeset -A _acq_tried\n" + _snippet() + "\nwait\n" + _snippet() + "\nwait\n")
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "CACHE": str(cache), "DID_FAST": str(fake)}
    out = subprocess.run(["/bin/zsh", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "refreshes").read_text() == "--refresh-cache\n"   # one refresh, not one per pass


def test_watcher_stays_quiet_when_every_live_claim_is_cached(tmp_path):
    home = tmp_path / "home"
    root = home / "vault" / "z_ibx" / "agent-claims"
    _claim(root, "KNOWNTASK00001", "working")
    _claim(root, "DONETASK000001", "done")
    cache = tmp_path / "task-queue.json"
    cache.write_text(json.dumps({"today": [{"id": "KNOWNTASK00001"}]}))
    fake = tmp_path / "fake_did_fast.py"
    fake.write_text(f"open({str(tmp_path / 'refreshes')!r}, 'a').write('x')\n")
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "CACHE": str(cache), "DID_FAST": str(fake)}
    out = subprocess.run(["/bin/zsh", "-c", "typeset -A _acq_tried\n" + _snippet() + "\nwait\n"],
                         env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert not (tmp_path / "refreshes").exists()
