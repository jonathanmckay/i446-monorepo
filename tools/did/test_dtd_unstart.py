#!/usr/bin/env python3
"""dtd ⌥↓ (alt-down) sends a started-today task back to its regular spot
(2026-11-15: "sometimes I get started on a task accidentally or I want to move
it back to regular spot in the list").

Covers: the binding and key hint, the list generator honouring a tombstone in
dtd-started (and a later start re-pinning the task), and the generated unstart
script (tombstones only ids with a live start today, skips picker rows).
"""
import importlib.util
import json
import os
import re
import sys
import subprocess
import tempfile
import time
from pathlib import Path

from test_dtd_inprogress_tiers import _ids, _run

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
LINES = DTD.split("\n")
DAY = "2026-10-11"
REL, IDLE, WORK, DONE, FAR = ("relrelrelrel0001", "idleidleidle0002", "workworkwork0003",
                              "donedonedone0004", "farfarfarfar0005")


def _ac():
    spec = importlib.util.spec_from_file_location("agent_claims_unpin_t", HERE / "agent_claims.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    m.push = lambda: None          # never rsync from a test
    m._retitle = lambda *a, **k: None
    return m


def _claim(d: Path, tid: str, state: str, ts=None, timer=False):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.json").write_text(json.dumps({"task_id": tid, "session": "s-" + tid, "host": "h"}))
    (d / f"{tid}.state").write_text(f"{state} {int(ts or time.time())}\n")
    if timer:
        (d / f"{tid}.timer").write_text("{}")


class _Ssh:
    """Stands in for subprocess.run: records the call, answers like Ix."""
    def __init__(self, stdout="", rc=0):
        self.calls, self.stdout, self.rc = [], stdout, rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, self.stdout, "")


def test_alt_down_binding_hint_warm_and_cleanup():
    m = re.search(r'--bind "alt-down:[^"]*"', DTD)
    assert m and "execute-silent($DTD_UNSTART {+2})" in m.group(0) and "reload-sync($DTD_RELOAD)" in m.group(0)
    assert "⌥↓: back in line" in DTD
    assert '"$DTD_BUMP" "$DTD_UNSTART" "$DTD_SKIP"' in DTD          # warm list
    assert '"$DTD_UNSTART" "$DTD_BUMP" "$DTD_UNDO"' in DTD          # exit cleanup


def test_tombstone_drops_task_from_started_tier():
    now = int(time.time())
    ids = _ids(_run("R1", started=[(DAY, "N1", now - 900), (DAY, "N2", now - 600),
                                   (DAY, "R1", now - 300), (DAY, "N2", f"x\t{now - 100}")]))
    assert ids[:2] == ["R1", "N1"], ids       # N2 no longer pinned under the running row
    assert ids.index("N2") > 3, ids           # back among the plain rows, below the claims


def test_start_after_tombstone_pins_again():
    now = int(time.time())
    ids = _ids(_run("R1", started=[(DAY, "N2", now - 600), (DAY, "N2", f"x\t{now - 100}"),
                                   (DAY, "N2", now - 50)]))
    assert ids[:2] == ["R1", "N2"], ids


