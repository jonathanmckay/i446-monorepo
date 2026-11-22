#!/usr/bin/env python3
"""/claim (2026-10-05): tie a dtd task to the Claude session working on it.
Store round-trip, staleness, matching, the zsh hook, the ticker spinner, and
both renderers (terminal dtd greys + 😈, dtd web flags `agent`)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOK = HERE.parent.parent / "scripts" / "agent-claim-hook.sh"
sys.path.insert(0, str(HERE))
import agent_claims as ac  # noqa: E402

TASK = {"id": "T1", "content": "source logging (30) [20]", "labels": ["i9"]}


def test_claim_load_release(tmp_path):
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    got = ac.load(tmp_path, now=1010)
    assert got["T1"]["working"] and got["T1"]["session"] == "S1"
    ac.set_state("T1", "idle", now=1020, root=tmp_path)
    assert ac.working_ids(tmp_path, now=1030) == set()
    assert ac.release("S1", tmp_path, now=1040) == "T1"
    # Released (2026-10-21): the record stays so dtd keeps the row at the top,
    # not working, no pane; pruned a day later.
    got = ac.load(tmp_path, now=1050)
    assert got["T1"]["state"] == "released" and not got["T1"]["working"] and got["T1"]["pane"] is None
    assert not (tmp_path / "by-session/S1").exists()
    assert ac.load(tmp_path, now=1040 + ac.RELEASED_TTL + 1) == {}
    assert not (tmp_path / "T1.json").exists() and not (tmp_path / "T1.state").exists()


def test_one_task_per_session(tmp_path):
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    ac.claim({"id": "T2", "content": "other"}, "S1", root=tmp_path, now=1001)
    got = ac.load(tmp_path, now=1002)
    # The session holds T2; T1 was handed back and stays listed as released
    # (dtd keeps it at the top until JM closes it, 2026-10-21).
    assert got["T2"]["working"] and got["T1"]["state"] == "released" and not got["T1"]["working"]
    assert (tmp_path / "by-session/S1").read_text().strip() == "T2"


def test_pane_number_attached_from_panes_json(tmp_path):
    """cs.py writes panes.json {session: n}; load() hands dtd the number as 'pane'
    (2026-10-21: the cmux number before 😈). panes.json is never read as a claim."""
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    ac.claim({"id": "T2", "content": "other"}, "S2", root=tmp_path, now=1000)
    assert ac.load(tmp_path, now=1001)["T1"]["pane"] is None
    (tmp_path / "panes.json").write_text(json.dumps({"S1": 3}))
    got = ac.load(tmp_path, now=1001)
    assert set(got) == {"T1", "T2"}
    assert got["T1"]["pane"] == 3 and got["T2"]["pane"] is None
    (tmp_path / "panes.json").write_text("not json")
    assert ac.load(tmp_path, now=1001)["T1"]["pane"] is None


def test_stale_working_is_not_working(tmp_path):
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    assert ac.working_ids(tmp_path, now=1000 + ac.STALE_SECS + 1) == set()


def test_candidates_substring_and_ambiguity():
    df = ac._did()
    tasks = [TASK, {"id": "T2", "content": "source logging v2 (10) [5]"},
             {"id": "T3", "content": "1 f694 (30) [15]"}]
    assert [t["id"] for t in ac.candidates("f694", tasks, df)] == ["T3"]
    assert [t["id"] for t in ac.candidates("source logging", tasks, df)] == ["T1"]  # exact
    assert len(ac.candidates("logging", tasks, df)) == 2                          # ambiguous
    assert ac.candidates("T2", tasks, df)[0]["id"] == "T2"                        # by id


def test_candidates_hyphenated_skill_name_matches_spaced_weekly_card():
    """2026-11-01: `/d 1-i446` claimed `1-i446 #14: ...` (substring) instead
    of the weekly `1 i446` card, so dtd never showed the review as claimed."""
    df = ac._did()
    tasks = [{"id": "W", "content": "1 i446 (20) [15]"},
             {"id": "R14", "content": "1-i446 #14: neon-rules mismatch P/S/X (1 day each) (10) [0]"}]
    assert [t["id"] for t in ac.candidates("1-i446", tasks, df)] == ["W"]
    assert [t["id"] for t in ac.candidates("1-i446 #14", tasks, df)] == ["R14"]


def _fake_ps(home):
    """A process table where the hook runs under `claude` (pid 100), plus the
    rows in <home>/shells. pytest itself may run under a real session's shell,
    which the hook would otherwise see. The hook's pid is ps's parent or
    grandparent (command substitution forks)."""
    bin_dir = Path(home) / "bin"
    bin_dir.mkdir(exist_ok=True)
    ps = bin_dir / "ps"
    ps.write_text(f"""#!/bin/sh
