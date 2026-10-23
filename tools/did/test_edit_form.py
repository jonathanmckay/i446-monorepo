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


def test_prefill_short_title_disables_haiku_and_reads_the_bonus(ef):
    p = ef.prefill({"id": "t2", "content": "call LR {15}", "labels": [], "due": "2026-10-22"}, None)
    assert not p["short_enabled"] and p["points"] == "" and p["bonus"] == "15" and p["has_bonus"]
    assert p["due"] == "2026-10-22"
    assert not p["desc_loaded"] and p["desc"] == ""
    q = ef.prefill({"id": "t3", "content": "call LR (10) [5]", "labels": []}, None)
    assert q["bonus"] == "" and not q["has_bonus"] and q["due"] == ""


def test_remainder_values_are_independent_and_prefill_with_the_totals(ef):
    assert ef.remainder("20", "30", "15", "10") == ("5", "20")
    assert ef.remainder("20", "", "15", "") == ("5", "")
    assert ef.remainder("", "30", "15", "40") == ("", "0")
    assert ef.remainder("20", "30", "", "") == ("20", "30")     # nothing done yet = whole total
    assert ef.remainder("", "", "", "") == ("", "")


def test_validate_numbers_and_bounds(ef):
    base = {"points": "5", "time": "20", "total_pts": "20", "total_min": "30", "total_bonus": "10"}
    assert ef.validate({**base, "done_pts": "15", "done_min": "10"}) is None
    assert "number" in ef.validate({**base, "done_pts": "ten"})
    assert "> current" in ef.validate({**base, "done_pts": "25"})
    assert "> current" in ef.validate({**base, "done_pts": "5", "done_min": "45"})
    assert "needs pts or bonus done" in ef.validate({**base, "done_min": "5"})
    assert ef.validate({**base, "done_bonus": "4", "done_min": "5"}) is None       # bonus alone is a split
    assert "bonus done 12 > current 10" in ef.validate({**base, "done_bonus": "12"})
    assert "number" in ef.validate({**base, "bonus": "x"})
    assert "keep remainder" in ef.validate({**base, "keep": False})              # nothing done to credit
    assert ef.validate({**base, "keep": False, "done_pts": "3"}) is None
    assert "do-date" in ef.validate({**base, "due": "someday"})
    assert ef.validate({**base, "due": "fri"}) is None


def test_result_shapes_the_edit_fast_payload(ef):
    vals = {"name": " memo ", "points": "5", "time": "30", "short": "Q memo", "short_enabled": True,
            "domain": "@i9", "desc": "a\nb\n", "desc_loaded": True,
            "done_pts": "15", "done_min": "", "done_name": "drafted  it", "done_short": "did it"}
    r = ef.result(vals)
    assert r["name"] == "memo" and r["domain"] == "i9" and r["desc"] == "a\nb"
    assert r["points"] == "5" and r["time"] == "30"        # the remainder section = what the task keeps
    assert r["split"] == {"pts": 15, "bonus": None, "minutes": None, "name": "drafted it", "short": "",
                          "keep": True}
    assert r["short"] == "" and r["short_enabled"] is False   # haiku is generated, never typed
    vals.update(desc_loaded=False, done_pts="")
    r = ef.result(vals)
    assert r["desc"] is None and r["split"] is None and r["due"] == ""
    vals.update(done_bonus="5", keep=False, bonus="10", due="2026-10-30")
    r = ef.result(vals)
    assert r["split"]["pts"] is None and r["split"]["bonus"] == 5 and r["split"]["keep"] is False
    assert r["bonus"] == "10" and r["due"] == "2026-10-30"


def test_parse_date_forms(ef):
    import datetime as dt
    t = dt.date(2026, 10, 22)                      # a Thursday
    assert ef.parse_date("tmrw", t) == "2026-10-23" and ef.parse_date("today", t) == "2026-10-22"
    assert ef.parse_date("fri", t) == "2026-10-23" and ef.parse_date("thu", t) == "2026-10-29"
    assert ef.parse_date("10/24", t) == "2026-10-24" and ef.parse_date("1.5", t) == "2027-01-05"
    assert ef.parse_date("+3", t) == "2026-10-25" and ef.parse_date("2026-11-02", t) == "2026-11-02"
    assert ef.parse_date("month", t) is None and ef.parse_date("13/40", t) is None


