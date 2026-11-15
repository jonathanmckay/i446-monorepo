#!/usr/bin/env python3
"""dtd ⌥↓ (alt-down) sends a started-today task back to its regular spot
(2026-11-15: "sometimes I get started on a task accidentally or I want to move
it back to regular spot in the list").

Covers: the binding and key hint, the list generator honouring a tombstone in
dtd-started (and a later start re-pinning the task), and the generated unstart
script (tombstones only ids with a live start today, skips picker rows).
"""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from test_dtd_inprogress_tiers import _ids, _run

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()
LINES = DTD.split("\n")
DAY = "2026-10-11"


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
        env = {**os.environ, "XDG_STATE_HOME": str(tmp)}
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
            assert "not started today" in (tmp / "hdr").read_text()
        finally:
            script.unlink(missing_ok=True)