gp=$(/bin/ps -o ppid= -p $PPID | tr -d ' ')
echo "  $PPID   100 00:01 /bin/sh -c hook"
echo "  $gp   100 00:01 /bin/sh -c hook"
echo "  100     1 02:00 claude --session-id S1"
[ -f {home}/shells ] && cat {home}/shells
exit 0
""")
    ps.chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


def _hook(home, state, sid):
    env = dict(os.environ, HOME=str(home), PATH=_fake_ps(home))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([str(HOOK), state], input=json.dumps({"session_id": sid}),
                          text=True, env=env, capture_output=True, timeout=10)


def _fake_tmux(home):
    """A tmux that only records its arguments: the hook must never reach the real one from a
    test (it would type into the pane running pytest)."""
    log = home / "tmux.log"
    b = home / "bin" / "tmux"
    b.parent.mkdir(exist_ok=True)
    b.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n")
    b.chmod(0o755)
    return log


def _hook_pane(home, state, sid, prompt=None):
    env = dict(os.environ, HOME=str(home), PATH=_fake_ps(home), TMUX_PANE="%99")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("DTD_NOTE_CHILD", None)
    payload = {"session_id": sid, **({"prompt": prompt} if prompt else {})}
    return subprocess.run([str(HOOK), state], input=json.dumps(payload),
                          text=True, env=env, capture_output=True, timeout=10)


def _typed(log, wait=2.5):
    import time
    end = time.time() + wait
    while time.time() < end:
        if log.exists() and "Enter" in log.read_text():
            break
        time.sleep(0.1)
    return log.read_text() if log.exists() else ""


def test_fin_exit_request_is_typed_once_the_turn_is_idle(tmp_path):
    """2026-11-22 (/bug "didn't exit the tab"): /fin filed its tab-close marker but nothing
    ended claude, so the tab stayed open. done_session now leaves by-session/<sid>.exit and the
    Stop hook types /exit, with no claim left in the index (the /fin turn has already cleared it)."""
    root = tmp_path / "vault/z_ibx/agent-claims"
    (root / "by-session").mkdir(parents=True)
    log = _fake_tmux(tmp_path)
    req = root / "by-session" / "S1.exit"
    req.write_text("S1\n")
    assert _hook_pane(tmp_path, "idle", "S1").returncode == 0
    out = _typed(log)
    assert "send-keys -t %99 -l /exit" in out and out.rstrip().endswith("send-keys -t %99 Enter")
    assert not req.exists()                                     # one shot
    log.unlink()
    _hook_pane(tmp_path, "idle", "S1")
    assert _typed(log, wait=1.5) == ""                          # nothing asked, nothing typed
    _hook_pane(tmp_path, "idle", "OTHER")
    assert _typed(log, wait=1.5) == ""


def test_fin_exit_request_is_void_for_a_prompt_queued_behind_fin(tmp_path):
    """2026-11-22 (JM: "if there are further commands after /fin, you can keep the window open,
    means I'm using it for other tasks"): Stop removed the request before the typist's wait, so
    a prompt queued behind /fin, submitted as the turn ended, voided nothing and /exit was typed
    into it. The request now stays through the wait and the typist claims it by removing it."""
    root = tmp_path / "vault/z_ibx/agent-claims"
    (root / "by-session").mkdir(parents=True)
    log = _fake_tmux(tmp_path)
    req = root / "by-session" / "S1.exit"
    req.write_text("S1\n")
    _hook_pane(tmp_path, "idle", "S1")
    assert req.exists()                                          # still voidable during the wait
    _hook_pane(tmp_path, "working", "S1", prompt="next task")    # the queued prompt lands
    assert not req.exists()
    assert _typed(log, wait=2.0) == ""                           # the tab stays open
    req.write_text("S1\n")
    _hook_pane(tmp_path, "idle", "S1")
    _hook_pane(tmp_path, "idle", "S1")                           # two Stops, one /exit
    _typed(log)
    __import__("time").sleep(1.0)
    assert log.read_text().count("-l /exit") == 1
    hook = (Path(__file__).resolve().parents[2] / "scripts/agent-claim-hook.sh").read_text()
    assert hook.index("is void once JM sends") < hook.index("agent_claims.py\" autoclaim")


def test_fin_exit_request_waits_for_background_work_and_dies_with_a_new_prompt(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    (root / "by-session").mkdir(parents=True)
    log = _fake_tmux(tmp_path)
    req = root / "by-session" / "S1.exit"
    req.write_text("S1\n")
    (root / "by-session" / "S1.agents").write_text(f"A1 {int(__import__('time').time())}\n")
    _hook_pane(tmp_path, "idle", "S1")                          # a background agent is still out
    assert _typed(log, wait=1.5) == "" and req.exists()
    _hook_pane(tmp_path, "working", "S1", prompt="one more thing")   # JM kept working here
    assert not req.exists()
    (root / "by-session" / "S1.agents").unlink()
    _hook_pane(tmp_path, "idle", "S1")
    assert _typed(log, wait=1.5) == ""
    req.write_text("S1\n")
    _hook_pane(tmp_path, "release", "S1")                       # session ended some other way
    assert not req.exists()


def test_hook_states_and_release(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    assert _hook(tmp_path, "idle", "S1").returncode == 0
    assert (root / "T1.state").read_text().startswith("idle ")
    _hook(tmp_path, "working", "S1")
    assert (root / "T1.state").read_text().startswith("working ")
    _hook(tmp_path, "working", "OTHER")          # unclaimed session: no-op
    assert set(p.name for p in root.iterdir()) == {"T1.json", "T1.state", "by-session"}
    _hook(tmp_path, "release", "S1")
    assert (root / "T1.json").exists() and (root / "T1.state").read_text().startswith("released ")
    assert not (root / "by-session/S1").exists()


def test_hook_noop_without_claims_dir(tmp_path):
    assert _hook(tmp_path, "working", "S1").returncode == 0


def test_ticker_claim_lines_above_the_line():
    """Every claim gets its own footer line (the footer is above fzf's list
    separator): waiting first as 🕐, then working as spinner + 😈."""
    spec = importlib.util.spec_from_file_location("ticker", HERE / "dtd-ticker.py")
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    assert tk.claim_lines([], 3) == ""
    out = tk.claim_lines([("source logging (30) [20]", True), ("wait (x [5]", False)], 0)
    lines = out.split("\n")[1:]
    assert len(lines) == 2
    assert lines[0] == "🕐 wait x"
    assert "😈 source logging" in lines[1] and "30" not in lines[1]
    assert not any(c in out for c in "(){}")   # a ) would end change-footer()
    assert tk.claim_lines([("a", True)], 0) != tk.claim_lines([("a", True)], 1)  # it spins


def test_ticker_working_claim_shows_even_on_timed_task(tmp_path, monkeypatch):
    """2026-10-16: a working claim on the task JM is timing still gets its
    spinner + 😈 footer line; only an idle claim on it is hidden behind ▶."""
    spec = importlib.util.spec_from_file_location("ticker", HERE / "dtd-ticker.py")
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    root = tmp_path / "claims"
    ac.claim({"id": "T1", "content": "0t (3) [10]"}, "S1", root=root)
    ac.claim({"id": "T2", "content": "notes (10) [5]"}, "S2", root=root)
    ac.set_state("T2", "idle", root=root)
    monkeypatch.setattr(tk.agent_claims, "CLAIMS_DIR", root)
    monkeypatch.setattr(tk.agent_claims.load, "__defaults__", (root, None))
    assert tk._claims("T1") == [("0t (3) [10]", True), ("notes (10) [5]", False)]
    assert tk._claims("T2") == [("0t (3) [10]", True)]


# --- terminal dtd list generator ------------------------------------------
TODAY = dt.date.today().isoformat()
DTD = (HERE / "dtd.sh").read_text()


def _listgen_payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines)) if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    return "\n".join(lines[ps + 1:pe])


def test_terminal_dtd_greys_claimed_task(tmp_path):
    home = tmp_path / "home"
    (home / "i446-monorepo").parent.mkdir(parents=True, exist_ok=True)
    (home / "i446-monorepo").symlink_to(Path.home() / "i446-monorepo")
    ac.claim({"id": "T1", "content": "alpha task (10) [5]"}, "S1",
             root=home / "vault/z_ibx/agent-claims")
    (home / "vault/z_ibx/agent-claims/panes.json").write_text(json.dumps({"S1": 4}))
    tasks = [{"id": i, "content": f"{n} task (10) [5]", "labels": ["i9"], "priority": 1,
              "due": TODAY, "recurring": False} for i, n in (("T1", "alpha"), ("T2", "bravo"))]
    f = {}
    for name, text in (("cache.json", json.dumps({"updated": f"{TODAY}T10:00:00", "today": tasks})),
                       ("done.json", json.dumps({"date": TODAY, "names": [], "ids": {}})),
                       ("removed", ""), ("skipped", ""), ("timer", ""), ("view", "")):
        (tmp_path / name).write_text(text)
        f[name] = str(tmp_path / name)
    (tmp_path / "removed.ids").write_text("")
    (tmp_path / "lg.py").write_text(_listgen_payload())
    r = subprocess.run([sys.executable, str(tmp_path / "lg.py"), f["cache.json"], f["done.json"],
                        f["removed"], TODAY, "120", f["skipped"], f["timer"], f["view"]],
                       capture_output=True, text=True, env=dict(os.environ, HOME=str(home)))
    assert r.returncode == 0, r.stderr
    alpha = next(l for l in r.stdout.splitlines() if "alpha" in l)
    bravo = next(l for l in r.stdout.splitlines() if "bravo" in l)
    assert "😈" in alpha and "\x1b[38;2;110;110;110m" in alpha
    assert " 4😈 " in alpha   # the claiming session's cmux number sits before the glyph
    assert "😈" not in bravo
    # claimed rows sit at the top of the list (2026-10-13)
    assert r.stdout.index("alpha") < r.stdout.index("bravo")


# ── /d fast path (2026-10-05): the UserPromptSubmit hook claims before the
#    model starts, so dtd shows it in ~1s instead of after skill loading. ──

class _FakeDid:
    ANNOT_RE = __import__("re").compile(r"\s*[\(\[]\d+[\)\]]")

    def __init__(self, tasks):
        self._tasks = tasks

    def load_task_queue(self):
        return {"today": self._tasks}

    def match_todoist_task(self, q, tasks):
        return None


@pytest.fixture
def hooked(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "CLAIMS_DIR", tmp_path)
    monkeypatch.setattr(ac, "push", lambda: None)
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "T2", "content": "source review"}]))
    # claim()/release() take root defaults bound at def time; route them to tmp
    real_claim, real_release = ac.claim, ac.release
    monkeypatch.setattr(ac, "claim", lambda t, s, root=None, now=None: real_claim(t, s, root=tmp_path, now=now))
    monkeypatch.setattr(ac, "release", lambda s, root=None: real_release(s, root=tmp_path))
    return tmp_path


def _fast(prompt, session="S9"):
    return ac.hook(json.dumps({"session_id": session, "prompt": prompt}))


def test_hook_claims_d_prompt(hooked):
    out = _fast("/d source logging: build the adapter")
    assert "😈 claimed: source logging" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "T1"


def test_hook_ignores_ordinary_prompts(hooked):
    assert _fast("can you check the source logging") == ""
    assert _fast("/do something") == ""     # /d must be a whole command
    assert not (hooked / "by-session").exists()


def test_hook_ambiguous_and_release(hooked):
    assert "ambiguous" in _fast("/claim source")
    _fast("/d source logging")
    assert "released" in _fast("/d off")
    assert not (hooked / "by-session" / "S9").exists()


def test_hook_new_creates_then_claims(hooked, monkeypatch):
    """`/d new <task>` (2026-10-05): attach work that isn't in dtd yet."""
    made = []
    monkeypatch.setattr(ac, "new_task", lambda c: made.append(c) or {"id": "N1", "content": c})
    out = _fast("/d new draft xbox memo: write the outline")
    assert made == ["draft xbox memo"]
    assert "created and 😈 claimed: draft xbox memo" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "N1"


def test_hook_unmatched_d_creates_then_claims(hooked, monkeypatch):
    """2026-10-06: `/d wire up /d to janus [5]` matched nothing and claimed
    nothing; "that should be the default behavior" -- no match = create it."""
    made = []
    monkeypatch.setattr(ac, "new_task", lambda c: made.append(c) or {"id": "N2", "content": c})
    out = _fast("/d wire up /d to janus [5]")
    assert made == ["wire up /d to janus [5]"]
    assert "created and 😈 claimed: wire up /d to janus [5]" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "N2"


def test_hook_matched_d_does_not_create(hooked, monkeypatch):
    monkeypatch.setattr(ac, "new_task", lambda c: (_ for _ in ()).throw(AssertionError("created")))
    assert "😈 claimed: source logging" in _fast("/d source logging")


@pytest.mark.parametrize("prompt", [
    "/dt source logging: build the adapter",
    "<command-name>/dt</command-name><command-args>source logging</command-args>",
])
def test_dt_claims_then_starts_resolved_task(hooked, monkeypatch, prompt):
    started = []

    def start(task):
        assert (hooked / "by-session" / "S9").read_text().strip() == task["id"]
        started.append(task)
        return "Started: source logging"

    monkeypatch.setattr(ac, "start_task_timer", start)
    monkeypatch.setattr(ac, "_retitle", lambda *a: None)
    out = _fast(prompt)
    assert started == [TASK]
    assert "😈 claimed:" in out and "Started: source logging" in out


def test_dt_code_resolves_before_starting_timer(hooked, monkeypatch):
    import types
    started = []
    monkeypatch.setattr(ac, "_dtd_codes", lambda: types.SimpleNamespace(
        lookup=lambda q, tasks: ["T1"] if q == "wh" else [], is_code=lambda q: q == "wh"))
    monkeypatch.setattr(ac, "start_task_timer", lambda t: started.append(t["id"]) or "Started")
    monkeypatch.setattr(ac, "_retitle", lambda *a: None)
    assert "😈 claimed:" in _fast("/dt wh")
    assert started == ["T1"]


@pytest.mark.parametrize("prompt", ["/dt new draft xbox memo", "/dt draft xbox memo"])
def test_dt_created_task_starts_timer(hooked, monkeypatch, prompt):
    started = []
    monkeypatch.setattr(ac, "new_task", lambda c: {"id": "N1", "content": c, "labels": ["i9"]})
    monkeypatch.setattr(ac, "start_task_timer", lambda t: started.append(t) or "Started")
    monkeypatch.setattr(ac, "_retitle", lambda *a: None)
    assert "created and 😈 claimed:" in _fast(prompt)
    assert started == [{"id": "N1", "content": "draft xbox memo", "labels": ["i9"]}]


@pytest.mark.parametrize("prompt", [
    "/d source logging", "/dt source", "/dt", "/dt off", "/dt release",
    "/dt done", "/dt 999", "/dtd source logging",
])
def test_dt_unresolved_or_control_prompts_never_start_timer(hooked, monkeypatch, prompt):
    monkeypatch.setattr(ac, "start_task_timer", lambda t: pytest.fail("unexpected timer start"))
    monkeypatch.setattr(ac, "_retitle", lambda *a: None)
    monkeypatch.setattr(ac, "row_task_id", lambda n: None)
    _fast(prompt)


def test_dt_bare_and_ambiguous_context_preserves_timer_command(hooked):
    out = _fast("/dt")
    assert "bare /dt," in out and "agent_claims.py dt <id>" in out
    assert 'dt new "<thread goal>' in out
    out = _fast("/dt source")
    assert "ambiguous, nothing claimed" in out and "agent_claims.py dt <id>" in out


def test_dt_timer_failure_keeps_claim_and_cli_fails(hooked, monkeypatch, capsys):
    def fail(task):
        raise RuntimeError("Toggl unavailable")

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S9")
    monkeypatch.setattr(ac, "_retitle", lambda *a: None)
    monkeypatch.setattr(ac, "start_task_timer", fail)
    assert ac.main(["dt", "source logging"]) == 1
    assert "Timer failed: Toggl unavailable" in capsys.readouterr().out
    assert (hooked / "by-session" / "S9").read_text().strip() == "T1"


def test_task_timer_uses_literal_title_project_and_task_link(hooked, monkeypatch):
    import types
    tid = "6j3rV8VVw9vwJxxc"
    task = {"id": tid, "content": "write review 9-10 (30) [20]", "labels": ["hcmc"],
            "due": {"date": "2026-11-20"}}
    calls = []
    current = {"id": 100, "description": "old"}

    def resolve(raw):
        calls.append(("resolve", raw))
        return "write review 9-10", "hcmc", ["review"]

    def stop(args):
        calls.append(("stop", args))

    def start(args):
        nonlocal current
        calls.append(("start", args))
        current = {"id": 200, "description": "write review 9-10"}
        print("Started: write review 9-10")

    cli = types.SimpleNamespace(
        PROJECT_MAP={"hcmc": 1},
        toggl_api=types.SimpleNamespace(get_current_cached=lambda: current),
        cmd_stop=stop, cmd_start=start)
    monkeypatch.setattr(ac, "_timer_tools", lambda: (types.SimpleNamespace(resolve=resolve), cli))
    assert ac.start_task_timer(task) == "Started: write review 9-10"
    assert calls == [
        ("resolve", "write review 9-10 @hcmc"),
        ("stop", []),
        ("start", ["write review 9-10", "hcmc", "review", "--task", tid, "--due", "2026-11-20"]),
    ]
    assert ac.timer_link(tid, root=hooked)["entry_id"] == 200
    calls.clear()
    assert ac.start_task_timer(task) == "Timer already running: write review 9-10"
    assert calls == []


def test_task_timer_stop_failure_never_starts_another(hooked, monkeypatch):
    import types

    def fail(args):
        raise RuntimeError("stop failed")

    cli = types.SimpleNamespace(
        PROJECT_MAP={}, toggl_api=types.SimpleNamespace(get_current_cached=lambda: {}),
        cmd_stop=fail, cmd_start=lambda args: pytest.fail("started after failed stop"))
    tg = types.SimpleNamespace(resolve=lambda raw: (raw, "", []))
    monkeypatch.setattr(ac, "_timer_tools", lambda: (tg, cli))
    with pytest.raises(RuntimeError, match="stop failed"):
        ac.start_task_timer(TASK)


def test_dt_runs_real_timer_commands_without_reparsing_title(hooked, monkeypatch, capsys):
    import types
    tid = "6j3rV8VVw9vwJxxc"
    task = {"id": tid, "content": "write review 9-10 (30) [20]", "labels": ["hcmc"]}
    tg, cli = ac._timer_tools()
    calls = []
    current = {"id": 100, "description": "old", "duration": -1}

    def stop(entry_id):
        nonlocal current
        calls.append(("stop", entry_id))
        old, current = current, None
        return old

    def start(desc, project_id, tags, start_time=None):
        nonlocal current
        calls.append(("start", desc, project_id, tags))
        current = {"id": 200, "description": desc, "duration": -1}
        return current

    monkeypatch.setattr(cli.toggl_api, "get_current_cached", lambda: current)
    monkeypatch.setattr(cli.toggl_api, "stop_timer", stop)
    monkeypatch.setattr(cli.toggl_api, "start_timer", start)
    monkeypatch.setattr(cli, "_claims", lambda: types.SimpleNamespace(
        link_timer=lambda *a: ac.link_timer(*a, root=hooked),
        unlink_timer=lambda **kw: ac.unlink_timer(root=hooked, **kw)))
    monkeypatch.setattr(ac, "_timer_tools", lambda: (tg, cli))
    monkeypatch.setattr(ac, "_retitle", lambda *a: None)
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([task]))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S9")
    assert ac.main(["dt", tid]) == 0
    assert "Started: write review 9-10" in capsys.readouterr().out
    assert calls == [("stop", 100), ("start", "write review 9-10", cli.PROJECT_MAP["hcmc"], None)]
    assert ac.timer_link(tid, root=hooked)["entry_id"] == 200
    assert ac.main(["dt", tid]) == 0
    assert "Timer already running:" in capsys.readouterr().out
    assert len(calls) == 2