def test_unstart_script_tombstones_only_live_starts():
    s = next(i for i, l in enumerate(LINES) if l.startswith("DTD_UNSTART="))
    e = next(i for i in range(s, len(LINES)) if LINES[i].strip() == "UNSTARTEOF")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "jm").mkdir()
        log = tmp / "jm/dtd-started"
        today = time.strftime("%Y-%m-%d")
        log.write_text(f"{today}\tA\t100\n{today}\tB\t200\n{today}\tB\tx\t250\n2020-01-01\tC\t300\n")
        gen = tmp / "g.zsh"
        gen.write_text(f'#!/bin/zsh\nDTD_ID="tunstartt"; DTD_HDR="{tmp}/hdr"; DTD_EVENTS="{tmp}/events"\n'
                       + "\n".join(LINES[s:e + 2]) + "\n")
        subprocess.run(["zsh", str(gen)], check=True)
        script = Path("/tmp/dtd-tunstartt.unstart.sh")
        env = {**os.environ, "XDG_STATE_HOME": str(tmp), "AGENT_CLAIMS_DIR": str(tmp / "claims")}
        env.pop("DTD_WARM", None)
        try:
            before = log.read_text()
            # A is live; B already tombstoned; C started another day; Z never; picker row.
            r = subprocess.run(["zsh", str(script), "A", "B", "C", "Z", "BLOCK:戌"],
                               capture_output=True, text=True, env=env)
            assert r.returncode == 0, r.stderr
            added = log.read_text()[len(before):].splitlines()
            assert len(added) == 1 and added[0].split("\t")[:3] == [today, "A", "x"], added
            assert "back in line: 1 task" in (tmp / "hdr").read_text()
            assert "\tunstart\tA,B,C,Z,BLOCK:戌" in (tmp / "events").read_text()

            r = subprocess.run(["zsh", str(script), "A"], capture_output=True, text=True, env=env)
            assert r.returncode == 0 and log.read_text().count("\tx\t") == 2   # nothing appended
            assert "nothing pins this row" in (tmp / "hdr").read_text()

            # A released claim pins a row with no start at all (the rows JM
            # first pressed ⌥↓ on): the script drops it and counts the row.
            claims = tmp / "claims"
            claims.mkdir()
            (claims / f"{REL}.json").write_text(json.dumps({"task_id": REL, "session": "s", "host": "ix"}))
            (claims / f"{REL}.state").write_text("released 1\n")
            r = subprocess.run(["zsh", str(script), REL], capture_output=True, text=True,
                               env={**env, "PATH": f"{tmp}/bin:{env['PATH']}"})
            assert r.returncode == 0 and not (claims / f"{REL}.json").exists()
            assert "back in line: 1 task" in (tmp / "hdr").read_text()
        finally:
            script.unlink(missing_ok=True)


def test_unpin_drops_released_done_idle_keeps_working_and_timer(tmp_path):
    ac = _ac()
    for tid, st in ((REL, "released"), (DONE, "done"), (IDLE, "idle"), (WORK, "working")):
        _claim(tmp_path, tid, st, timer=(tid == REL))
    ssh = _Ssh()
    out = ac.unpin([REL, DONE, IDLE, WORK, "B2"], root=tmp_path, run=ssh, host="ix")
    assert sorted(out["dropped"]) == sorted([REL, DONE, IDLE]) and out["live"] == [WORK], out
    assert not ssh.calls                                     # nothing mirrored from Ix
    assert not (tmp_path / f"{REL}.json").exists() and not (tmp_path / f"{REL}.state").exists()
    assert (tmp_path / f"{REL}.timer").exists()              # Toggl link survives
    assert (tmp_path / f"{WORK}.json").exists()


def test_unpin_stale_working_counts_as_idle(tmp_path):
    ac = _ac()
    _claim(tmp_path, WORK, "working", ts=time.time() - ac.STALE_SECS - 5)
    assert ac.unpin([WORK], root=tmp_path, run=_Ssh(), host="ix")["dropped"] == [WORK]


def test_unpin_ix_mirror_dropped_only_after_ix_confirms(tmp_path):
    ac = _ac()
    mirror = tmp_path / "remote" / "ix"
    _claim(mirror, REL, "released")
    _claim(mirror, IDLE, "idle")

    down = _Ssh(rc=255)                                      # Ix unreachable: keep, say so
    out = ac.unpin([REL], root=tmp_path, run=down, host="straylight-refit")
    assert out["failed"] == [REL] and not out["dropped"] and (mirror / f"{REL}.json").exists()

    up = _Ssh(stdout=json.dumps({"dropped": [REL], "live": [IDLE], "failed": [], "elsewhere": []}))
    out = ac.unpin([REL, IDLE], root=tmp_path, run=up, host="straylight-refit")
    assert up.calls and up.calls[0][-1].endswith(f"unpin {REL} {IDLE}") and "ix" in up.calls[0]
    assert out["dropped"] == [REL] and out["live"] == [IDLE], out   # Ix says an agent just picked IDLE up
    assert not (mirror / f"{REL}.json").exists() and (mirror / f"{IDLE}.json").exists()


def test_unpin_on_ix_leaves_another_hosts_copy(tmp_path):
    ac = _ac()
    _claim(tmp_path / "remote" / "straylight-refit", FAR, "released")
    out = ac.unpin([FAR], root=tmp_path, run=_Ssh(), host="ix")
    assert out["elsewhere"] == [FAR] and not out["dropped"]
    assert (tmp_path / "remote" / "straylight-refit" / f"{FAR}.json").exists()
