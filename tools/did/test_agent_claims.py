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