def test_hook_row_number_with_runon_request_claims_row(hooked, monkeypatch):
    """2026-10-28: `/d 21 can you take a look ... https://x` split at the
    URL's colon, queried "21 can you ... https", and created that as a task."""
    monkeypatch.setattr(ac, "new_task", lambda c: (_ for _ in ()).throw(AssertionError("created")))
    monkeypatch.setattr(ac, "row_task_id", lambda n: {2: "T2"}.get(n))
    out = _fast("/d 2 can you take a look at this? https://example.com/board.pdf")
    assert "😈 claimed: source review" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "T2"


def test_hook_row_number_runon_with_no_row_creates_nothing(hooked, monkeypatch):
    monkeypatch.setattr(ac, "new_task", lambda c: (_ for _ in ()).throw(AssertionError("created")))
    monkeypatch.setattr(ac, "row_task_id", lambda n: None)
    assert "no dtd row 21" in _fast("/d 21 can you take a look at this")


def test_hook_number_led_task_name_still_matches_by_name(hooked, monkeypatch):
    """`/d 1 hpm` names the "1 hpm" card; it is not row 1."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "H1", "content": "1 hpm (60) [60]"}]))
    monkeypatch.setattr(ac, "row_task_id", lambda n: "T1")
    assert "😈 claimed: 1 hpm" in _fast("/d 1 hpm")
    assert (hooked / "by-session" / "S9").read_text().strip() == "H1"


def test_hook_url_colon_does_not_end_task_name(hooked, monkeypatch):
    made = []
    monkeypatch.setattr(ac, "new_task", lambda c: made.append(c) or {"id": "N3", "content": c})
    _fast("/d read https://example.com/doc")
    assert made == ["read https://example.com/doc"]


# ── bare [N] = the claimed task's value, never a completion (2026-10-05:
#    a bare [200] got logged as +200 immediately; points are opportunity
#    until JM completes the task in dtd with ⌥↵) ──

def test_bare_value_sets_claimed_task_content_not_points(hooked, monkeypatch):
    import types
    _fast("/d source logging")
    calls = []
    fake = types.SimpleNamespace(
        get_task=lambda tid: {"id": tid, "content": "source logging [20]"},
        _request=lambda m, path, body=None: calls.append((m, path, body)))
    monkeypatch.setitem(sys.modules, "todoist", fake)
    monkeypatch.setattr(ac.subprocess if hasattr(ac, "subprocess") else __import__("subprocess"),
                        "Popen", lambda *a, **k: None)
    out = _fast("[200]")
    assert calls == [("POST", "/tasks/T1", {"content": "source logging [200]"})]
    assert "NOT a completion" in out and "do NOT log points" in out


def test_bare_value_without_claim_is_left_to_the_model(hooked):
    assert _fast("[200]", session="nobody") == ""


def test_zsh_hook_prefilter_routes_bare_value_to_python():
    """The hook only spawns python for /d, /claim, or a bare [N] prompt."""
    src = HOOK.read_text()
    assert r'\[[0-9]+\][[:space:]]*"' in src


# ── /0t claims its own dtd card while it runs (2026-10-07: "calling /0t
#    invokes /d for the task 0t until it automatically marks it complete") ──

def test_skill_prompt_claims_its_card(hooked, monkeypatch):
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "Z0", "content": "0t (3) [10]"},
                                                      {"id": "Z1", "content": "-1t"}]))
    out = _fast("/0t")
    assert "😈 claimed: 0t (3) [10]" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "Z0"


def test_skill_prompt_never_creates_when_card_is_gone(hooked, monkeypatch):
    monkeypatch.setattr(ac, "new_task", lambda c: (_ for _ in ()).throw(AssertionError("created")))
    assert _fast("/0t") == ""          # 0t already done today: nothing to claim
    assert _fast("/0tx") == ""          # must be the whole command
    assert not (hooked / "by-session").exists()


def test_zsh_hook_prefilter_routes_0t_to_python():
    assert _hook_alternations() == ["d|claim|dt|" + "|".join(ac.SKILL_CLAIMS)] * 2


def test_1s897_claims_weekly_card_and_keeps_it(hooked, monkeypatch):
    """2026-10-07: /1s897 claims '1 s897' like /0t, but has a manual part, so
    nothing releases or completes it; JM closes the card in dtd."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "W1", "content": "1 s897 (25) [30]"},
                                                      {"id": "W2", "content": "ibx s897 (15) [5]"},
                                                      {"id": "W3", "content": "s897 (120) [30]"}]))
    out = _fast("/1s897 7.4")
    assert "😈 claimed: 1 s897 (25) [30]" in out and "leave the card open" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "W1"


