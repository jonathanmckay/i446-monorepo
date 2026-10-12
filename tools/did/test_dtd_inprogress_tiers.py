#!/usr/bin/env python3
"""Regression: dtd's in-progress tiers (2026-10-11).

"I want tasks that are in progress to be above the line in dtd": JM's own
running timer first (🧮, domain color), then agent claims waiting on JM
(🧮 🕐, domain color), then agent claims mid-turn (spinner + 😈, grey),
then everything else. A task JM is timing stays tier A even if an agent
also holds it.

Runs the real list-gen payload with HOME pointed at a temp dir whose
agent-claims store holds one idle and one working claim.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from test_dtd_running_highlight_by_id import _listgen_payload, _row_for_id

MONO = Path(__file__).resolve().parents[2]
GREY = "\x1b[38;2;110;110;110m"


def _task(tid, name):
    return {"id": tid, "content": f"{name} (15) [10]", "labels": ["i9"],
            "priority": 1, "due": "2026-10-11", "recurring": False}


CACHE = {"updated": "2026-10-11T10:00:00",
         "today": [_task("N1", "plain one"), _task("W1", "agent working"),
                   _task("I1", "agent waiting"), _task("N2", "plain two"),
                   _task("R1", "my timer"), _task("RC", "timed and claimed")]}


def _claim(root: Path, tid: str, state: str):
    (root / f"{tid}.json").write_text(json.dumps({"task_id": tid, "session": "s-" + tid}))
    (root / f"{tid}.state").write_text(f"{state} {int(time.time())}\n")


def _run(timer_id: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        home = tmp / "home"
        claims = home / "vault/z_ibx/agent-claims"
        claims.mkdir(parents=True)
        (home / "i446-monorepo").symlink_to(MONO)
        _claim(claims, "W1", "working")
        _claim(claims, "I1", "idle")
        _claim(claims, "RC", "working")

        def w(name, text):
            (tmp / name).write_text(text)
            return str(tmp / name)
        args = [w("cache.json", json.dumps(CACHE)),
                w("done.json", json.dumps({"date": "2026-10-11", "names": [], "ids": {}})),
                w("removed", ""), "2026-10-11", "120", w("skipped", ""),
                w("timer", f"x\t{int(time.time()) - 300}\t{timer_id}"), w("view", "")]
        (tmp / "removed.ids").write_text("")
        (tmp / "lg.py").write_text(_listgen_payload())
        env = {**os.environ, "HOME": str(home)}
        r = subprocess.run([sys.executable, str(tmp / "lg.py"), *args],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout


def _ids(out: str) -> list[str]:
    return [l.rsplit("\t", 1)[-1] for l in out.splitlines() if "\t" in l]


def test_tiers_order_running_then_waiting_then_working():
    # Active rows sit at the BOTTOM: the footer shows them above the line
    # (2026-10-11), the list only keeps them searchable/actionable.
    out = _run("R1")
    ids = _ids(out)
    assert set(ids[:2]) == {"N1", "N2"}, ids
    # Printed in reverse footer order, so the running task is the LAST row.
    assert ids[2:][::-1] in (["R1", "I1", "W1", "RC"], ["R1", "I1", "RC", "W1"]), ids


def test_up_from_top_task_wraps_to_running_task():
    """2026-10-12: "press up from the top task takes me to the current task".
    fzf --cycle wraps ↑ on row 1 to the last row, which must be the timer."""
    assert _ids(_run("R1"))[-1] == "R1"
    dtd = Path(__file__).with_name("dtd.sh").read_text()
    assert "--layout=reverse-list --no-sort --ansi --cycle" in dtd


def test_tier_markers():
    out = _run("R1")
    a, b, c = (_row_for_id(out, i) for i in ("R1", "I1", "W1"))
    assert "🧮 5m · " in a and "😈" not in a and "🕐" not in a and GREY not in a
    assert "🧮 🕐 " in b and "😈" not in b and GREY not in b
    assert "😈" in c and "🧮" not in c and c.startswith(GREY)
    for i in ("N1", "N2"):
        row = _row_for_id(out, i)
        assert not any(m in row for m in ("🧮", "🕐", "😈"))


def test_running_beats_agent_claim():
    out = _run("RC")
    ids = _ids(out)
    assert ids.index("RC") > ids.index("I1") > ids.index("W1"), ids
    row = _row_for_id(out, "RC")
    assert "🧮" in row and "😈" not in row and GREY not in row


def test_resolver_strips_tier_prefixes():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    for vis in ("🧮 5m · plain one", "🧮 🕐 plain one", "🧮 🕐 ↻ plain one"):
        r = subprocess.run([sys.executable, str(Path(__file__).with_name("dtd_resolve.py")),
                            "/nonexistent", vis], capture_output=True, text=True)
        assert r.stdout.strip().endswith("plain one") and "🧮" not in r.stdout, (vis, r.stdout)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
