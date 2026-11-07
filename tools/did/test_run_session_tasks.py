"""Dream v173 (2026-10-30): /did's fast path is run.py, not did-fast. The
teaching sessions did-fast prices at 1分/min + 5 (math, english, 中文;
VARIABLE_SESSION_BONUS) have no registry habit, so run.py sent them to
route.py: "math" still aliased to the retired 问学 0n habit (col AI, its
0neon card closed by alias), and "english 30" fell to run_one_off's word
overlap, which closed any open card containing "english" and credited
that card's [N]. run.py must hand them to did-fast verbatim."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).parent
sys.path.insert(0, str(Path.home() / "i446-monorepo/lib"))
_spec = importlib.util.spec_from_file_location("did_run_sessions", _HERE / "run.py")
run = importlib.util.module_from_spec(_spec)
sys.modules["did_run_sessions"] = run
_spec.loader.exec_module(run)


def _didfast_mirror():
    s = importlib.util.spec_from_file_location("didfast_sessions", _HERE / "did-fast.py")
    m = importlib.util.module_from_spec(s)
    sys.modules["didfast_sessions"] = m
    s.loader.exec_module(m)
    return m


def test_session_set_mirrors_did_fast():
    assert run.SESSION_TASKS == set(_didfast_mirror().VARIABLE_SESSION_BONUS)


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        out = {"results": [{"name": cmd[2].split()[0], "step": "variable",
                            "0fen": {"col": "X", "points": 35}}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    def no_route(*a, **k):
        raise AssertionError("session task must not reach route.py")

    def no_one_off(*a, **k):
        raise AssertionError("session task must not reach the word-overlap close")

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(run, "_route", no_route)
    monkeypatch.setattr(run, "run_one_off", no_one_off)
    return seen


@pytest.mark.parametrize("item", ["english 30", "math 30", "中文 1600-1630", "Math 20 10/29"])
def test_session_items_go_to_did_fast_verbatim(monkeypatch, calls, item):
    monkeypatch.setattr(sys, "argv", ["run.py", item])
    assert run.main() == 0
    assert calls == [["python3", run.DID_FAST, item]]


def test_did_fast_failure_is_rc1_not_agent_deferral(monkeypatch):
    monkeypatch.setattr(run.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=2, stdout="", stderr="write failed"))
    monkeypatch.setattr(sys, "argv", ["run.py", "english 30"])
    assert run.main() == 1


def test_math_is_not_a_wenxue_alias_in_the_registry():
    cfg = json.loads((_HERE.parent.parent / "config" / "tasks.json").read_text())
    for hid, h in cfg["habits"].items():
        assert "math" not in [a.lower() for a in h.get("aliases", [])], hid
    assert cfg["habits"]["问学"].get("toggl_desc") != "math"
