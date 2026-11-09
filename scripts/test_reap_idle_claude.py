#!/usr/bin/env python3
"""reap-idle-claude.py (2026-11-08, Ix memory): which sessions it reaps, and
that it signals a session's whole tree captured before the parent dies."""
import importlib.util
import json
import signal
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOW = 1_800_000_000.0
H = 3600 * 1000


def _load():
    spec = importlib.util.spec_from_file_location("reap", HERE / "reap-idle-claude.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _session(d: Path, pid, sid, *, kind="interactive", status="idle", idle_h=100, start="Tue Nov  3 16:21:43 2026"):
    (d / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "kind": kind, "status": status, "name": sid,
        "statusUpdatedAt": NOW * 1000 - idle_h * H, "procStart": start}))


def test_candidates_reap_only_idle_unclaimed_interactive(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "SESSIONS", tmp_path)
    monkeypatch.setattr(m, "lstart_utc", lambda pid: "Sun Nov 1 00:00:00 2026" if pid == 7 else "Tue Nov 3 16:21:43 2026")
    _session(tmp_path, 1, "old")                              # reap
    _session(tmp_path, 2, "waiting", status="waiting")        # reap: a dialog left open for days
    _session(tmp_path, 3, "fresh", idle_h=10)
    _session(tmp_path, 4, "busy", status="busy")
    _session(tmp_path, 5, "shell", status="shell")
    _session(tmp_path, 6, "bg", kind="bg")
    _session(tmp_path, 7, "reused")                           # pid now belongs to another process
    _session(tmp_path, 8, "claimed")
    _session(tmp_path, 9, "me")
    _session(tmp_path, 10, "gone")                            # not running
    table = {p: 1 for p in range(1, 10)}
    rows = m.candidates(48, NOW, table, held={"claimed"}, me={9})
    assert {r["session"]: r["skip"] for r in rows} == {
        "old": None, "waiting": None, "fresh": "idle 10h", "busy": "status busy", "shell": "status shell",
        "bg": "kind bg", "reused": "pid reused", "claimed": "holds a dtd claim", "me": "this session"}


def test_unknown_claims_reap_nothing(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "SESSIONS", tmp_path)
    monkeypatch.setattr(m, "lstart_utc", lambda pid: "Tue Nov 3 16:21:43 2026")
    _session(tmp_path, 1, "old")
    assert m.candidates(48, NOW, {1: 1}, held={"*"}, me=set())[0]["skip"] == "holds a dtd claim"


def test_reap_signals_the_tree_captured_before_the_parent_dies(monkeypatch):
    m = _load()
    # 100 = claude; 101 = npx wrapper; 102 = its node grandchild; 200 = unrelated
    table = {100: 1, 101: 100, 102: 101, 200: 1}
    sent = []
    alive = {100, 101, 102, 200}

    def kill(pid, sig):
        if pid not in alive:
            raise ProcessLookupError
        sent.append((pid, sig))
        if sig == signal.SIGTERM and pid != 102:   # the grandchild ignores SIGTERM
            alive.discard(pid)
        elif sig == signal.SIGKILL:
            alive.discard(pid)

    monkeypatch.setattr(m.os, "kill", kill)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    left = m.reap([100], table)
    assert {p for p, s in sent if s == signal.SIGTERM} == {100, 101, 102}
    assert sent[-1] == (102, signal.SIGKILL) and left == {100: 1}
    assert 200 in alive


def test_ix_crontab_runs_the_reaper_at_0245():
    cron = (HERE.parent / "config" / "ix.crontab").read_text().splitlines()
    reap = next(l for l in cron if "reap-idle-claude.py" in l and not l.startswith("#"))
    assert reap.split()[:2] == ["45", "2"]


def test_dead_tmux_only_detached_cc_sessions_at_a_bare_shell():
    m = _load()
    # pane pids: 10 bare zsh; 20 zsh running claude-tracked (child 21); 30 attached;
    # 40 python foreground; 50 not a cc-* session; 60/61 one dead pane + one live pane
    table = {10: 1, 20: 1, 21: 20, 30: 1, 40: 1, 50: 1, 60: 1, 61: 1, 62: 61}
    panes = [("cc-095453", 0, "zsh", 10), ("cc-busy", 0, "zsh", 20), ("cc-phone", 1, "zsh", 30),
             ("cc-claude", 0, "Python", 40), ("dtd", 0, "zsh", 50),
             ("cc-split", 0, "zsh", 60), ("cc-split", 0, "zsh", 61)]
    assert m.dead_tmux(panes, table) == ["cc-095453"]
