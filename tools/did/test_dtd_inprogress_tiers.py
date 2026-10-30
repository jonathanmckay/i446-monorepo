#!/usr/bin/env python3
"""Regression: dtd's in-progress tiers (2026-10-11).

2026-10-13 order, top down: JM's running timer (spinner Nm since 2026-10-16,
was 🧮 Nm), tasks JM started today (Enter/ctrl-s) that aren't done (no glyph
since 2026-10-30, most recent start first), agent
claims mid-turn (spinner + 😈, grey), agent claims waiting on JM (🕐, no 🧮 since 2026-10-28),
then everything else. A task JM is timing stays on top even if an agent
also holds it; a WORKING claim on it swaps the abacus for spinner + 😈
(2026-10-16), an idle one leaves his timer's 🧮 Nm.

Runs the real list-gen payload with HOME pointed at a temp dir whose
agent-claims store holds one idle and one working claim.
"""
import json
import re
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from test_dtd_running_highlight_by_id import _listgen_payload, _row_for_id

MONO = Path(__file__).resolve().parents[2]
GREY = "\x1b[38;2;110;110;110m"
SPIN = "·✢✳✶✻✽✻✶✳✢"   # Claude Code's spinner glyphs (dtd.sh _AGENT_SPIN)


def _lead_glyph(row: str) -> str:
    """First visible character of a row, ANSI stripped."""
    import re
    return re.sub(r"\x1b\[[0-9;]*m", "", row).lstrip()[:1]


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