def test_1hcmc_claims_weekly_card_and_releases_it(hooked, monkeypatch):
    """2026-10-08: /1hcmc claims '1 hcmc'; the plain 'hcmc' card must not make
    the match ambiguous. 2026-10-17: the skill now runs the media survey and
    closes the card itself, so it is a releaser."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "H1", "content": "1 hcmc (20) [20]"},
                                                      {"id": "H2", "content": "hcmc (60) [10]"}]))
    out = _fast("/1hcmc 10.1")
    assert "😈 claimed: 1 hcmc (20) [20]" in out and "releases the claim" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "H1"


def test_1s_claims_weekly_card_and_keeps_it(hooked, monkeypatch):
    """2026-10-11: /1s claims '1s' and keeps it (the survey is the manual
    part); neither '1 s meta' nor '1 s897' may steal the match, and /1s897
    must still claim its own card."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "S1", "content": "1s (20) [45]"},
                                                      {"id": "S2", "content": "1 s meta (20) [30]"},
                                                      {"id": "S3", "content": "1 s897 (25) [30]"}]))
    out = _fast("/1s")
    assert "😈 claimed: 1s (20) [45]" in out and "leave the card open" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "S1"
    assert "😈 claimed: 1 s897 (25) [30]" in _fast("/1s897 7.4", session="S8")


def _hook_alternations() -> list[str]:
    import re
    return re.findall(r"/\(((?:d\|claim)\|[^)]*)\)", HOOK.read_text())


def test_hook_prefilter_lists_exactly_the_skill_claims():
    """The zsh prefilter keeps python off the hot path, so it must name every
    SKILL_CLAIMS key (prompt form and <command-name> form), no more, no less."""
    assert _hook_alternations() == ["d|claim|dt|" + "|".join(ac.SKILL_CLAIMS)] * 2


def test_skill_claim_is_exact_name_only(hooked, monkeypatch):
    """2026-10-11: a weekly/quarterly card that isn't on today's list must
    claim nothing, never a look-alike via the word-overlap fallback."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "Q2", "content": "3s prep notes (10) [5]"},
                                                      {"id": "Q3", "content": "moral audit followups (5) [5]"}]))
    assert _fast("/3s") == ""
    assert _fast("/moralaudit", session="S8") == ""


def test_release_mode_and_odd_names(hooked, monkeypatch):
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "I9", "content": "1 i9 (10) [40]"},
                                                      {"id": "B1", "content": "早餐 (15) [5]"},
                                                      {"id": "G2", "content": "1 -2g (13) [20]"},
                                                      {"id": "Q4", "content": "4g checkin (15) [40]"}]))
    assert "😈 claimed: 1 i9 (10) [40]" in _fast("/1i9") and "releases the claim" in _fast("/1i9")
    assert "😈 claimed: 早餐" in _fast("/早餐 toast", session="S1") and "releases" in _fast("/早餐", session="S1")
    # 2026-10-19: /1-2g applies its own edits and closes the card, so it releases.
    assert "😈 claimed: 1 -2g" in _fast("/1-2g", session="S2") and "releases the claim" in _fast("/1-2g", session="S2")
    assert "😈 claimed: 4g checkin" in _fast("/2026-4g-checkin 2026-Q3", session="S3")


def test_no_skill_is_both_linked_and_declined():
    assert not set(ac.SKILL_CLAIMS) & set(ac.SKILL_NO_CLAIM)


def test_notes_claims_its_card_and_says_skill_releases(hooked, monkeypatch):
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "N7", "content": "notes (3) [8]"}]))
    out = _fast("/notes")
    assert "😈 claimed: notes (3) [8]" in out and "releases the claim" in out
    assert _fast("/notesy", session="S8") == ""


def test_hook_unwraps_expanded_slash_command(hooked, monkeypatch):
    """2026-10-07: /notes reached the hook as <command-name> tags, not '/notes',
    and claimed nothing. Both the python matcher and the zsh prefilter must
    accept the wrapped form."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "N7", "content": "notes (3) [8]"}]))
    wrapped = ("<command-message>notes</command-message>\n<command-name>/notes</command-name>")
    assert "😈 claimed: notes (3) [8]" in _fast(wrapped)
    assert "😈 claimed: source logging" in _fast(
        "<command-message>d</command-message>\n<command-name>/d</command-name>\n"
        "<command-args>source logging: go</command-args>", session="S7")
    assert "command-name>[[:space:]]*/(d|claim|" in HOOK.read_text()


