#!/usr/bin/env python3
"""dtd record log (2026-10-17): the logs JM asked for after reading a week of
usage -- (1) the query a row was found under and how deep it sat, (2) task
lifecycle (first seen, impressions, age/overdue at completion), (3) estimate
vs actual minutes, (5) what did-fast actually wrote. Per-host JSONL under
the synced vault, read by dtd-log.py."""
import datetime as dt
import glob
import importlib.util
import json
import os
import pty
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DTD = HERE / "dtd.sh"
SRC = DTD.read_text()
sys.path.insert(0, str(HERE))
from test_dtd_running_highlight_by_id import _listgen_payload  # noqa: E402

TODAY = dt.date.today()


def _load_outcome(monkeypatch, tmp_path):
    state = tmp_path / "state"; (state / "jm").mkdir(parents=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("DTD_RECLOG", str(tmp_path / "rec.jsonl"))
    monkeypatch.setenv("DTD_LIFECYCLE", str(tmp_path / "lc.json"))
    monkeypatch.setenv("DTD_HOSTSLUG", "testhost")
    monkeypatch.setenv("DTD_ID", "S1")
    spec = importlib.util.spec_from_file_location("dtd_outcome_rec", HERE / "dtd_outcome.py")
    m = importlib.util.module_from_spec(spec); sys.modules["dtd_outcome_rec"] = m
    spec.loader.exec_module(m)
    return m, state, tmp_path


def test_done_record_carries_estimate_actual_points_writes_age_and_overdue(monkeypatch, tmp_path):
    m, state, tmp = _load_outcome(monkeypatch, tmp_path)
    due = (TODAY - dt.timedelta(days=2)).isoformat()
    (state / "jm/task-queue.json").write_text(json.dumps({"today": [
        {"id": "T1", "content": "repair prius (30) [8]", "labels": ["i447"], "due": due, "recurring": False}]}))
    seen = (TODAY - dt.timedelta(days=3)).isoformat()
    (tmp / "lc.json").write_text(json.dumps({"first_seen": {"T1": seen},
                                             "days": {seen: {"T1": 2}, TODAY.isoformat(): {"T1": 3}}}))
    out = {"results": [{"name": "repair prius", "step": "todoist", "todoist": {"id": "T1", "closed": True}}],
           "0fen_write": {"ok": True, "output": "OK:0fen row=287"}, "toggl_stopped": {"minutes": 25}}
    m._completed_today = lambda: (set(), set())
    rows = m.classify(out, [("T1", "repair prius (30) [8]")])
    recs = m.done_records(out, rows, [("T1", "repair prius (30) [8]")], "0", now=1000.0)
    r = recs[0]
    assert (r["status"], r["bucket"], r["est"], r["actual"], r["points"]) == ("ok", "task", 30, 25, 8)
    assert r["writes"] == {"0fen": "0fen row=287"}
    assert (r["overdue_days"], r["age_days"], r["impressions"]) == (2, 3, 5)
    assert r["host"] == "testhost" and r["kind"] == "done"
    # failed write shows as ERR in the record, never as a tick
    out2 = {"results": [{"name": "push", "step": "0n"}], "0n_write": {"ok": False, "output": "", "error": "0n verify: no OK:row echo ('')"}}
    rows2 = m.classify(out2, [("", "push")])
    r2 = m.done_records(out2, rows2, [("", "push")], "2")[0]
    assert r2["status"] == "failed" and r2["writes"]["0n"].startswith("ERR 0n verify")


def test_main_appends_record_to_reclog(monkeypatch, tmp_path):
    m, state, tmp = _load_outcome(monkeypatch, tmp_path)
    (state / "jm/task-queue.json").write_text(json.dumps({"today": []}))
    m._completed_today = lambda: (set(), set())
    out = json.dumps({"results": [{"name": "x", "step": "0n"}], "0n_write": {"ok": True, "output": "OK:row=291|24=1.0|"}})
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(out))
    m.main(["-", "0", "\tx"])
    lines = (tmp / "rec.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["writes"] == {"0n": "row=291 24=1.0"}


def test_list_generator_writes_order_and_lifecycle(tmp_path):
    home = tmp_path / "home"; (home / "i446-monorepo").parent.mkdir(parents=True, exist_ok=True)
    (home / "i446-monorepo").symlink_to(Path.home() / "i446-monorepo")
    tasks = [{"id": i, "content": f"{n} (10) [5]", "labels": ["i9"], "priority": 1, "due": TODAY.isoformat(), "recurring": False}
             for i, n in (("A1", "alpha"), ("B2", "bravo"), ("C3", "charlie"))]
    f = {}
    for name, text in (("cache.json", json.dumps({"updated": f"{TODAY}T10:00:00", "today": tasks})),
                       ("done.json", json.dumps({"date": TODAY.isoformat(), "names": [], "ids": {}})),
                       ("removed", ""), ("skipped", ""), ("timer", ""), ("view", "")):
        (tmp_path / name).write_text(text); f[name] = str(tmp_path / name)
    (tmp_path / "removed.ids").write_text("")
    (tmp_path / "lg.py").write_text(_listgen_payload())
    env = dict(os.environ, HOME=str(home), DTD_LIFECYCLE=str(tmp_path / "lc.json"), DTD_ORDER=str(tmp_path / "order"),
               DTD_EVENTS=str(tmp_path / "ev.tsv"), DTD_ID="T")
    args = [sys.executable, str(tmp_path / "lg.py"), f["cache.json"], f["done.json"], f["removed"], TODAY.isoformat(), "120",
            f["skipped"], f["timer"], f["view"]]
    for _ in range(2):
        r = subprocess.run(args, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
    order = (tmp_path / "order").read_text().split()
    assert set(order) == {"A1", "B2", "C3"} and len(order) == 3
    lc = json.loads((tmp_path / "lc.json").read_text())
    assert set(lc["first_seen"]) == {"A1", "B2", "C3"} and lc["first_seen"]["A1"] == TODAY.isoformat()
    assert lc["days"][TODAY.isoformat()]["B2"] == 2      # two renders counted


def test_routers_write_pick_records_and_change_stashes_query():
    for marker in ('_rec_pick enter "\\$id" "\\$2"', '_rec_pick done "\\$_id" "\\$_qq"', '_rec_pick defer "\\$_tid" "\\$_qq"'):
        assert marker in SRC, marker
    i = SRC.index('cat > "$DTD_DOMAINSEARCH"')
    assert 'print -rn -- "\\$q" > "$DTD_QUERY"' in SRC[i:i + 400]
    assert "DTD_RECLOG=" in SRC and "DTD_ORDER=" in SRC and "DTD_LIFECYCLE=" in SRC


def _drain(fd, seconds):
    import select
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                if not os.read(fd, 65536):
                    return
            except OSError:
                return


@pytest.mark.skipif(shutil.which("fzf") is None or shutil.which("zsh") is None, reason="needs zsh + fzf")
def test_generated_pick_writer_records_query_and_position(tmp_path):
    """Launch dtd for real, then run its generated Enter and done routers the
    way fzf would and read the pick records back: position comes from the
    order file, the done router's query from the change-event stash."""
    rec = tmp_path / "rec.jsonl"
    env = dict(os.environ, DTD_RECLOG=str(rec), DTD_EVENTS=str(tmp_path / "ev.tsv"), DTD_LIFECYCLE=str(tmp_path / "lc.json"))
    before = set(glob.glob("/tmp/dtd-*.start.sh"))
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover
        os.execvpe("zsh", ["zsh", "-c", f"source {DTD}"], env)
    stem = None
    try:
        for _ in range(150):
            _drain(fd, 0.1)
            new = set(glob.glob("/tmp/dtd-*.start.sh")) - before
            if new:
                stem = sorted(new)[0][: -len(".start.sh")]
                break
        assert stem, "dtd never launched"
        for _ in range(250):
            _drain(fd, 0.1)
            if os.path.exists(stem + ".port") and os.path.getsize(stem + ".port") > 0:
                break
        Path(stem + ".order").write_text("A1\nB2\nC3\n")
        subprocess.run(["zsh", stem + ".pickenter.sh", "B2", 'xk87 "quoted"'], capture_output=True, text=True, timeout=30)
        Path(stem + ".query").write_text("m5x2")
        # dtd's own watcher may regenerate the real list (and the order file)
        # between calls; rewrite the order right before this router runs.
        Path(stem + ".order").write_text("A1\nB2\nC3\n")
        subprocess.run(["zsh", stem + ".done-router.sh", "C3"], capture_output=True, text=True, timeout=30)
    finally:
        try:
            os.write(fd, b"\x1b")
        except OSError:
            pass
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if os.waitpid(pid, os.WNOHANG) != (0, 0):
                    break
            except ChildProcessError:
                break
            _drain(fd, 0.1)
        else:
            os.kill(pid, 9)
    recs = [json.loads(l) for l in rec.read_text().splitlines() if l.strip()]
    enter = next(r for r in recs if r["action"] == "enter")
    done = next(r for r in recs if r["action"] == "done")
    assert (enter["id"], enter["q"], enter["pos"], enter["n"]) == ("B2", 'xk87 "quoted"', 2, 3)
    assert (done["id"], done["q"], done["pos"]) == ("C3", "m5x2", 3)
    assert all(r["kind"] == "pick" and r["host"] for r in recs)


def test_reader_summarizes_both_hosts(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("dtd_log_t", HERE / "dtd-log.py")
    dl = importlib.util.module_from_spec(spec); spec.loader.exec_module(dl)
    monkeypatch.setattr(dl, "LOGDIR", str(tmp_path))
    now = time.time()
    (tmp_path / "ix.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"ts": now, "host": "ix", "kind": "done", "status": "ok", "bucket": "task", "content": "a (30) [20]", "est": 30, "actual": 45, "points": 20, "writes": {}},
        {"ts": now, "host": "ix", "kind": "done", "status": "ok", "bucket": "habit", "content": "push", "writes": {"0n": "row=1 24=1.0"}},
        {"ts": now, "host": "ix", "kind": "pick", "action": "done", "id": "x", "q": "xk87", "pos": 22, "n": 80},
    ]) + "\n")
    (tmp_path / "straylight.jsonl").write_text(json.dumps(
        {"ts": now, "host": "straylight", "kind": "done", "status": "failed", "bucket": "task", "content": "b", "writes": {"0n": "ERR x"}}) + "\n")
    s = dl.summarize(dl.load(1))
    assert (s["done"], s["failed"], s["by_bucket"], s["points"]) == (2, 1, {"task": 1, "habit": 1}, 20)
    assert s["picks_with_query"] == 1 and s["picks_beyond_15"] == 1 and s["est_vs_actual"] == [(30, 45)]


def test_every_completion_path_records():
    """The single-item success path (the common one) and the idle-tick retry
    path must feed dtd_outcome.py too, not only the batch and fallback paths."""
    i = SRC.index('if [[ -n "$ok" ]]; then')
    assert 'python3 "$DTD_OUTCOME" - "$rc" "$task_id"' in SRC[i:i + 900]
    j = SRC.index('(retry)" > "$DTD_HDR"')
    assert 'python3 "$DTD_OUTCOME" - "$rrc" "$rid"' in SRC[j - 400:j]