# ── pty: the real form ──────────────────────────────────────────────────────

def _env(tmp_path, content=LONG):
    state = tmp_path / "state"
    (state / "jm" / "task-detail").mkdir(parents=True)
    (state / "jm" / "task-detail" / "t1.json").write_text(json.dumps({
        "task": {"id": "t1", "content": content, "description": "old notes", "labels": ["i9"]},
        "comments": []}))
    cache = {"today": [{"id": "t1", "content": content, "labels": ["i9"], "short": "Quarterly memo (30) [20]"}]}
    (tmp_path / "cache.json").write_text(json.dumps(cache))
    return dict(os.environ, TERM="xterm-256color", XDG_STATE_HOME=str(state), LANG="en_US.UTF-8",
                LC_ALL="en_US.UTF-8", LINES="40", COLUMNS="100")


def _run(tmp_path, keys: str, focus="mother", env_extra=None, content=LONG):
    env = _env(tmp_path, content)
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
    for label in ("DONE SO FAR", "min done", "pts done", "REMAINDER", "(N) min", "[N] pts", "haiku", "name", "COMMON",
                  "notes", "project"):
        assert label in screen, label
    # three sections: DONE SO FAR, REMAINDER, COMMON; name → haiku → min → pts inside
    assert screen.index("DONE SO FAR") < screen.index("name") < screen.index("haiku") < screen.index("min done") \
        < screen.index("pts done") < screen.index("REMAINDER") < screen.index("(N) min") < screen.index("[N] pts") \
        < screen.index("COMMON") < screen.index("notes") < screen.index("project")
    paint = (stamps["edit-form-paint"] - t0) * 1000
    assert paint < 200, f"form took {paint:.0f} ms from exec to first paint"


def test_pty_sections_are_separated_by_a_blank_line_and_the_name_wraps(tmp_path):
    long = "write the quarterly planning memo for the whole org and the regional leads (30) [20]"
    rc, out, _, _, screen = _run(tmp_path, "\x1b", content=long)
    assert rc == 2
    lines = screen.splitlines()
    # rendered via cursor addressing: find the rows by their labels
    import re as _re
    rows = {}
    for m in _re.finditer(r"(DONE SO FAR|REMAINDER|COMMON|min done|\(N\) min|project)", screen):
        rows.setdefault(m.group(1), m.start())
    assert rows["DONE SO FAR"] < rows["min done"] < rows["REMAINDER"] < rows["(N) min"] < rows["COMMON"] < rows["project"]


def test_pty_ctrl_c_cancels_with_rc_2(tmp_path):
    rc, out, _, _, _ = _run(tmp_path, "\x03")
    assert rc == 2 and not out.exists()


def test_pty_ctrl_p_done_section_drives_the_remainder_and_ctrl_s_saves(tmp_path):
    # ctrl-p lands on the done name (prefilled); Tab → min done 20, Tab → pts
    # done 15 → the remainder's (N) min 10 / [N] pts 5 fill themselves
    rc, out, _, _, screen = _run(tmp_path, "\t20\t15\x13", focus="split")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["split"] == {"pts": 15, "bonus": None, "minutes": 20,
                          "name": "write the quarterly planning memo for the whole org", "short": "", "keep": True}
    assert j["points"] == "5" and j["time"] == "10"
    assert j["name"] == "write the quarterly planning memo for the whole org"
    assert j["desc"] == "old notes"


def test_pty_short_pane_still_shows_every_section(tmp_path):
    rc, out, _, _, screen = _run(tmp_path, "\t\t15\x13", focus="split", env_extra={"LINES": "22"})
    assert rc == 0
    assert "REMAINDER" in screen and "[N] pts" in screen and "project" in screen
    j = json.loads(out.read_text())
    assert j["points"] == "5" and j["time"] == "30"       # min done blank → whole time kept


