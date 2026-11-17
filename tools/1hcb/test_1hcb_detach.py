#!/usr/bin/env python3
"""1hcb --detach (2026-11-17: "move saving ... all surveys ... to the
background so that the UI is more snappy, and then we can have the bottom
notification letting me know when it is saved").

The form process must do no Excel / run.py / claim work after the last
answer: it leaves the answers in a file, hides the card, starts one detached
child and exits 0. The child (--from-json FILE --report-hdr) does the writes
and reports in dtd's status line. Nothing here touches Excel, Todoist or a
real child process.
"""
import datetime as dt
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("hcb1_detach", Path(__file__).with_name("1hcb.py"))
hcb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hcb)

THU = dt.date(2026, 11, 12)
BLANK = {"weight": "", "learned": "", "goals": ""}


class Rig:
    """Every side effect 1hcb can have, recorded in order."""

    def __init__(self, tmp_path, monkeypatch):
        self.events = []
        self.spawned = []
        self.hdr = tmp_path / "hdr"
        self.hide = tmp_path / "removed.ids"
        self.rec = tmp_path / "recovery"
        self.hdr.write_text("before\n")
        monkeypatch.setenv("DTD_HDR_FILE", str(self.hdr))
        monkeypatch.setenv("DTD_TASK_ID", "card-1hcb")
        monkeypatch.setenv("DTD_HIDE_IDS_FILE", str(self.hide))
        monkeypatch.delenv("DTD_PORT_FILE", raising=False)
        monkeypatch.delenv("DTD_HDRGEN_FILE", raising=False)
        monkeypatch.setattr(hcb, "RECOVERY_DIR", self.rec)
        monkeypatch.setattr(hcb, "LOG", tmp_path / "1hcb.log")
        monkeypatch.setattr(hcb, "read_week", lambda thu: dict(BLANK))
        monkeypatch.setattr(hcb, "week_report", lambda thu: [])
        self.excel_error = None
        self.mark = (True, "  ✓ 1 hcb → 1n+!X46 = 1m, 0分!M +30 + todoist closed")
        self.spawn_error = None

        def write(sheet, col, *, date=None, row=None, value, src=None):
            self.events.append(("excel", col, date, value))
            return {"ok": False, "error": self.excel_error} if self.excel_error else {"ok": True}
        monkeypatch.setattr(hcb.excel, "write", write)

        def mark_habit():
            self.events.append(("mark",))
            if isinstance(self.mark, Exception):
                raise self.mark
            return self.mark
        monkeypatch.setattr(hcb, "mark_habit", mark_habit)
        monkeypatch.setattr(hcb, "run_bounded",
                            lambda cmd, **kw: self.events.append(("run", cmd[-1])))

        def spawn(argv, log, env=None):
            # The row must already be hidden when the child starts.
            self.events.append(("spawn", self.hidden()))
            if self.spawn_error:
                raise self.spawn_error
            self.spawned.append(list(argv))
        monkeypatch.setattr(hcb.form_detach, "spawn_detached", spawn)
        monkeypatch.setattr(hcb.form_detach, "announce",
                            lambda msg, **kw: hcb.form_detach.hdr(msg, poke=False))
        self.monkeypatch = monkeypatch

    def answers(self, lines, multi):
        lines, multi = list(lines), list(multi)
        self.monkeypatch.setattr(hcb, "read_line", lambda prompt="": lines.pop(0))
        self.monkeypatch.setattr(hcb, "read_multiline", lambda: multi.pop(0))

    def hidden(self):
        return self.hide.exists() and "card-1hcb" in self.hide.read_text().split()

    def header(self):
        return self.hdr.read_text().strip()

    def pending(self):
        return sorted(self.rec.glob("pending-*.json")) if self.rec.exists() else []

    def writes(self):
        return [e for e in self.events if e[0] in ("excel", "mark", "run")]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def _child_file(rig, **over):
    rig.rec.mkdir(parents=True, exist_ok=True)
    f = rig.rec / "pending-2026-11-12-1.json"
    data = {"week": "2026-11-12", "weight": 191.4, "learned": "• slept more",
            "goals": "• 3 HIIT", "no_mark": False}
    data.update(over)
    f.write_text(json.dumps(data, ensure_ascii=False))
    return f


