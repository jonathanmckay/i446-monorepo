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
    assert ac.release("S1", tmp_path) == "T1"
    assert ac.load(tmp_path) == {}


def test_one_task_per_session(tmp_path):
    ac.claim(TASK, "S1", root=tmp_path, now=1000)
    ac.claim({"id": "T2", "content": "other"}, "S1", root=tmp_path, now=1001)
    assert set(ac.load(tmp_path, now=1002)) == {"T2"}


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


def _hook(home, state, sid):
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return subprocess.run([str(HOOK), state], input=json.dumps({"session_id": sid}),
                          text=True, env=env, capture_output=True, timeout=10)


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
    assert not (root / "T1.json").exists() and not (root / "by-session/S1").exists()


def test_hook_noop_without_claims_dir(tmp_path):
    assert _hook(tmp_path, "working", "S1").returncode == 0


def test_ticker_spinner_suffix():
    spec = importlib.util.spec_from_file_location("ticker", HERE / "dtd-ticker.py")
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    assert tk.agent_suffix([], 3) == ""
    one = tk.agent_suffix(["source logging (30) [20]"], 0)
    assert "😈 source logging 30 20" in one and "(" not in one   # parens break change-footer()
    assert tk.agent_suffix(["a", "b"], 1).endswith("😈 ×2")
    assert tk.agent_suffix(["a"], 0) != tk.agent_suffix(["a"], 1)  # it spins


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
    assert "😈" not in bravo
    # order unchanged: the claimed task stays in place
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
    assert "(d|claim|0t|1s897|notes)" in HOOK.read_text()


def test_1s897_claims_weekly_card_and_keeps_it(hooked, monkeypatch):
    """2026-10-07: /1s897 claims '1 s897' like /0t, but has a manual part, so
    nothing releases or completes it; JM closes the card in dtd."""
    monkeypatch.setattr(ac, "_did", lambda: _FakeDid([TASK, {"id": "W1", "content": "1 s897 (25) [30]"},
                                                      {"id": "W2", "content": "ibx s897 (15) [5]"},
                                                      {"id": "W3", "content": "s897 (120) [30]"}]))
    out = _fast("/1s897 7.4")
    assert "😈 claimed: 1 s897 (25) [30]" in out and "leave the card open" in out
    assert (hooked / "by-session" / "S9").read_text().strip() == "W1"


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
    assert "command-name>[[:space:]]*/(d|claim|0t|1s897|notes)" in HOOK.read_text()
