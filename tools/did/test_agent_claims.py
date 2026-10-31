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
    real_done = ac.mark_done
    monkeypatch.setattr(ac, "mark_done",
                        lambda s, ids=None, root=None, now=None: real_done(s, ids, root=tmp_path, now=now))
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
    assert _hook_alternations() == ["d|claim|" + "|".join(ac.SKILL_CLAIMS)] * 2


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
    assert _hook_alternations() == ["d|claim|" + "|".join(ac.SKILL_CLAIMS)] * 2


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
    env = dict(os.environ, HOME=str(home))
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


def test_release_drops_the_agents_file(tmp_path):
    root = tmp_path / "vault/z_ibx/agent-claims"
    ac.claim(TASK, "S1", root=root, now=1000)
    _hook_json(tmp_path, "subagent-start", {"session_id": "S1", "agent_id": "a1"})
    _hook(tmp_path, "release", "S1")
    assert not (root / "by-session/S1.agents").exists()


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


def test_hook_d_done_marks_done_not_released(hooked):
    """v169 review: `/d done` must mark the held task done (checkmark), not release it."""
    _fast("/d source logging")
    out = _fast("/d done")
    assert "agent-finished" in out and "released" not in out
    assert ac.load(hooked)["T1"]["state"] == "done"
