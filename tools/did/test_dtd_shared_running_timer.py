"""Bug 2026-10-19: "dtd is still showing 'teams' while Janus CLI has it right."

Each dtd pane kept its own /tmp timer file and believed it, so a pane that
had not itself started the current task showed a stale running row forever.
The list now also reads lib/running_timer's shared observation of Toggl --
the same helper janus resolves timer names with -- and a reading taken after
this pane's own start wins, including one that says nothing is running.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from test_dtd_running_highlight_by_id import _listgen_payload

MONO = Path(__file__).resolve().parents[2]
SPIN = "·✢✳✶✻✽"


def _task(tid, name):
    return {"id": tid, "content": f"{name} (15) [10]", "labels": ["i9"],
            "priority": 1, "due": "2026-10-11", "recurring": False}


CACHE = {"updated": "2026-10-11T10:00:00",
         "today": [_task("TEAMS", "teams"), _task("GOAL", "-1g"), _task("N1", "plain one")]}


def _run(timer_id, timer_name, observation, started=()):
    """Render the list with this pane's timer file set to (timer_name,
    timer_id) and `observation` written to the shared Toggl cache."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        home = tmp / "home"
        (home / "vault/z_ibx/agent-claims").mkdir(parents=True)
        (home / "i446-monorepo").symlink_to(MONO)
        state = home / ".local/state"
        (state / "jm").mkdir(parents=True)
        (state / "jm/task-queue.json").write_text(json.dumps(CACHE))
        (state / "jm/dtd-started").write_text(
            "".join(f"{day}\t{tid}\t{ts}\n" for day, tid, ts in started))
        if observation is not None:
            (home / ".cache").mkdir(parents=True)
            (home / ".cache/toggl-current.json").write_text(json.dumps(observation))

        def w(name, text):
            (tmp / name).write_text(text)
            return str(tmp / name)
        started_at = int(time.time()) - 300
        args = [w("cache.json", json.dumps(CACHE)),
                w("done.json", json.dumps({"date": "2026-10-11", "names": [], "ids": {}})),
                w("removed", ""), "2026-10-11", "120", w("skipped", ""),
                w("timer", f"{timer_name}\t{started_at}\t{timer_id}"), w("view", "")]
        (tmp / "removed.ids").write_text("")
        (tmp / "lg.py").write_text(_listgen_payload())
        env = {**os.environ, "HOME": str(home), "XDG_STATE_HOME": str(state)}
        r = subprocess.run([sys.executable, str(tmp / "lg.py"), *args],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return re.sub(r"^\x1b\[2m *\d+\x1b\[0m ", "", r.stdout, flags=re.M)  # drop the row number (2026-10-24)


def _row(out, tid):
    for line in out.splitlines():
        if line.rsplit("\t", 1)[-1] == tid:
            return re.sub(r"\x1b\[[0-9;]*m", "", line.split("\t")[0])
    raise AssertionError(f"{tid} not in list")


def _running(out):
    """The id of the row rendered as the live timer (leads with a spinner)."""
    for line in out.splitlines():
        body = re.sub(r"\x1b\[[0-9;]*m", "", line.split("\t")[0]).lstrip()
        if body[:1] in SPIN and "😈" not in body:
            return line.rsplit("\t", 1)[-1]
    return None


def _obs(desc, ago=5):
    now = time.time()
    entry = None if desc is None else {
        "description": desc, "id": 1,
        "start": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - 120))}
    return {"ts": now - ago, "entry": entry}


def test_stale_pane_follows_the_shared_reading():
    # This pane started 'teams' (dtd's start writes the timer file AND the
    # started log); Toggl has since moved to '-1g'.
    out = _run("TEAMS", "teams", _obs("-1g"),
               started=[("2026-10-11", "TEAMS", int(time.time()) - 300)])
    assert _running(out) == "GOAL", out
    # 2026-10-30: only the live timer keeps 🧮; the abandoned task loses it.
    assert "🧮" not in _row(out, "TEAMS") and "🧮" in _row(out, "GOAL"), out


def test_shared_reading_of_idle_clears_a_stale_running_row():
    out = _run("TEAMS", "teams", _obs(None))
    assert _running(out) is None, out


def test_own_start_wins_over_an_older_reading():
    # The reading predates this pane's start (cache invalidated on mutation).
    out = _run("TEAMS", "teams", _obs("-1g", ago=600))
    assert _running(out) == "TEAMS", out


def test_no_cache_leaves_behaviour_unchanged():
    assert _running(_run("TEAMS", "teams", None)) == "TEAMS"


def test_reading_that_names_no_task_marks_nothing_running():
    out = _run("TEAMS", "teams", _obs("B2"))
    assert _running(out) is None, out
