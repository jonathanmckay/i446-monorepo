"""/closeshell hand-off helper (2026-11-01): the state of a thread goes into dtd
before the terminal closes. Nothing here touches Todoist: the creator and the
comment poster are stubbed, the claim store is a temp dir.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import agent_claims as ac  # noqa: E402


def _cs():
    spec = importlib.util.spec_from_file_location("closeshell", HERE / "closeshell.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "CLAIMS_DIR", tmp_path)
    monkeypatch.setattr(ac, "push", lambda: None)
    for fn in ("load", "held", "claim", "release", "set_state", "_set_held", "mark_done"):
        f = getattr(ac, fn)
        if "root" in f.__code__.co_varnames:
            monkeypatch.setattr(f, "__defaults__", tuple(
                tmp_path if d is ac.CLAIMS_DIR or (isinstance(d, Path)) else d for d in (f.__defaults__ or ())))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    return tmp_path


class _TF:
    """todo-fast stand-in: create() returns what the real one returns."""
    made = []

    @classmethod
    def create(cls, text):
        cls.made.append(text)
        return {"task": {"id": f"T{len(cls.made)}", "content": text}, "content": text,
                "labels": ["i447"], "value": 8}


def test_followup_lands_released_with_a_demon_note(store):
    cs = _cs()
    posted = []
    out = cs.followup("wire closeshell into janus (20) [8] @i447", "start at janus.py",
                      ac=ac, tf=_TF, post=lambda tid, body: posted.append((tid, body)))
    assert out["ok"] and out["state"] == "released" and out["noted"]
    tid = out["task_id"]
    rec = ac.load(store)[tid]
    assert rec["state"] == "released" and not rec["working"]   # 🕐 tier, no spinner
    assert ac.held("sess-1", store) == []                       # not held by the dying session
    assert posted == [(tid, "😈 start at janus.py")]


def test_followup_keeps_the_sessions_own_claim(store):
    cs = _cs()
    ac.claim({"id": "MAIN", "content": "the thread's task"}, "sess-1", root=store)
    cs.followup("a follow-up [3]", "", ac=ac, tf=_TF, post=lambda *a: None)
    assert ac.held("sess-1", store) == ["MAIN"]
    assert ac.load(store)["MAIN"]["state"] == "working"


def test_followup_without_session_refuses(store, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    out = _cs().followup("x [1]", "", ac=ac, tf=_TF, post=lambda *a: None)
    assert out == {"ok": False, "error": "no CLAUDE_CODE_SESSION_ID; run inside Claude"}


def test_handoff_posts_prefixed_note():
    posted = []
    out = _cs().handoff("MAIN", "  done: A. left: B. files: C  ", post=lambda t, b: posted.append((t, b)))
    assert out == {"ok": True, "task_id": "MAIN", "noted": True}
    assert posted == [("MAIN", "😈 done: A. left: B. files: C")]


def test_status_reports_claims_and_survivability(store, monkeypatch):
    cs = _cs()
    ac.claim({"id": "MAIN", "content": "the thread's task"}, "sess-1", root=store)
    monkeypatch.delenv("TMUX", raising=False); monkeypatch.delenv("CC_IN_TMUX", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "Straylight-Refit.local")
    s = cs.status(ac=ac)
    assert s["host"] == "straylight-refit" and s["survives_close"] is False
    assert [c["task_id"] for c in s["claims"]] == ["MAIN"]
    monkeypatch.setenv("CC_IN_TMUX", "1")
    assert cs.status(ac=ac)["survives_close"] is True
    monkeypatch.delenv("CC_IN_TMUX"); monkeypatch.setattr("socket.gethostname", lambda: "ix.local")
    assert cs.status(ac=ac)["survives_close"] is True


def test_cli_usage_and_status_shape():
    r = subprocess.run([sys.executable, str(HERE / "closeshell.py")], capture_output=True, text=True)
    assert r.returncode == 2 and "followup" in r.stdout
    env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "sess-cli"}
    r = subprocess.run([sys.executable, str(HERE / "closeshell.py"), "status"], capture_output=True, text=True, env=env)
    assert r.returncode == 0
    j = json.loads(r.stdout)
    assert j["ok"] and j["session"] == "sess-cli" and "survives_close" in j and isinstance(j["claims"], list)