def test_0g_claims_its_card_and_says_skill_releases(hooked, monkeypatch):
    """2026-10-08: /0g claims the '0g' card while it runs, then releases it once
    did-fast closes the card. Goal args after /0g must not stop the match, and
    a #0g goal that merely mentions 0g must not make it ambiguous."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "G0", "content": "0g (4) [8]"},
                                                      {"id": "G1", "content": "fix 0g dedup {8}"}]))
    out = _fast("/0g fix 0g dedup {8}, call mom (15)")
    assert "😈 claimed: 0g (4) [8]" in out and "releases the claim" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "G0"
    assert _fast("/0gx", session="S8") == ""


# ── bare `/d` (2026-10-19): claim the task this THREAD is doing; the hook
#    only hands the model the surfaced list, the model picks or creates. ──

def test_bare_d_hands_model_the_surfaced_list_and_claims_nothing(hooked):
    out = _fast("/d")
    assert out.startswith("[claim hook] bare /d")
    assert "T1: source logging (30) [20]" in out and "T2: source review" in out
    assert "agent_claims.py claim <id>" in out and "agent_claims.py new" in out
    assert "THREAD" in out, "must steer the model to the thread's deliverable, not the last message"
    assert ac.load(hooked) == {}, "bare /d must not claim or create anything in the hook"


def test_bare_d_with_trailing_space_is_still_bare(hooked):
    assert _fast("/d ").startswith("[claim hook] bare /d")
    assert ac.load(hooked) == {}


def test_bare_d_lists_only_surfaced_sections(hooked):
    tq = {"today": [{"id": "A", "content": "alpha (10) [5]"}],
          "关键路径": [{"id": "C", "content": "critical"}],
          "0neon": [{"id": "H", "content": "0t"}],
          "1neon": [{"id": "W", "content": "1 xk88"}]}
    out = ac.bare_d_line(ac.all_tasks(tq), None, tq)
    assert "C: critical" in out and "A: alpha" in out
    assert "H: 0t" not in out and "W: 1 xk88" not in out, "habit cards are never a thread's task"
    assert out.index("C: critical") < out.index("A: alpha"), "critical path first"


def test_zsh_hook_prefilter_routes_bare_d_to_python():
    """`"prompt": "/d"` must reach python: the alternation allows a closing quote after /d."""
    src = HOOK.read_text()
    assert '([[:space:]]|")' in src


# --- background subagents keep the claim working (2026-10-19) ---------------
def _hook_json(home, state, payload):
    env = dict(os.environ, HOME=str(home), PATH=_fake_ps(home))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([str(HOOK), state], input=json.dumps(payload),
                          text=True, env=env, capture_output=True, timeout=10)


def _state(root):
    return (root / "T1.state").read_text().split()[0]


def test_stop_keeps_working_while_background_subagent_runs(tmp_path):
    """Bug 2026-10-19: Stop wrote idle while an Agent launched in the background
    was still running, so dtd showed the card as waiting on JM mid-work."""
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    assert _hook_json(tmp_path, "subagent-start", {"session_id": "S1", "agent_id": "a1"}).returncode == 0
    agents = root / "by-session/S1.agents"
    assert agents.read_text().split()[0] == "a1" and _state(root) == "working"
    _hook(tmp_path, "idle", "S1")                      # main turn ends, agent still running
    assert _state(root) == "working"
    _hook_json(tmp_path, "subagent-start", {"session_id": "S1", "agent_id": "a2"})
    _hook_json(tmp_path, "subagent-stop", {"session_id": "S1", "agent_id": "a1"})
    assert agents.read_text().split()[0] == "a2"       # a1 dropped, a2 still live
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "working"
    _hook_json(tmp_path, "subagent-stop", {"session_id": "S1", "agent_id": "a2"})
    assert not agents.exists() and _state(root) == "working"   # parent resumes to read the result
    _hook(tmp_path, "idle", "S1")                      # ...and its own Stop goes idle
    assert _state(root) == "idle"


def test_subagent_started_before_the_claim_still_counts(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    _hook_json(tmp_path, "subagent-start", {"session_id": "S1", "agent_id": "a1"})
    assert (root / "by-session/S1.agents").exists()    # tracked even with no claim yet
    ac.claim(TASK, "S1", root=root, now=1000)
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "working"


def test_stale_subagent_entry_cannot_pin_the_card(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    (root / "by-session/S1.agents").write_text("dead 100\n")   # epoch 100: long gone
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "idle"


# --- background shells keep the claim working too (2026-11-05) ---------------
SNAPSHOT_SHELL = "/bin/zsh -c source /Users/x/.claude/shell-snapshots/snapshot-zsh-1.sh && eval 'pytest'"


def test_stop_keeps_working_while_background_shell_runs(tmp_path):
    """Bug 2026-11-05: a run_in_background Bash fires no Subagent event, so Stop
    wrote idle with the shell still going and dtd showed "waiting on JM"."""
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    shells = tmp_path / "shells"
    shells.write_text(f"  200   100 03:33 {SNAPSHOT_SHELL}\n")
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "working"
    shells.unlink()                                    # shell exits, session resumes
    _hook(tmp_path, "idle", "S1")                      # ...and its own Stop goes idle
    assert _state(root) == "idle"


@pytest.mark.parametrize("etime", ["01:00:00", "2-03:00:00"])
def test_hour_old_background_shell_cannot_pin_the_card(tmp_path, etime):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    (tmp_path / "shells").write_text(f"  200   100 {etime} {SNAPSHOT_SHELL}\n")
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "idle"


def test_another_sessions_background_shell_does_not_count(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    (tmp_path / "shells").write_text(f"  200   999 03:33 {SNAPSHOT_SHELL}\n")
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "idle"


def test_mcp_child_of_claude_is_not_a_background_shell(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    (tmp_path / "shells").write_text("  200   100 03:33 node /opt/homebrew/bin/google-calendar-mcp\n")
    _hook(tmp_path, "idle", "S1")
    assert _state(root) == "idle"


def test_busy_ignores_background_shells(tmp_path):
    # Nothing re-checks after Stop, so a dev server would hold the tab black.
    (tmp_path / "shells").write_text(f"  200   100 03:33 {SNAPSHOT_SHELL}\n")
    assert _hook_json(tmp_path, "busy", {"session_id": "S1"}).returncode == 1


def test_release_drops_the_agents_file(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    _hook_json(tmp_path, "subagent-start", {"session_id": "S1", "agent_id": "a1"})
    _hook(tmp_path, "release", "S1")
    assert not (root / "by-session/S1.agents").exists()


def test_busy_mode_tells_stop_a_background_agent_is_running(tmp_path):
    # 2026-11-05: blue means waiting on JM, so the Stop colour hook skips it
    # while the session's background agents still run.
    assert _hook_json(tmp_path, "busy", {"session_id": "S1"}).returncode == 1
    _hook_json(tmp_path, "subagent-start", {"session_id": "S1", "agent_id": "a1"})
    assert _hook_json(tmp_path, "busy", {"session_id": "S1"}).returncode == 0
    _hook_json(tmp_path, "subagent-stop", {"session_id": "S1", "agent_id": "a1"})
    assert _hook_json(tmp_path, "busy", {"session_id": "S1"}).returncode == 1


def test_stop_colour_hook_is_gated_on_busy():
    cfg = json.loads((Path.home() / ".claude/settings.json").read_text())
    cmds = [h.get("command", "") for g in cfg["hooks"]["Stop"] for h in g["hooks"]]
    blue = next(c for c in cmds if "term-color.sh blue" in c)
    assert "agent-claim-hook.sh busy ||" in blue


def test_subagent_hooks_are_registered_in_claude_settings():
    cfg_path = Path.home() / ".claude/settings.json"
    if not cfg_path.exists():
        pytest.skip("no ~/.claude/settings.json on this host")
    hooks = json.loads(cfg_path.read_text()).get("hooks", {})
    for ev, mode in (("SubagentStart", "subagent-start"), ("SubagentStop", "subagent-stop")):
        cmds = [h.get("command", "") for g in hooks.get(ev, []) for h in g.get("hooks", [])]
        assert any(f"agent-claim-hook.sh {mode}" in c for c in cmds), ev


# --- per-host mirror (2026-10-25) -------------------------------------------
# "you ran /notes but didn't claim the task from dtd": an Ix session claimed the
# notes card at 07:03:34; at 07:03:45 a Straylight session's hook pushed its
# store with `rsync --delete` into Ix's root and wiped it. Other hosts now
# mirror into remote/<host>/ and every reader merges.
PUSH = HERE.parent.parent / "scripts" / "agent-claims-push.sh"


def test_push_mirrors_into_remote_host_dir_never_ix_root(tmp_path):
    home = tmp_path / "home"
    (home / "vault/z_ibx/agent-claims").mkdir(parents=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "rsync.args"
    (bin_ / "rsync").write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > {log}\n')
    (bin_ / "hostname").write_text("#!/bin/sh\necho Straylight-Refit\n")
    for f in ("rsync", "hostname"):
        (bin_ / f).chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_}:{os.environ['PATH']}"}
    subprocess.run(["zsh", str(PUSH)], env=env, check=True, timeout=10)
    args = log.read_text().split("\n")
    assert "--delete" in args
    assert args[-2] == "ix:vault/z_ibx/agent-claims/remote/straylight-refit/"
    assert "--exclude=/remote/" in args


# 2026-11-02: /1-1n claimed its card on Ix but it sat at #20 in Straylight's
# dtd: Ix's store reached no other host. Other hosts now pull it into remote/ix/.
PULL = HERE.parent.parent / "scripts" / "agent-claims-pull.sh"


def _pull_env(tmp_path, host):
    home = tmp_path / "home"
    (home / "vault/z_ibx/agent-claims").mkdir(parents=True)
    (home / ".claude").mkdir()
    (home / ".claude/.host-name").write_text(host)
    src = tmp_path / "ix-store"
    ac.claim({"id": "T9", "content": "1 -1n (10) [15]"}, "IX", root=src, now=1000)
    ac.claim(TASK, "ST", root=src / "remote" / "straylight-refit", now=1000)
    (src / "by-session").mkdir(exist_ok=True)
    (src / "panes.json").write_text('{"IX": 9}')
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(tmp_path),
           "AGENT_CLAIMS_PULL_SRC": f"{src}/"}
    return home / "vault/z_ibx/agent-claims", env


def test_pull_brings_ix_claims_into_remote_ix(tmp_path):
    store, env = _pull_env(tmp_path, "straylight")
    subprocess.run(["zsh", str(PULL)], env=env, check=True, timeout=10)
    mirror = store / "remote" / "ix"
    assert (mirror / "T9.json").exists() and (mirror / "T9.state").exists()
    # Not this host's own pushed copy, Ix's pane map or its hook index.
    assert not (mirror / "remote").exists()
    assert not (mirror / "panes.json").exists()
    assert not (mirror / "by-session").exists()
    got = ac.load(store, now=1010)
    assert got["T9"]["working"] and set(got) == {"T9"}


def test_pull_is_noop_on_ix(tmp_path):
    store, env = _pull_env(tmp_path, "ix")
    subprocess.run(["zsh", str(PULL)], env=env, check=True, timeout=10)
    assert not (store / "remote").exists()


def test_dtd_watcher_runs_the_pull():
    src = (HERE / "dtd.sh").read_text()
    watcher = src[src.index('while [[ -f "$DTD_PORT" ]]; do'):]
    assert "scripts/agent-claims-pull.sh" in watcher[:watcher.index("_dtd_tz_refresh")]


def test_load_merges_remote_host_dirs(tmp_path):
    ac.claim(TASK, "IX", root=tmp_path, now=1000)
    remote = tmp_path / "remote" / "straylight-refit"
    ac.claim({"id": "T2", "content": "janus ios"}, "ST", root=remote, now=1000)
    (remote / "panes.json").write_text('{"ST": 3}')
    got = ac.load(tmp_path, now=1010)
    assert got["T1"]["working"] and got["T2"]["working"]
    assert got["T2"]["pane"] == 3


def test_same_card_on_two_hosts_newer_state_wins(tmp_path):
    remote = tmp_path / "remote" / "straylight-refit"
    ac.claim(TASK, "ST", root=remote, now=1000)
    ac.set_state("T1", "idle", now=1005, root=remote)
    ac.claim(TASK, "IX", root=tmp_path, now=1020)
    got = ac.load(tmp_path, now=1030)
    assert got["T1"]["session"] == "IX" and got["T1"]["working"]


def test_clear_task_clears_remote_copy_too(tmp_path):
    remote = tmp_path / "remote" / "straylight-refit"
    ac.claim(TASK, "ST", root=remote, now=1000)
    assert ac.clear_task("T1", tmp_path)
    assert ac.load(tmp_path, now=1010) == {}


# --- turn notes (2026-10-25) ------------------------------------------------
def _transcript(tmp_path, entries):
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return p


def test_turn_text_takes_last_real_prompt_and_replies(tmp_path):
    t = _transcript(tmp_path, [
        {"type": "user", "message": {"content": "old prompt"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "old reply"}]}},
        {"type": "user", "message": {"content": "fix the stale list"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"description": "Run tests"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed it."}]}},
    ])
    prompt, reply, tools = ac.turn_text(t)
    assert prompt == "fix the stale list" and reply == "Fixed it." and tools == ["Bash: Run tests"]


def test_note_turn_posts_demon_summary_on_held_and_just_done_tasks(tmp_path):
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    ac.claim({"id": "T2", "content": "other"}, "S1", root=tmp_path, now=1000, also=True)
    ac.mark_done("S1", ["T2"], root=tmp_path)   # left the index, still gets this turn's note
    t = _transcript(tmp_path, [
        {"type": "user", "message": {"content": "do it"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
    ])
    posted, prompts = [], []
    got = ac.note_turn(json.dumps({"session_id": "S1", "transcript_path": str(t)}), root=tmp_path,
                       post=lambda tid, c: posted.append((tid, c)),
                       summarizer=lambda p: prompts.append(p) or "I did X. Next is Y.")
    assert got == ["T1", "T2"]
    assert posted == [("T1", "😈 I did X. Next is Y."), ("T2", "😈 I did X. Next is Y.")]
    assert "source logging" in prompts[0] and "two sentences" in prompts[0]
    # The done queue is consumed: the next turn notes only what is still held.
    assert ac.note_turn(json.dumps({"session_id": "S1", "transcript_path": str(t)}), root=tmp_path,
                        post=lambda *a: None, summarizer=lambda p: "x") == ["T1"]


def test_note_turn_skips_unclaimed_sessions_and_stop_hook_loops(tmp_path):
    boom = lambda *a: (_ for _ in ()).throw(AssertionError("must not run"))
    assert ac.note_turn(json.dumps({"session_id": "nobody"}), root=tmp_path, post=boom, summarizer=boom) == []
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    assert ac.note_turn(json.dumps({"session_id": "S1", "stop_hook_active": True}),
                        root=tmp_path, post=boom, summarizer=boom) == []


def test_stop_hook_launches_note_only_for_claimed_sessions():
    src = HOOK.read_text()
    assert 'agent_claims.py" note' in src and "DTD_NOTE_CHILD" in src
    assert src.index('agent_claims.py" note') < src.index('[[ -n "$sid" && -f "$D/by-session/$sid" ]] || exit 0')


# ── auto-claim (2026-11-14): the first ordinary prompt matches a dtd task ──

def _auto(prompt, session="S9", **extra):
    return ac.autoclaim(json.dumps({"session_id": session, "prompt": prompt, **extra}),
                        compute=extra.pop("_compute", None))


@pytest.fixture
def auto(hooked, monkeypatch):
    monkeypatch.setattr(ac, "_task_queue", lambda: {"today": [TASK, {"id": "T2", "content": "source review"}]})
    monkeypatch.setattr(ac, "_retitle", lambda s: None)
    return hooked


def test_autoclaim_matches_first_prompt(auto, monkeypatch):
    monkeypatch.setattr(ac, "_haiku_match", lambda p, c: "T1")
    out = _auto("let's build the source logging adapter")
    assert "auto-claimed: source logging" in out and "/d off" in out
    assert (auto / "by-session" / "S9").read_text().strip() == "T1"
    assert (auto / "by-session" / "S9.auto").exists()


def test_autoclaim_runs_once_and_never_creates(auto, monkeypatch):
    calls = []
    monkeypatch.setattr(ac, "_haiku_match", lambda p, c: calls.append(p) or None)
    monkeypatch.setattr(ac, "new_task", lambda c: pytest.fail("autoclaim must not create tasks"))
    assert _auto("how long does coffee stay warm") == ""
    assert (auto / "by-session" / "S9.auto").exists() and not (auto / "by-session" / "S9").exists()
    # The marker stops a second attempt even when Haiku would now match.
    monkeypatch.setattr(ac, "_haiku_match", lambda p, c: "T1")
    assert _auto("now the source logging adapter") == ""
    assert calls == ["how long does coffee stay warm"]


def test_autoclaim_skips_commands_values_claimed_and_forks(auto, monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "_haiku_match", lambda p, c: "T1")
    assert _auto("/do source logging") == ""            # a command is never a first prompt
    assert _auto("[40]") == ""
    assert not (auto / "by-session" / "S9.auto").exists()
    _fast("/d source review", session="S8")            # already holds a claim: untouched
    assert _auto("source logging please", session="S8") == ""
    assert (auto / "by-session" / "S8").read_text().strip() == "T2"
    tr = tmp_path / "fork.jsonl"                        # a fork carries the whole thread
    tr.write_text('{"type":"user","message":{"content":"a"}}\n{"type":"assistant"}\n'
                  '{"type":"user","message":{"content":"b"}}\n')
    assert _auto("continue", session="S7", transcript_path=str(tr)) == ""
    assert (auto / "by-session" / "S7.auto").exists() and not (auto / "by-session" / "S7").exists()
    assert ac._transcript_user_turns(str(tr)) == 2
    fresh = tmp_path / "fresh.jsonl"
    fresh.write_text('{"type":"user","message":{"content":"first"}}\n')
    assert "auto-claimed" in _auto("source logging adapter", session="S6", transcript_path=str(fresh))


def test_zsh_hook_autoclaim_gate_is_interactive_only_and_not_an_alternation():
    """The prefilter runs python for a first prompt only under CC_SESSION (set by
    claude-tracked), never for headless `claude -p`, and is not written as a
    third /(d|claim|…) alternation (test_hook_prefilter_lists_exactly_the_skill_claims)."""
    src = HOOK.read_text()
    assert 'CC_SESSION' in src and 'agent_claims.py" autoclaim' in src
    assert '"$D/by-session/$sid.auto"' in src
    assert len(_hook_alternations()) == 2


# --- reverse /fin (2026-11-14) ---------------------------------------------
class _Run:
    """Records tmux commands; has-session succeeds for the one named session."""
    def __init__(self, alive):
        self.alive, self.calls = alive, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc = 0 if cmd[1] != "has-session" or cmd[3] == f"={self.alive}" else 1
        return subprocess.CompletedProcess(cmd, rc, "", "")


def _claimed(tmp_path, sid, tmux="cc-x"):
    rec = ac.claim({"id": "T1", "content": "fix it"}, sid, tmp_path)
    rec["tmux"] = tmux
    (tmp_path / "T1.json").write_text(json.dumps(rec))
    return rec


def test_fin_idle_session_is_typed_into(tmp_path, monkeypatch):
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    _claimed(tmp_path, "S1")
    ac.set_state("T1", "idle", root=tmp_path)
    run = _Run("cc-x")
    assert ac.fin_holders("T1", tmp_path, run=run, self_session="") == ["typed:S1"]
    sent = [c for c in run.calls if c[1] == "send-keys"]
    assert sent[0][-2:] == ["-l", "/clear"] and sent[1][-1] == "Enter"
    assert ac.held("S1", tmp_path) == []            # no longer attached
    assert not (tmp_path / "by-session" / "S1.fin").exists()


def test_fin_working_session_gets_marker_not_keys(tmp_path):
    _claimed(tmp_path, "S1")                         # claim() leaves state working
    run = _Run("cc-x")
    assert ac.fin_holders("T1", tmp_path, run=run, self_session="") == ["marked:S1"]
    assert not run.calls
    assert (tmp_path / "by-session" / "S1.fin").read_text().strip() == "T1"
    assert ac.held("S1", tmp_path) == ["T1"]         # the hook validates against this


def test_fin_idle_without_live_tmux_falls_back_to_marker(tmp_path):
    _claimed(tmp_path, "S1", tmux="gone")
    ac.set_state("T1", "idle", root=tmp_path)
    assert ac.fin_holders("T1", tmp_path, run=_Run("cc-x"), self_session="") == ["marked:S1"]


def test_fin_skips_self_done_and_released(tmp_path):
    _claimed(tmp_path, "S1")
    ac.set_state("T1", "idle", root=tmp_path)
    run = _Run("cc-x")
    assert ac.fin_holders("T1", tmp_path, run=run, self_session="S1") == []   # /fin from inside
    ac.mark_done("S1", ["T1"], tmp_path)
    assert ac.fin_holders("T1", tmp_path, run=run, self_session="") == []
    _claimed(tmp_path, "S1")
    ac.release("S1", tmp_path)
    assert ac.fin_holders("T1", tmp_path, run=run, self_session="") == []
    assert not run.calls


def test_fin_ix_record_from_other_host_goes_over_ssh(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_host", lambda: "straylight")
    (tmp_path / "remote" / "ix").mkdir(parents=True)
    (tmp_path / "remote" / "ix" / "T1.json").write_text(json.dumps(
        {"task_id": "T1", "session": "S9", "host": "ix", "tmux": "cc-9"}))
    spawned = []
    def spawn(cmd, **kw):
        spawned.append(cmd)
    assert ac.fin_holders("T1", tmp_path, run=_Run("cc-9"), self_session="", spawn=spawn) == ["ssh:ix"]
    assert spawned[0][:1] == ["ssh"] and spawned[0][-1] == ac.FIN_SSH_CMD.format(tid="T1")


def test_fin_ix_marks_unreachable_host_in_root_fin_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_host", lambda: "ix")
    (tmp_path / "remote" / "straylight").mkdir(parents=True)
    (tmp_path / "remote" / "straylight" / "T1.json").write_text(json.dumps(
        {"task_id": "T1", "session": "S7", "host": "straylight"}))
    assert ac.fin_holders("T1", tmp_path, run=_Run(""), self_session="") == ["marked-remote:S7"]
    assert (tmp_path / "fin" / "S7").read_text().strip() == "T1"


def test_fin_typed_marks_session_free_and_a_new_claim_clears_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    monkeypatch.setattr(ac, "_retitle", lambda sid, target=None: None)
    _claimed(tmp_path, "S1")
    ac.set_state("T1", "idle", root=tmp_path)
    ac.fin_holders("T1", tmp_path, run=_Run("cc-x"), self_session="")
    assert (tmp_path / "by-session" / "S1.free").exists()
    ac.claim({"id": "T2", "content": "next"}, "S1", tmp_path)
    assert not (tmp_path / "by-session" / "S1.free").exists()


# --- timer links (2026-11-14) -----------------------------------------------
def test_link_timer_is_the_join_row_and_self_cleaning(tmp_path):
    assert ac.link_timer("6ghxxj7hhCWQgvqc", 111, {"date": "2026-11-14T08:00:00"}, "0l", tmp_path)
    assert ac.link_timer("6ghxxjGgqpmq6MPc", 222, "2026-11-14", "0t", tmp_path)
    assert ac.timer_link("6ghxxj7hhCWQgvqc", tmp_path) is None      # one timer runs at a time
    assert ac.timer_link("6ghxxjGgqpmq6MPc", tmp_path)["due"] == "2026-11-14"
    assert ac.task_for_entry(222, tmp_path) == "6ghxxjGgqpmq6MPc" and ac.task_for_entry(111, tmp_path) is None
    assert not ac.link_timer("0l", 333, root=tmp_path)             # text echoed back by dtd_resolve, not an id
    assert not ac.link_timer("", 333, root=tmp_path) and not (tmp_path / ".timer").exists()
    ac.unlink_timer(entry_id=222, root=tmp_path)
    assert ac.task_for_entry(222, tmp_path) is None


def test_load_merges_timer_and_prunes_a_rolled_recurring_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_retitle", lambda *a, **k: None)   # the prune retitles the holder's tab
    ac.claim({"id": "RRRRRRRRRR01", "content": "0l (8) [20]", "due": "2026-11-14"}, "S1", tmp_path)
    ac.claim({"id": "OOOOOOOOOO01", "content": "one-off", "due": {"date": "2026-11-14"}}, "S2", tmp_path)
    ac.link_timer("RRRRRRRRRR01", 500, "2026-11-14", "0l", tmp_path)
    q = {"today": [{"id": "RRRRRRRRRR01", "content": "0l (8) [20]", "due": "2026-11-15", "recurring": True},
                   {"id": "OOOOOOOOOO01", "content": "one-off", "due": "2026-11-20", "recurring": False}]}
    before = ac.load(tmp_path, queue={})
    assert before["RRRRRRRRRR01"]["toggl_entry_id"] == 500 and before["OOOOOOOOOO01"]["toggl_entry_id"] is None
    after = ac.load(tmp_path, queue=q)
    assert "RRRRRRRRRR01" not in after                      # the card rolled to its next occurrence
    assert "OOOOOOOOOO01" in after                          # a rescheduled one-off keeps its thread
    assert not (tmp_path / "RRRRRRRRRR01.timer").exists()   # _forget takes the sidecar too


def test_pruning_a_rolled_claim_frees_the_session_that_held_it(tmp_path, monkeypatch):
    """Bug 2026-11-15: the 1s survey closed the card through run.py (no
    fin_holders), the recurring card rolled, and load() forgot the claim but
    left by-session/<sid> pointing at it with no .free marker, so the tab read
    ❔ instead of ~~."""
    retitled = []
    monkeypatch.setattr(ac, "_retitle", lambda sid, target=None: retitled.append((sid, target)))
    monkeypatch.setattr(ac, "_tmux_session_name", lambda: "cc-cr")
    ac.claim({"id": "RRRRRRRRRR02", "content": "1s (20) [45]", "due": "2026-11-15"}, "S1", tmp_path)
    q = {"today": [{"id": "RRRRRRRRRR02", "content": "1s (20) [45]", "due": "2026-11-22", "recurring": True}]}
    assert "RRRRRRRRRR02" not in ac.load(tmp_path, queue=q)
    assert ac.held("S1", tmp_path) == []                              # no dangling index
    assert (tmp_path / "by-session" / "S1.free").exists()             # tab reads ~~
    assert retitled == [("S1", "cc-cr")]


def test_pruning_a_rolled_claim_leaves_a_session_that_moved_on(tmp_path, monkeypatch):
    """A released claim's session has ended or holds something else: pruning it
    must not free that session's tab, and a claim with no tmux name must not
    retitle (the fallback would paint the caller's own tab, i.e. dtd's)."""
    retitled = []
    monkeypatch.setattr(ac, "_retitle", lambda sid, target=None: retitled.append((sid, target)))
    monkeypatch.setattr(ac, "_tmux_session_name", lambda: "")
    ac.claim({"id": "RRRRRRRRRR03", "content": "0l (8) [20]", "due": "2026-11-14"}, "S1", tmp_path)
    ac.claim({"id": "TTTTTTTTTT01", "content": "other work"}, "S1", tmp_path)   # releases the first
    ac.claim({"id": "RRRRRRRRRR04", "content": "0t (3) [10]", "due": "2026-11-14"}, "S2", tmp_path)
    q = {"today": [{"id": "RRRRRRRRRR03", "content": "0l (8) [20]", "due": "2026-11-15", "recurring": True},
                   {"id": "RRRRRRRRRR04", "content": "0t (3) [10]", "due": "2026-11-15", "recurring": True}]}
    ac.load(tmp_path, queue=q)
    assert ac.held("S1", tmp_path) == ["TTTTTTTTTT01"]
    assert not (tmp_path / "by-session" / "S1.free").exists()
    assert ac.held("S2", tmp_path) == [] and (tmp_path / "by-session" / "S2.free").exists()
    assert retitled == []                                             # S2's claim carries no tmux name


# --- dtd codes (2026-11-15) ---------------------------------------------------
class _NoMatchDid:
    ANNOT_RE = __import__("re").compile(r"\(\(?\d+\)?\)|\[\d*G?\]|\{\d+\}")

    def match_todoist_task(self, query, tasks):
        return None


CODE_TASKS = [{"id": "6gA3", "content": "ibx i9 (4) [20]"},
              {"id": "6gA4", "content": "ibx m5x2 (4) [15]"},
              {"id": "6gA5", "content": "0t (3) [10]"},
              {"id": "6gA6", "content": "wiki index (5) [5]"},
              {"id": "6hB1", "content": "fix claiming bug [2]"}]


def test_a_dtd_code_claims_the_row_that_shows_it():
    """`/d ii` is the ibx i9 card (its code), not `wiki index`, the shortest
    name that happens to contain "ii"."""
    df = _NoMatchDid()
    assert [t["id"] for t in ac.candidates("ii", CODE_TASKS, df)] == ["6gA3"]
    assert [t["id"] for t in ac.candidates("FC", CODE_TASKS, df)] == ["6hB1"]
    assert [t["id"] for t in ac.candidates("0t", CODE_TASKS, df)] == ["6gA5"]   # a 2-char name is its own code


def test_a_query_that_is_no_code_still_matches_by_name():
    df = _NoMatchDid()
    assert [t["id"] for t in ac.candidates("zz", CODE_TASKS, df)] == []          # a free code names nothing
    assert [t["id"] for t in ac.candidates("claiming", CODE_TASKS, df)] == ["6hB1"]
    assert [t["id"] for t in ac.candidates("ibx m5x2", CODE_TASKS, df)] == ["6gA4"]


def test_a_code_two_dtds_drew_on_different_rows_is_ambiguous_not_guessed(tmp_path, monkeypatch):
    """Straylight's dtd and Ix's can hold different maps; `/d qq` must list
    both rows instead of claiming whichever the session's host computed."""
    monkeypatch.setenv("DTD_CODES_DIR", str(tmp_path))
    now = __import__("time").time()
    (tmp_path / "dtd-codes-straylight.json").write_text(json.dumps({"ts": now, "codes": {"6hB1": "qq"}}))
    (tmp_path / "dtd-codes-ix.json").write_text(json.dumps({"ts": now, "codes": {"6gA6": "qq"}}))
    got = ac.candidates("qq", CODE_TASKS, _NoMatchDid())
    assert sorted(t["id"] for t in got) == ["6gA6", "6hB1"]


def test_hook_never_creates_a_task_named_after_an_unknown_code():
    src = (Path(ac.__file__)).read_text()
    i = src.index("explicit_new = query.lower().startswith(\"new \")")
    block = src[i:i + 2600]
    assert block.index("_dtd_codes().is_code(query.strip().lower())") < block.index("task = new_task(name)")