def test_pty_typed_remainder_overrides_the_autofill(tmp_path):
    # min done 20 / pts done 15 → remainder auto (10, 5); ^O jumps to the
    # remainder name, Tab Tab → [N] pts, override to 8
    rc, out, _, _, _ = _run(tmp_path, "\t20\t15\x0f\t\t\x15" "8\x13", focus="split")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["points"] == "8" and j["time"] == "10"


def test_pty_mother_edit_ctrl_s_saves_from_anywhere(tmp_path):
    # ctrl-o: focus starts on the remainder's name; Tab → (N) min 45, Tab →
    # [N] pts 25 (= current values, nothing done), ^S
    rc, out, _, _, _ = _run(tmp_path, "\t\x15" "45\t\x15" "25\x13")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["points"] == "25" and j["time"] == "45" and j["split"] is None


def test_pty_rejects_done_over_total_and_stays_open(tmp_path):
    rc, out, _, _, screen = _run(tmp_path, "\t\t99\x13" "\x1b", focus="split")
    assert rc == 2 and not out.exists()
    assert "pts done 99 > current 20" in screen


BONUS = "ship the -1g memo for the whole org (30) [20] {10}"


def test_pty_bonus_only_shows_with_curly_and_drives_its_remainder(tmp_path):
    # no {N}: neither bonus field is drawn
    rc, _, _, _, screen = _run(tmp_path, "\x1b")
    assert rc == 2 and "bonus" not in screen and "do-date" in screen and "keep remainder" in screen
    # {N}: done → min 20, pts 15, bonus 4; the remainder's {N} follows to 6 and
    # the do-date to today (the remainder stays in dtd)
    rc, out, _, _, screen = _run(tmp_path / "b", "\t20\t15\t4\x13", focus="split", content=BONUS)
    assert rc == 0 and "{N} bonus" in screen
    j = json.loads(out.read_text())
    assert j["split"]["bonus"] == 4 and j["split"]["pts"] == 15 and j["split"]["keep"] is True
    assert (j["points"], j["bonus"], j["time"]) == ("5", "6", "10")
    assert j["due"] == ""          # untouched do-date: the split script keeps the remainder on today
    import datetime as _dt
    assert _dt.date.today().isoformat() in screen
    assert (_dt.date.today() + _dt.timedelta(days=1)).isoformat() not in screen


def test_pty_unchecking_keep_remainder_is_partial_credit(tmp_path):
    # pts done 15, ^O to the remainder, Tab ×4 → keep, space unchecks, ^S
    rc, out, _, _, screen = _run(tmp_path, "\t\t15\x0f\t\t\t\t \x13", focus="split")
    assert rc == 0, screen[-300:]
    j = json.loads(out.read_text())
    assert j["split"]["keep"] is False and j["split"]["pts"] == 15


def test_pty_typed_do_date_is_saved_as_iso(tmp_path):
    import datetime as dt
    rc, out, _, _, _ = _run(tmp_path, "\t\t15\x0f\t\t\t\x15+2\x13", focus="split")
    assert rc == 0
    j = json.loads(out.read_text())
    assert j["due"] == (dt.date.today() + dt.timedelta(days=2)).isoformat()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_pty_mouse_reports_never_type_into_a_field(tmp_path):
    """Bug 2026-10-22: "dtd more info screen is taking in mouse movements and
    clicks as input to things like remainder name". fzf leaves mouse reporting
    on; a report (ESC [ < b ; x ; y M) read as Esc + key dropped the "ESC [" as
    an alt-combo and typed "<0;16;15M" into the focused field. Each report goes
    in as ONE write, the way the terminal sends it."""
    mouse = ["\x1b[<35;20;10M", "\x1b[<0;20;10M", "\x1b[<0;20;10m", "\x1b[<64;20;10M",
             "\x1b[M #("]                                   # SGR motion/press/release/wheel, legacy X10
    rc, out, _, _, screen = _run(tmp_path, mouse + ["\x13"])
    assert rc == 0, "a mouse report must not cancel the form either"
    j = json.loads(out.read_text())
    assert j["name"] == "write the quarterly planning memo for the whole org", j["name"]
    assert "<0;" not in screen and "<35;" not in screen
