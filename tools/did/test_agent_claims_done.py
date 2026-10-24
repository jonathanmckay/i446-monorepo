#!/usr/bin/env python3
"""2026-10-22: one session can hold several claims (`claim --also`, for work
fanned out to subagents), and `done` marks a claim finished-by-the-agent so
dtd draws ✅ on the left until JM completes it."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("ac_done", Path(__file__).parent / "agent_claims.py")
ac = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ac)


def test_also_keeps_earlier_claims(tmp_path):
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path, also=True)
    assert ac.held("S", tmp_path) == ["a", "b"]
    ac.claim({"id": "c", "content": "C"}, "S", tmp_path)      # plain claim replaces
    assert ac.held("S", tmp_path) == ["c"]
    assert ac.load(tmp_path)["a"]["state"] == "released"


def test_done_marks_and_leaves_index(tmp_path):
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path, also=True)
    assert ac.mark_done("S", ["a"], tmp_path) == ["a"]
    claims = ac.load(tmp_path)
    assert claims["a"]["done"] and not claims["a"]["working"] and claims["a"]["pane"] is None
    assert not claims["b"]["done"]
    assert ac.held("S", tmp_path) == ["b"]
    ac.mark_done("S", None, tmp_path)                          # default: everything held
    assert ac.load(tmp_path)["b"]["done"] and ac.held("S", tmp_path) == []


def test_release_all_held(tmp_path):
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path, also=True)
    assert ac.release("S", tmp_path) == "a"
    assert {r["state"] for r in ac.load(tmp_path).values()} == {"released"}


def test_done_claim_keeps_its_session_pane_number(tmp_path):
    """2026-10-22: "that number should then flow through to each task that you
    have claimed" — a done claim still shows which tab finished it ('6✅')."""
    import json
    (tmp_path / "panes.json").write_text(json.dumps({"S": 6}))
    ac.claim({"id": "a", "content": "A"}, "S", tmp_path)
    ac.mark_done("S", ["a"], tmp_path)
    assert ac.load(tmp_path)["a"]["pane"] == 6
    ac.claim({"id": "b", "content": "B"}, "S", tmp_path)       # replaces: a is done, stays done
    ac.release("S", tmp_path)
    assert ac.load(tmp_path)["b"]["pane"] is None              # released: session elsewhere


def test_dtd_draws_the_pane_number_before_the_done_check():
    dtd = (Path(__file__).parent / "dtd.sh").read_text()
    assert "prefix = _pane + '✅ ' + prefix" in dtd


# ── 2026-10-24: "notes had the abacus as if I'm working on it, but it was long since stale" ──

def test_clear_task_drops_the_claim_whatever_its_state(tmp_path):
    ac.claim({"id": "n", "content": "notes"}, "S", tmp_path)
    ac.release("S", tmp_path)                       # released claims normally linger a day
    assert ac.load(tmp_path)["n"]["state"] == "released"
    assert ac.clear_task("n", tmp_path) is True
    assert "n" not in ac.load(tmp_path)
    assert ac.clear_task("n", tmp_path) is False    # no claim: harmless


def test_load_reports_when_the_state_was_set(tmp_path):
    ac.claim({"id": "n", "content": "notes"}, "S", tmp_path, now=1792767000)
    assert ac.load(tmp_path, now=1792767100)["n"]["state_ts"] == 1792767000


def test_did_fast_clears_the_claim_of_every_task_it_closes():
    src = (Path(__file__).parent / "did-fast.py").read_text()
    i = src.index("completed_ids = {id_to_name[tid]: tid")
    block = src[i:i + 1200]
    assert "_ac.clear_task(str(_tid))" in block and "if _ok:" in block


def test_dtd_ignores_a_repeating_cards_claim_from_before_today():
    dtd = (Path(__file__).parent / "dtd.sh").read_text()
    i = dtd.index("_claims = _ac.load()")
    block = dtd[i:i + 900]
    assert "_midnight = time.mktime(time.strptime(today, '%Y-%m-%d'))" in block
    assert "if not (i in _recur_ids and r.get('state_ts', 0) < _midnight)" in block
    assert block.index("_recur_ids") < block.index("_agent_ids = ")   # filtered before any set is built