# ── the form process ────────────────────────────────────────────────────────
def test_detach_spawns_one_child_and_writes_nothing_itself(rig):
    rig.answers(["191.4"], ["slept more\nless sugar", "3 HIIT"])
    assert hcb.main(["2026-11-12", "--detach"]) == 0
    assert rig.writes() == [], "no Excel / run.py / claim work in the form process"
    assert len(rig.spawned) == 1
    argv = rig.spawned[0]
    (f,) = rig.pending()
    assert argv[1:] == [str(Path(hcb.__file__).resolve()), "--from-json", str(f), "--report-hdr"]
    assert json.loads(f.read_text()) == {
        "week": "2026-11-12", "weight": 191.4,
        "learned": "• slept more\n• less sugar", "goals": "• 3 HIIT", "no_mark": False}
    assert rig.header() == "⏳ 1 hcb week 11/12: saving in the background …"


def test_detach_hides_the_card_before_the_child_starts(rig):
    rig.answers(["191.4"], ["", ""])
    assert hcb.main(["2026-11-12", "--detach"]) == 0
    assert rig.events == [("spawn", True)]
    assert rig.hidden()


def test_detach_no_mark_leaves_the_card_visible(rig):
    rig.answers(["191.4"], ["", ""])
    assert hcb.main(["2026-11-12", "--detach", "--no-mark"]) == 0
    assert rig.events == [("spawn", False)] and not rig.hidden()
    assert json.loads(rig.pending()[0].read_text())["no_mark"] is True


def test_spawn_failure_saves_inline_and_unhides(rig):
    rig.spawn_error = OSError("fork failed")
    rig.answers(["191.4"], ["slept more", "3 HIIT"])
    assert hcb.main(["2026-11-12", "--detach"]) == 0
    assert rig.spawned == [] and rig.pending() == []
    assert rig.writes() == [("excel", "BG", "11/12", "191.4"),
                            ("excel", "BH", "11/12", "• slept more"),
                            ("excel", "BJ", "11/12", "• 3 HIIT"),
                            ("mark",), ("run", "release")]
    assert not rig.hidden(), "the inline close reports for itself: no optimistic hide left behind"
    assert rig.header() == "before"


