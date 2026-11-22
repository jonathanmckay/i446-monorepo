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


# --- regrab (2026-11-21): a follow-up in the thread that ✅'d a task takes it back ---
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

RG_TASK = {"id": "R1", "content": "hcmc feed [5]"}
HOOK_SH = Path(__file__).resolve().parents[2] / "scripts" / "agent-claim-hook.sh"


def _rg_state(root, tid="R1"):
    return (root / f"{tid}.state").read_text().split()[0]


def _prompt(sid, text="one more thing"):
    return json.dumps({"session_id": sid, "prompt": text})


def test_followup_regrabs_the_done_task(tmp_path):
    ac.claim(RG_TASK, "S1", root=tmp_path, now=1000)
    ac.mark_done("S1", root=tmp_path, now=1010)
    assert ac.held("S1", tmp_path) == [] and _rg_state(tmp_path) == "done"
    out = ac.regrab(_prompt("S1"), root=tmp_path, now=1020, open_ids={"R1"})
    assert "re-claimed" in out and "hcmc feed [5]" in out and "R1" in out
    assert ac.held("S1", tmp_path) == ["R1"] and _rg_state(tmp_path) == "working"
    # one grab per ✅: the marker is spent, the next prompt is an ordinary held turn
    assert not (tmp_path / "by-session/S1.done").exists()
    assert ac.regrab(_prompt("S1"), root=tmp_path, now=1030, open_ids={"R1"}) == ""
    # done again → ✅ again, and a later follow-up grabs it again
    ac.mark_done("S1", root=tmp_path, now=1040)
    assert _rg_state(tmp_path) == "done" and not (tmp_path / "by-session/S1.regrab").exists()
    assert ac.regrab(_prompt("S1"), root=tmp_path, now=1050, open_ids={"R1"})


def test_repeat_bare_done_keeps_the_marker(tmp_path):
    ac.claim(RG_TASK, "S1", root=tmp_path, now=1000)
    ac.mark_done("S1", root=tmp_path, now=1010)
    ac.mark_done("S1", root=tmp_path, now=1011)          # skill, then model: nothing held now
    assert ac.regrab(_prompt("S1"), root=tmp_path, now=1020, open_ids={"R1"})


def test_no_regrab_once_jm_closed_or_someone_else_took_it(tmp_path):
    for case in ("closed-here", "closed-elsewhere", "other-session", "unpinned-state"):
        root = tmp_path / case
        ac.claim(RG_TASK, "S1", root=root, now=1000)
        ac.mark_done("S1", root=root, now=1010)
        open_ids = {"R1"}
        if case == "closed-here":
            ac.clear_task("R1", root)
        elif case == "closed-elsewhere":                  # local ✅ record survives; dtd's cache is the proof
            open_ids = set()
        elif case == "other-session":
            ac.claim(RG_TASK, "S2", root=root, now=1015)
        else:
            ac.set_state("R1", "released", now=1015, root=root)
        assert ac.regrab(_prompt("S1"), root=root, now=1020, open_ids=open_ids) == "", case
        assert ac.held("S1", root) == [], case


def test_claiming_or_releasing_ends_the_regrabbing(tmp_path):
    ac.claim(RG_TASK, "S1", root=tmp_path, now=1000)
    ac.mark_done("S1", root=tmp_path, now=1010)
    ac.release("S1", tmp_path, now=1015)                 # `/d off` with nothing held still clears the marker
    assert ac.regrab(_prompt("S1"), root=tmp_path, now=1020, open_ids={"R1"}) == ""
    assert _rg_state(tmp_path) == "done"

    ac.claim(RG_TASK, "S1", root=tmp_path, now=1030)
    ac.mark_done("S1", root=tmp_path, now=1040)
    ac.claim({"id": "R2", "content": "something else"}, "S1", root=tmp_path, now=1050)
    ac.release("S1", tmp_path, now=1060)
    assert ac.regrab(_prompt("S1"), root=tmp_path, now=1070, open_ids={"R1", "R2"}) == ""
    assert _rg_state(tmp_path) == "done"


def test_leaving_a_regrabbed_task_restores_done_not_released(tmp_path):
    """The thread moved on (`/d off` or `/d <other>`): the card the follow-up
    grabbed goes back to ✅, where the agent had left it, not 🕐."""
    ac.claim(RG_TASK, "S1", root=tmp_path, now=1000)
    ac.mark_done("S1", root=tmp_path, now=1010)
    ac.regrab(_prompt("S1"), root=tmp_path, now=1020, open_ids={"R1"})
    ac.claim({"id": "R2", "content": "something else"}, "S1", root=tmp_path, now=1030)
    assert _rg_state(tmp_path) == "done" and ac.held("S1", tmp_path) == ["R2"]
    ac.release("S1", tmp_path, now=1040)                 # an ordinary claim still releases as before
    assert _rg_state(tmp_path, "R2") == "released"


def test_only_the_latest_done_is_grabbed_back(tmp_path):
    ac.claim(RG_TASK, "S1", root=tmp_path, now=1000)
    ac.mark_done("S1", root=tmp_path, now=1010)
    ac.claim({"id": "R2", "content": "second"}, "S1", root=tmp_path, now=1020)
    ac.mark_done("S1", root=tmp_path, now=1030)
    ac.regrab(_prompt("S1"), root=tmp_path, now=1040, open_ids={"R1", "R2"})
    assert ac.held("S1", tmp_path) == ["R2"] and _rg_state(tmp_path) == "done"


def _sh(home, mode, payload):
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("AGENT_CLAIMS_DIR", None)
    return subprocess.run([str(HOOK_SH), mode], input=json.dumps(payload), text=True, env=env,
                          capture_output=True, timeout=20)


def test_shell_hook_regrabs_on_an_ordinary_prompt_only(tmp_path):
    """End to end through agent-claim-hook.sh: `/…`, `<command-…>` and `[N]`
    prompts never reach python; an ordinary one does, and the card is working."""
    home = tmp_path
    root = home / "vault/z_ibx/agent-claims"
    (home / "i446-monorepo").symlink_to(Path(__file__).resolve().parents[2])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
    import state_paths
    tq = home / Path(state_paths.TASK_QUEUE).relative_to(Path.home())
    tq.parent.mkdir(parents=True, exist_ok=True)
    tq.write_text(json.dumps({"today": [RG_TASK]}))
    ac.claim(RG_TASK, "S1", root=root, now=1000)
    ac.mark_done("S1", root=root, now=1010)
    for text in ("/0t", "<command-name>/fin</command-name>", "[20]"):
        _sh(home, "working", {"session_id": "S1", "prompt": text})
        assert _rg_state(root) == "done" and (root / "by-session/S1.done").exists(), text
    r = _sh(home, "working", {"session_id": "S1", "prompt": "also make it blue"})
    assert "re-claimed" in r.stdout, r.stderr
    assert _rg_state(root) == "working" and ac.held("S1", root) == ["R1"]
    _sh(home, "release", {"session_id": "S1"})           # SessionEnd: back to ✅, markers gone
    assert _rg_state(root) == "done"
    for name in ("S1", "S1.done", "S1.regrab"):
        assert not (root / "by-session" / name).exists(), name


def test_shell_session_end_drops_the_marker(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(RG_TASK, "S1", root=root, now=1000)
    ac.mark_done("S1", root=root, now=1010)
    _sh(tmp_path, "release", {"session_id": "S1"})
    assert not (root / "by-session/S1.done").exists() and _rg_state(root) == "done"
