#!/usr/bin/env python3
"""edit-form.py — dtd's ctrl-o / ctrl-p curses form (2026-10-21, "I don't like
the new ctrl+o / ctrl+p page": points and time first, then haiku name, full
name, notes, project, then the 'done so far' breakoff block).

Pure checks on prefill / validate / result, plus a pty run of the real form:
it must paint fast (budget < 200 ms, aim 80–100), Esc / ^C cancel with rc 2
and no result file, and a save writes the JSON edit-fast consumes.
"""
import importlib.util
import json
import os
import pty
import re
import select
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("edit_form", HERE / "edit-form.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["edit_form"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ef():
    return _load()


LONG = "write the quarterly planning memo for the whole org (30) [20]"


def test_prefill_points_time_short_name_domain(ef):
    t = {"id": "t1", "content": LONG, "labels": ["i9", "1neon"], "short": "Quarterly memo (30) [20]"}
    p = ef.prefill(t, "notes")
    assert (p["points"], p["time"], p["domain"]) == ("20", "30", "i9")
    assert p["name"] == "write the quarterly planning memo for the whole org"
    assert p["short"] == "Quarterly memo" and p["short_enabled"]
    assert p["desc"] == "notes" and p["desc_loaded"]


def test_prefill_short_title_disables_haiku_and_curly_points_are_read_only(ef):
    p = ef.prefill({"id": "t2", "content": "call LR {15}", "labels": []}, None)
    assert not p["short_enabled"] and p["points"] == "{15}" and p["points_curly"]
    assert not p["desc_loaded"] and p["desc"] == ""


def test_remainder_values_are_independent_and_prefill_with_the_totals(ef):
    assert ef.remainder("20", "30", "15", "10") == ("5", "20")
    assert ef.remainder("20", "", "15", "") == ("5", "")
    assert ef.remainder("", "30", "15", "40") == ("", "0")
    assert ef.remainder("20", "30", "", "") == ("20", "30")     # nothing done yet = whole total
    assert ef.remainder("", "", "", "") == ("", "")


def test_validate_numbers_and_bounds(ef):
    base = {"points": "20", "time": "30", "points_curly": False}
    assert ef.validate({**base, "done_pts": "15", "done_min": "10"}) is None
    assert "number" in ef.validate({**base, "done_pts": "ten"})
    assert "> total" in ef.validate({**base, "done_pts": "25"})
    assert "> time" in ef.validate({**base, "done_pts": "5", "done_min": "45"})
    assert "needs pts done" in ef.validate({**base, "done_min": "5"})
    assert ef.validate({**base, "points": "{15}", "points_curly": True}) is None


def test_result_shapes_the_edit_fast_payload(ef):
    vals = {"name": " memo ", "points": "20", "time": "30", "short": "Q memo", "short_enabled": True,
            "domain": "@i9", "desc": "a\nb\n", "desc_loaded": True,
            "done_pts": "15", "done_min": "", "done_name": "drafted  it"}
    r = ef.result(vals)
    assert r["name"] == "memo" and r["domain"] == "i9" and r["desc"] == "a\nb"
    assert r["split"] == {"pts": 15, "minutes": None, "name": "drafted it", "rem_pts": None, "rem_min": None}
    vals.update(rem_pts="5", rem_min="12")
    assert ef.result(vals)["split"]["rem_pts"] == 5 and ef.result(vals)["split"]["rem_min"] == 12
    vals.update(short_enabled=False, desc_loaded=False, done_pts="", points_curly=True)
    r = ef.result(vals)
    assert r["short"] == "" and r["desc"] is None and r["split"] is None and r["points"] == ""


# ── pty: the real form ──────────────────────────────────────────────────────

def _env(tmp_path):
    state = tmp_path / "state"
    (state / "jm" / "task-detail").mkdir(parents=True)
    (state / "jm" / "task-detail" / "t1.json").write_text(json.dumps({
        "task": {"id": "t1", "content": LONG, "description": "old notes", "labels": ["i9"]},
        "comments": []}))
    cache = {"today": [{"id": "t1", "content": LONG, "labels": ["i9"], "short": "Quarterly memo (30) [20]"}]}
    (tmp_path / "cache.json").write_text(json.dumps(cache))
    return dict(os.environ, TERM="xterm-256color", XDG_STATE_HOME=str(state), LANG="en_US.UTF-8",
                LC_ALL="en_US.UTF-8", LINES="40", COLUMNS="100")


def _run(tmp_path, keys: str, focus="mother", env_extra=None):
    env = _env(tmp_path)
    env.update(env_extra or {})
    out, timing = tmp_path / "out.json", tmp_path / "timing.log"
    t0 = time.time()
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("python3", ["python3", str(HERE / "edit-form.py"), "--id", "t1", str(tmp_path / "cache.json"),
                               "--out", str(out), "--focus", focus, "--timing", str(timing)], env)
    buf = b""

    def pump(t):
        nonlocal buf
        end = time.time() + t
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.02)
            if r:
                try:
                    buf += os.read(fd, 65536)
                except OSError:
                    return
    pump(0.7)
    for k in keys:
        os.write(fd, k.encode())
        pump(0.04)
    pump(0.4)
    _, st = os.waitpid(pid, 0)
    stamps = {}
    for line in timing.read_text().splitlines():
        ts, stage, _arg = line.split("\t")
        stamps[stage] = float(ts)
    screen = re.sub(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\(B", b"", buf).decode("utf-8", "replace")
    return os.WEXITSTATUS(st), out, stamps, t0, screen


def test_pty_paints_fast_and_esc_cancels(tmp_path):
    rc, out, stamps, t0, screen = _run(tmp_path, "\x1b")
    assert rc == 2 and not out.exists()
    for label in ("[N] pts", "(N) min", "haiku", "name", "notes", "project", "done so far", "pts done", "min done",
                  "done as", "rem pts", "rem min"):
        assert label in screen, label
    assert screen.index("[N] pts") < screen.index("haiku") < screen.index("name ") < screen.index("notes") \
        < screen.index("project") < screen.index("done so far")
    paint = (stamps["edit-form-paint"] - t0) * 1000
    assert paint < 200, f"form took {paint:.0f} ms from exec to first paint"


def test_pty_ctrl_c_cancels_with_rc_2(tmp_path):
    rc, out, _, _, _ = _run(tmp_path, "\x03")
    assert rc == 2 and not out.exists()


def test_pty_ctrl_p_focus_split_remainder_autofills_and_enter_on_last_saves(tmp_path):
    # pts done 15 / min done 20 → rem pts 5 / rem min 10 fill themselves; Enter
    # walks through done as, rem pts, rem min (the last field) and saves
    rc, out, _, _, screen = _run(tmp_path, "15\t20\tdrafted it\r\r\r", focus="split")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["split"] == {"pts": 15, "minutes": 20, "name": "drafted it", "rem_pts": 5, "rem_min": 10}
    assert j["points"] == "20" and j["time"] == "30" and j["short"] == "Quarterly memo" and j["desc"] == "old notes"
    assert "rem pts" in screen and "rem min" in screen


def test_pty_short_pane_still_shows_the_remainder_row(tmp_path):
    env_rows = {"LINES": "18"}
    rc, out, _, _, screen = _run(tmp_path, "15\x13", focus="split", env_extra=env_rows)
    assert rc == 0
    assert "rem pts" in screen and "rem min" in screen and "done as" in screen
    j = json.loads(out.read_text())
    assert j["split"]["rem_pts"] == 5 and j["split"]["rem_min"] == 30   # min done blank → whole time


def test_pty_typed_remainder_overrides_the_autofill(tmp_path):
    # pts done 15 → rem pts auto 5; the user overrides rem pts to 8, leaves rem min
    rc, out, _, _, _ = _run(tmp_path, "15\t20\t\t\x15" "8\x13", focus="split")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["split"]["rem_pts"] == 8 and j["split"]["rem_min"] == 10


def test_pty_mother_edit_ctrl_s_saves_from_anywhere(tmp_path):
    # focus starts on [N] pts: replace 20 → 25, Tab to (N) min, type 45, ^S
    rc, out, _, _, _ = _run(tmp_path, "\x15" "25\t\x15" "45\x13")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["points"] == "25" and j["time"] == "45" and j["split"] is None


def test_pty_rejects_done_over_total_and_stays_open(tmp_path):
    rc, out, _, _, screen = _run(tmp_path, "99\x13" "\x1b", focus="split")
    assert rc == 2 and not out.exists()
    assert "pts done 99 > total 20" in screen


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