def test_unwritable_recovery_dir_saves_inline(rig, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    rig.monkeypatch.setattr(hcb, "RECOVERY_DIR", blocker / "sub")
    rig.answers(["191.4"], ["", ""])
    assert hcb.main(["2026-11-12", "--detach"]) == 0
    assert rig.spawned == [] and not rig.hidden()
    assert rig.writes() == [("excel", "BG", "11/12", "191.4"), ("mark",), ("run", "release")]


def test_cancel_spawns_nothing(rig):
    rig.answers([""], [])                       # no weight = abort
    assert hcb.main(["2026-11-12", "--detach"]) == 2
    assert rig.events == [] and rig.spawned == [] and rig.pending() == []
    assert not rig.hidden() and rig.header() == "before"


def test_declined_overwrite_spawns_nothing(rig):
    rig.monkeypatch.setattr(hcb, "read_week", lambda thu: dict(BLANK, weight="190"))
    rig.answers(["n"], [])
    assert hcb.main(["2026-11-12", "--detach"]) == 2
    assert rig.events == [] and rig.pending() == []


def test_without_detach_saves_inline_as_before(rig):
    rig.answers(["191.4"], ["slept more", ""])
    assert hcb.main(["2026-11-12"]) == 0
    assert rig.spawned == [] and rig.pending() == [] and not rig.hidden()
    assert rig.writes() == [("excel", "BG", "11/12", "191.4"),
                            ("excel", "BH", "11/12", "• slept more"),
                            ("mark",), ("run", "release")]
    assert rig.header() == "before"


def test_scripted_path_ignores_detach(rig):
    assert hcb.main(["2026-11-12", "--weight", "191.4", "--goals", "x", "--detach"]) == 0
    assert rig.spawned == [] and rig.pending() == []
    assert rig.writes()[:2] == [("excel", "BG", "11/12", "191.4"), ("excel", "BJ", "11/12", "• x")]


# ── the child ───────────────────────────────────────────────────────────────
def test_child_writes_marks_releases_then_reports_saved(rig):
    f = _child_file(rig)
    rig.hide.write_text("other\ncard-1hcb\n")
    rig.monkeypatch.setattr(hcb, "read_week", lambda thu: pytest.fail("the child reads nothing"))
    assert hcb.main(["--from-json", str(f), "--report-hdr"]) == 0
    assert rig.writes() == [("excel", "BG", "11/12", "191.4"),
                            ("excel", "BH", "11/12", "• slept more"),
                            ("excel", "BJ", "11/12", "• 3 HIIT"),
                            ("mark",), ("run", "release")]
    assert rig.header() == "✓ 1 hcb week 11/12 saved (BG, BH, BJ)"
    assert not f.exists(), "Excel has the answers: the file is done"
    assert rig.hidden(), "a confirmed close stays hidden"


def test_child_excel_failure_keeps_the_file_and_unhides(rig):
    f = _child_file(rig)
    rig.hide.write_text("card-1hcb\n")
    rig.excel_error = "excel_error: timeout"
    assert hcb.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header() == ("✗ 1 hcb week 11/12: save FAILED (Excel: write hcbi!BG 11/12: "
                            "excel_error: timeout) answers in %s" % f)
    assert json.loads(f.read_text())["learned"] == "• slept more"
    assert ("mark",) not in rig.events, "no habit credit for answers that were not written"
    assert not rig.hidden()
    # ...and the kept file replays once Excel is back.
    rig.excel_error = None
    assert hcb.main(["--from-json", str(f)]) == 0
    assert not f.exists()


@pytest.mark.parametrize("mark, why", [
    ((False, "  ✗ 1 hcb: todoist card not found"), "✗ 1 hcb: todoist card not found"),
    ((False, "(no output from did run.py)"), "(no output from did run.py)"),
    (subprocess.TimeoutExpired("run.py", 120), "TimeoutExpired"),
])
def test_child_unconfirmed_close_unhides_the_card(rig, mark, why):
    f = _child_file(rig)
    rig.hide.write_text("card-1hcb\n")
    rig.mark = mark
    assert hcb.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header().startswith("✓ 1 hcb week 11/12 saved (BG, BH, BJ) · NOT closed: ")
    if why:
        assert rig.header().endswith(why)
    assert not rig.hidden(), "still open in Todoist: the row comes back"
    assert not f.exists(), "the answers are in Excel"
    assert ("run", "release") in rig.events


def test_child_no_mark_only_writes(rig):
    f = _child_file(rig, no_mark=True, goals="")
    assert hcb.main(["--from-json", str(f), "--report-hdr"]) == 0
    assert rig.writes() == [("excel", "BG", "11/12", "191.4"), ("excel", "BH", "11/12", "• slept more")]
    assert rig.header() == "✓ 1 hcb week 11/12 saved (BG, BH)"


def test_child_unreadable_file_reports_the_path(rig):
    f = rig.rec / "pending-x.json"
    rig.rec.mkdir(parents=True)
    f.write_text("{not json")
    rig.hide.write_text("card-1hcb\n")
    assert hcb.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header().startswith("✗ 1 hcb: save FAILED (answers file unreadable")
    assert rig.header().endswith("answers in %s" % f)
    assert f.exists() and rig.events == [] and not rig.hidden()


def test_child_crash_still_unhides_and_reports_the_path(rig):
    f = _child_file(rig)
    rig.hide.write_text("card-1hcb\n")
    rig.monkeypatch.setattr(hcb, "written_cols", lambda *a: 1 / 0)
    assert hcb.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header().startswith("✗ 1 hcb: save FAILED (ZeroDivisionError")
    assert rig.header().endswith("answers in %s" % f)
    assert not rig.hidden()


def test_mark_habit_confirms_only_a_reported_close(monkeypatch):
    class P:
        def __init__(self, rc, out, err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err
    spec = importlib.util.spec_from_file_location("hcb1_mark", Path(__file__).with_name("1hcb.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    outs = iter([P(0, "  ✓ 1 hcb → 1n+!X46 = 1m, 0分!M +30 + todoist closed\n"), P(0, ""),
                 P(0, "  ✓ 1 hcb → 1n+!X46 = 1m, 0分!M +30\n"), P(0, ""),
                 P(1, "", "boom\n"), P(0, "")])
    calls = []
    monkeypatch.setattr(m, "run_bounded", lambda cmd, **kw: calls.append(cmd) or next(outs))
    assert m.mark_habit() == (True, "✓ 1 hcb → 1n+!X46 = 1m, 0分!M +30 + todoist closed")
    assert m.mark_habit() == (False, "✓ 1 hcb → 1n+!X46 = 1m, 0分!M +30")
    assert m.mark_habit() == (False, "boom")
    assert [c[-1] for c in calls] == ["1 hcb", "--refresh-cache"] * 3
    monkeypatch.setattr(m, "mark_habit", lambda: (False, "the line"))
    assert m.mark_done() == "the line"