def _run(timer_id: str, started=(), skipped="", rc_state="working") -> str:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        home = tmp / "home"
        claims = home / "vault/z_ibx/agent-claims"
        claims.mkdir(parents=True)
        (home / "i446-monorepo").symlink_to(MONO)
        _claim(claims, "W1", "working")
        _claim(claims, "I1", "idle")
        _claim(claims, "RC", rc_state)
        state = home / ".local/state"
        (state / "jm").mkdir(parents=True)
        (state / "jm/dtd-started").write_text("".join(
            f"{day}\t{tid}\t{ts}\n" for day, tid, ts in started))

        def w(name, text):
            (tmp / name).write_text(text)
            return str(tmp / name)
        args = [w("cache.json", json.dumps(CACHE)),
                w("done.json", json.dumps({"date": "2026-10-11", "names": [], "ids": {}})),
                w("removed", ""), "2026-10-11", "120", w("skipped", skipped),
                w("timer", f"x\t{int(time.time()) - 300}\t{timer_id}"), w("view", "")]
        (tmp / "removed.ids").write_text("")
        (tmp / "lg.py").write_text(_listgen_payload())
        env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(state)}
        r = subprocess.run([sys.executable, str(tmp / "lg.py"), *args],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return re.sub(r"^\x1b\[2m *\d+\x1b\[0m ", "", r.stdout, flags=re.M)  # drop the row number (2026-10-24)


def _ids(out: str) -> list[str]:
    return [l.rsplit("\t", 1)[-1] for l in out.splitlines() if "\t" in l]


def test_tiers_order_running_then_claims():
    # 2026-10-13: JM's running timer is the TOP row, then agent claims
    # (working above waiting), then the rest.
    ids = _ids(_run("R1"))
    assert ids[0] == "R1", ids
    assert set(ids[1:3]) == {"W1", "RC"} and ids[3] == "I1", ids
    assert set(ids[4:6]) == {"N1", "N2"}, ids


def test_started_not_done_stays_on_top_after_timer_moves():
    """2026-10-13: "when I hit enter on a task ... put the abacus emoji by it
    and it should be at the top. When the time entry shifts, if the task is
    still not done, leave it in dtd, at the top." """
    now = int(time.time())
    out = _run("R1", started=[("2026-10-11", "N1", now - 900), ("2026-10-11", "N2", now - 600),
                              ("2026-10-11", "R1", now - 300), ("2026-10-10", "W1", now)])
    ids = _ids(out)
    assert ids[:3] == ["R1", "N2", "N1"], ids          # running, then latest start first
    for i in ("N1", "N2"):
        row = _row_for_id(out, i)
        assert "🧮" not in row and "m · " not in row and "🕐" not in row, row
    assert "😈" in _row_for_id(out, "W1")                 # yesterday's start ignored; claim wins


def test_skip_beats_started():
    out = _run("R1", started=[("2026-10-11", "N1", int(time.time()))], skipped="plain one\n")
    ids = _ids(out)
    assert ids[-1] == "N1" and "🧮" not in _row_for_id(out, "N1"), ids


def test_ai_claimed_task_goes_to_top_not_bottom():
    """2026-10-13 bug: "when ai /d's a task, it should go to the top not the
    bottom of the list". A freshly /d'd task is a working claim. Only JM's
    own running timer sits above it, so check with no timer running."""
    ids = _ids(_run(""))
    assert ids[0] in ("W1", "RC"), ids
    assert ids.index("I1") < ids.index("N1"), ids


def test_scrolling_does_not_wrap():
    """2026-10-13: "change dtd scrolling so it doesn't wrap around". fzf
    --cycle (added 2026-10-12 for an up-from-top jump to the timer) is off."""
    dtd = Path(__file__).with_name("dtd.sh").read_text()
    fzf_line = next(l for l in dtd.splitlines() if "| fzf --prompt=" in l)
    assert "--cycle" not in fzf_line, fzf_line


def test_tier_markers():
    out = _run("R1")
    a, b, c = (_row_for_id(out, i) for i in ("R1", "I1", "W1"))
    assert _lead_glyph(a) in SPIN and " 5m · " in a, a
    # 2026-10-22: the running row keeps JM's 🧮 after the moving minutes
    assert " 5m · 🧮 " in a, a
    assert "😈" not in a and "🕐" not in a and GREY not in a
    assert "🕐 " in b and "🧮" not in b and "😈" not in b and GREY not in b, b
    assert "😈" in c and "🧮" not in c and c.startswith(GREY)
    for i in ("N1", "N2"):
        row = _row_for_id(out, i)
        assert not any(m in row for m in ("🧮", "🕐", "😈"))


def test_working_claim_replaces_abacus_on_timed_task():
    """2026-10-16 bug: "when agent started working on /0t it didn't replace
    the abacus with a demon icon". JM hits Enter on 0t (timer runs on it), the
    /0t session claims it and goes working: the row stays on top with its
    minutes, but 🧮 becomes spinner + 😈 and the row greys."""
    out = _run("RC")
    ids = _ids(out)
    assert ids[0] == "RC" and ids.index("W1") < ids.index("I1"), ids
    row = _row_for_id(out, "RC")
    assert "😈" in row and "5m · 🧮 " in row and row.startswith(GREY), row


def test_working_claim_on_started_task_has_no_abacus():
    """JM hit Enter on a task today, his timer moved on, and an agent now
    works on it: the row leads with the agent's spinner + 😈, no 🧮 (the
    2026-10-21 abacus here was a second one; since 2026-10-30 only the live
    timer's row has it). The running-timer case keeps its 🧮 after the minutes."""
    now = int(time.time())
    out = _run("R1", started=[("2026-10-11", "W1", now - 600)])
    row = _row_for_id(out, "W1")
    assert _lead_glyph(row) in SPIN and "😈 agent working" in row and "🧮" not in row and row.startswith(GREY), row
    rc = _row_for_id(_run("RC", started=[("2026-10-11", "RC", now - 600)]), "RC")
    assert "😈" in rc and "5m · 🧮 " in rc, rc


def test_idle_claim_loses_to_running_timer():
    """An idle claim on the task JM is timing still renders as his timer
    (spinner Nm), not 🧮 🕐: the agent is waiting on him, he is the one working."""
    out = _run("RC", rc_state="idle")
    row = _row_for_id(out, "RC")
    assert _lead_glyph(row) in SPIN and " 5m · " in row, row
    assert "5m · 🧮 " in row and "🕐" not in row and "😈" not in row and GREY not in row, row


def test_running_timer_moves_started_keeps_top_slot():
    """2026-10-16: "when my time is on a certain task, it should be a clock
    ticker or something that is like the claude snowflake that shows movement.
    If I started but didn't finish, it can just show the abacus." The timed
    row leads with a spinner frame (domain color, no 😈); a started-not-done
    row stays second, without 🧮 since 2026-10-30; the frame advances."""
    now = int(time.time())
    out = _run("R1", started=[("2026-10-11", "N1", now - 600)])
    r1, n1 = _row_for_id(out, "R1"), _row_for_id(out, "N1")
    assert _lead_glyph(r1) in SPIN and "5m · 🧮 " in r1 and "😈" not in r1, r1
    assert "🧮" not in n1, n1
    assert _ids(out)[:2] == ["R1", "N1"]
    # the glyph is a function of time, so two renders ~0.5s apart differ
    time.sleep(0.55)
    r1b = _row_for_id(_run("R1"), "R1")
    assert _lead_glyph(r1b) in SPIN and _lead_glyph(r1b) != _lead_glyph(r1), (r1, r1b)


def test_watcher_reloads_every_second_while_timer_runs():
    """The list only re-renders on a reload, so the moving glyph needs the
    auto-reload watcher to cycle while $DTD_TIMER is non-empty (1s), the way
    it already does at 0.5s while an agent claim is working."""
    dtd = Path(__file__).with_name("dtd.sh").read_text()
    loop = dtd[dtd.index("for _acw in 1 2 3 4; do"):]
    loop = loop[:loop.index("done")]
    assert '[[ -s "$DTD_TIMER" ]] && break' in loop, loop
    assert '[[ -s "$DTD_TIMER" ]] && ac_reload=1' in dtd


def test_resolver_strips_tier_prefixes():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    for vis in ("🧮 5m · plain one", "🧮 🕐 plain one", "🧮 🕐 ↻ plain one",
                "✻ 5m · plain one", "· 5m · plain one", "✽ 😈 5m · plain one", "✢ 😈 plain one",
                "✶ 7😈 🧮 plain one", "🧮 3🕐 plain one", "✢ 12😈 plain one",
                "🕐 plain one", "3🕐 plain one", "🕐 ↻ plain one"):
        r = subprocess.run([sys.executable, str(Path(__file__).with_name("dtd_resolve.py")),
                            "/nonexistent", vis], capture_output=True, text=True)
        assert r.stdout.strip().endswith("plain one") and "🧮" not in r.stdout, (vis, r.stdout)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_abacus_only_on_running_task_after_switch():
    """2026-10-30 bug: "the abacus emoji is not clearing out when I start
    another task ... abacus is only ever on one task at a time". Start N1,
    then N2, then switch the timer to R1: R1 alone carries 🧮; N1 and N2 stay
    on top (started, not done) without it."""
    now = int(time.time())
    out = _run("R1", started=[("2026-10-11", "N1", now - 900),
                              ("2026-10-11", "N2", now - 600),
                              ("2026-10-11", "R1", now - 300),
                              ("2026-10-11", "W1", now - 200)])
    abacus = [i for i in _ids(out) if "🧮" in _row_for_id(out, i)]
    assert abacus == ["R1"], abacus
    assert _ids(out)[:3] == ["R1", "N2", "N1"], _ids(out)
