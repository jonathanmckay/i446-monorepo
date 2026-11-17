#!/usr/bin/env python3
"""1f693 --detach (2026-11-17: "move saving ... all surveys ... to the
background so that the UI is more snappy, and then we can have the bottom
notification letting me know when it is saved").

After Esc the form process must make no Todoist / run.py call: it appends the
doc entry (local), leaves the slots in a pending file, hides the card, starts
one detached child, and still opens Obsidian + the stoplight tab itself. The
child (--from-json FILE --report-hdr) files the tasks, marks the habit and
reports in dtd's status line. Nothing here touches Todoist, run.py, `open`,
cmux or a real child process.
"""
import datetime as dt
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
_SPEC = importlib.util.spec_from_file_location("f693_detach", HERE / "1f693.py")
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)

TODAY = dt.date.today()
LABEL = m.entry_date(TODAY)
FORM = [{"initials": "ab", "task": "weekly checkin (20)"}, {"initials": "", "task": ""},
        {"initials": "cd", "task": "intro to HR"}]
SLOTS = [{"initials": "AB", "task": "weekly checkin (20)"}, {"initials": "CD", "task": "intro to HR"}]
TASKS = ["f693 AB: weekly checkin (20) [10]", "f693 CD: intro to HR (15) [10]"]
DOC0 = "intro\n\n## Current Org\n\n10.12\nold\n"
CLOSED = "✓ 1 f693 → 1n+!Q46 = 1m, 0分!AA +30 + todoist closed"


class Rig:
    """Every side effect 1f693 can have, recorded in order."""

    def __init__(self, tmp_path, monkeypatch):
        self.events = []
        self.spawned = []
        self.hdr = tmp_path / "hdr"
        self.hide = tmp_path / "removed.ids"
        self.rec = tmp_path / "recovery"
        self.doc = tmp_path / "f693.md"
        self.doc.write_text(DOC0)
        self.hdr.write_text("before\n")
        monkeypatch.setenv("DTD_HDR_FILE", str(self.hdr))
        monkeypatch.setenv("DTD_TASK_ID", "card-f693")
        monkeypatch.setenv("DTD_HIDE_IDS_FILE", str(self.hide))
        monkeypatch.delenv("DTD_PORT_FILE", raising=False)
        monkeypatch.delenv("DTD_HDRGEN_FILE", raising=False)
        monkeypatch.setattr(m, "F693_DOC", self.doc)
        monkeypatch.setattr(m, "RECOVERY_DIR", self.rec)
        monkeypatch.setattr(m, "LOG", tmp_path / "1f693.log")
        self.form = FORM
        self.fail_tasks = set()
        self.token_error = None
        self.mark = (True, CLOSED)
        self.spawn_error = None
        monkeypatch.setattr(m, "run_form", lambda: self.form)

        def token():
            self.events.append(("token",))
            if self.token_error:
                raise self.token_error
            return "tok"
        monkeypatch.setattr(m, "_todoist_token", token)

        def create_task(content, tok):
            if content in self.fail_tasks:
                self.events.append(("todoist-failed", content))
                raise m.urllib.error.URLError("timed out")
            self.events.append(("todoist", content))
            return "id"
        monkeypatch.setattr(m, "create_task", create_task)

        def mark_habit():
            self.events.append(("mark",))
            if isinstance(self.mark, Exception):
                raise self.mark
            return self.mark
        monkeypatch.setattr(m, "mark_habit", mark_habit)
        monkeypatch.setattr(m, "open_diagram", lambda: self.events.append(("diagram",)))
        # Desk-side steps record what the doc and header looked like when they ran.
        monkeypatch.setattr(m, "open_doc_in_obsidian",
                            lambda: self.events.append(("obsidian", self.entries())))
        monkeypatch.setattr(m, "launch_stoplight_tab",
                            lambda: self.events.append(("cc-tab",)) or "cc tab opened")

        def spawn(argv, log, env=None):
            # The row must already be hidden, and the slots on disk, when the child starts.
            self.events.append(("spawn", self.hidden(), len(self.pending())))
            if self.spawn_error:
                raise self.spawn_error
            self.spawned.append(list(argv))
        monkeypatch.setattr(m.form_detach, "spawn_detached", spawn)
        self.announced = []
        monkeypatch.setattr(m.form_detach, "announce",
                            lambda msg, **kw: self.announced.append(kw) or m.form_detach.hdr(msg, poke=False))
        self.monkeypatch = monkeypatch

    def hidden(self):
        return self.hide.exists() and "card-f693" in self.hide.read_text().split()

    def header(self):
        return self.hdr.read_text().strip()

    def pending(self):
        return sorted(self.rec.glob("pending-*.json")) if self.rec.exists() else []

    def entries(self):
        return self.doc.read_text().count("**Next steps:**")

    def kinds(self):
        return [e[0] for e in self.events]

    def child_file(self, **over):
        self.rec.mkdir(parents=True, exist_ok=True)
        f = self.rec / "pending-x-1.json"
        state = {"date": TODAY.isoformat(), "slots": SLOTS, "created": [], "doc_done": True,
                 "no_mark": False}
        state.update(over)
        f.write_text(json.dumps(state, ensure_ascii=False))
        return f


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


# ── the form process ────────────────────────────────────────────────────────
def test_detach_spawns_one_child_and_makes_no_todoist_or_habit_call(rig):
    assert m.main(["--detach"]) == 0
    assert rig.kinds() == ["diagram", "spawn", "obsidian", "cc-tab"]
    assert len(rig.spawned) == 1
    (f,) = rig.pending()
    assert rig.spawned[0][1:] == [str(Path(m.__file__).resolve()), "--from-json", str(f),
                                  "--report-hdr", "--no-browser", "--no-launch"]
    assert json.loads(f.read_text()) == {
        "date": TODAY.isoformat(), "slots": SLOTS, "created": [], "doc_done": True, "no_mark": False}
    assert rig.header() == "⏳ 1 f693 %s: saving in the background …" % LABEL


def test_detach_hides_before_spawn_and_opens_obsidian_onto_the_written_entry(rig):
    assert m.main(["--detach"]) == 0
    assert ("spawn", True, 1) in rig.events, "hidden, and slots on disk, before the child starts"
    assert ("obsidian", 1) in rig.events, "the entry is in the doc when Obsidian opens"
    doc = rig.doc.read_text()
    assert all("* " + t in doc for t in TASKS) and doc.index(LABEL) < doc.index("10.12")


def test_detach_no_mark_leaves_the_card_visible_and_no_launch_skips_desk_side(rig):
    assert m.main(["--detach", "--no-mark", "--no-launch", "--no-browser"]) == 0
    assert rig.events == [("spawn", False, 1)] and not rig.hidden()
    assert json.loads(rig.pending()[0].read_text())["no_mark"] is True


def test_spawn_failure_saves_inline_once_and_unhides(rig):
    rig.spawn_error = OSError("fork failed")
    assert m.main(["--detach"]) == 0
    assert rig.spawned == [] and rig.pending() == [] and not rig.hidden()
    assert rig.kinds() == ["diagram", "spawn", "token", "todoist", "todoist", "mark", "obsidian", "cc-tab"]
    assert rig.entries() == 1, "the entry the hand-off already wrote is not appended again"
    assert rig.header() == "1 f693 · +2 todoist · f693.md ✓ · %s · cc tab opened" % CLOSED


def test_unwritable_recovery_dir_saves_inline(rig, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    rig.monkeypatch.setattr(m, "RECOVERY_DIR", blocker / "sub")
    assert m.main(["--detach"]) == 0
    assert rig.spawned == [] and not rig.hidden() and rig.entries() == 1
    assert rig.kinds() == ["diagram", "token", "todoist", "todoist", "mark", "obsidian", "cc-tab"]


def test_cancel_spawns_nothing(rig):
    rig.form = None
    assert m.main(["--detach"]) == 0
    assert rig.kinds() == ["diagram"] and rig.pending() == [] and not rig.hidden()
    assert rig.doc.read_text() == DOC0 and rig.header() == "1 f693 cancelled"


def test_empty_form_spawns_nothing_and_runs_todays_inline_path(rig):
    rig.form = [{"initials": "", "task": ""}] * 6
    assert m.main(["--detach"]) == 0
    assert rig.spawned == [] and rig.pending() == [] and not rig.hidden()
    assert rig.kinds() == ["diagram", "mark", "obsidian", "cc-tab"]
    assert "_(none recorded)_" in rig.doc.read_text()


def test_without_detach_saves_inline_as_before(rig):
    assert m.main([]) == 0
    assert rig.spawned == [] and rig.pending() == [] and not rig.hidden()
    assert rig.events[:5] == [("diagram",), ("token",), ("todoist", TASKS[0]), ("todoist", TASKS[1]),
                              ("mark",)]
    assert rig.events[5:] == [("obsidian", 1), ("cc-tab",)]
    assert rig.header() == "1 f693 · +2 todoist · f693.md ✓ · %s · cc tab opened" % CLOSED


def test_plain_slots_list_from_json_keeps_the_inline_path(rig, tmp_path):
    f = tmp_path / "slots.json"
    f.write_text(json.dumps(FORM))
    assert m.main(["--from-json", str(f), "--detach", "--no-browser", "--no-launch"]) == 0
    assert rig.spawned == [] and rig.kinds() == ["token", "todoist", "todoist", "mark"]
    assert rig.entries() == 1 and f.exists()


# ── the child ───────────────────────────────────────────────────────────────
def test_child_files_tasks_marks_then_reports_saved(rig):
    f = rig.child_file()
    rig.hide.write_text("other\ncard-f693\n")
    assert m.main(["--from-json", str(f), "--report-hdr", "--no-browser", "--no-launch"]) == 0
    assert rig.events == [("token",), ("todoist", TASKS[0]), ("todoist", TASKS[1]), ("mark",)]
    assert rig.header() == "✓ 1 f693 %s saved (+2 todoist · f693.md ✓)" % LABEL
    assert rig.doc.read_text() == DOC0, "the form wrote the entry: the child must not add another"
    assert not f.exists() and rig.hidden(), "a confirmed close stays hidden"
    assert rig.announced == [{"timeout": 30}], "outwaits the form's cmux calls"


def test_child_writes_the_doc_entry_the_form_could_not(rig):
    f = rig.child_file(doc_done=False)
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 0
    assert rig.entries() == 1 and rig.kinds() == ["token", "todoist", "todoist", "mark"]


def test_child_task_failure_keeps_the_rest_replayable_without_duplicates(rig):
    f = rig.child_file()
    rig.hide.write_text("card-f693\n")
    rig.fail_tasks = {TASKS[1]}
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header() == ("✗ 1 f693 %s: save FAILED (todoist: 1 of 2 tasks not created; "
                            "+1 todoist · f693.md ✓) answers in %s" % (LABEL, f))
    state = json.loads(f.read_text())
    assert state["slots"] == SLOTS and state["created"] == [0] and state["marked"] is True
    assert rig.hidden(), "the habit did close (as it does inline when a task fails)"
    # Replay by hand: only the missing task, no second habit credit, no second entry.
    rig.events.clear()
    rig.fail_tasks = set()
    assert m.main(["--from-json", str(f)]) == 0
    assert rig.events == [("token",), ("todoist", TASKS[1])]
    assert not f.exists() and rig.doc.read_text() == DOC0


def test_child_progress_is_on_disk_after_each_task(rig):
    f = rig.child_file()
    seen = []
    real = m.create_task
    rig.monkeypatch.setattr(m, "create_task", lambda c, t: (
        seen.append(json.loads(f.read_text())["created"]), real(c, t))[1])
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 0
    assert seen == [[], [0]]


def test_child_token_failure_stops_unhides_and_keeps_everything(rig):
    f = rig.child_file()
    rig.hide.write_text("card-f693\n")
    rig.token_error = RuntimeError("TODOIST_TOKEN not found in did-fast.py")
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header() == ("✗ 1 f693 %s: save FAILED (todoist token: TODOIST_TOKEN not found in "
                            "did-fast.py) answers in %s" % (LABEL, f))
    assert rig.kinds() == ["token"] and not rig.hidden()
    assert json.loads(f.read_text())["created"] == []


@pytest.mark.parametrize("mark, why", [
    ((False, "✗ 1 f693: todoist card not found"), "✗ 1 f693: todoist card not found"),
    (subprocess.TimeoutExpired("run.py", 120), "TimeoutExpired"),
])
def test_child_unconfirmed_close_unhides_the_card(rig, mark, why):
    f = rig.child_file()
    rig.hide.write_text("card-f693\n")
    rig.mark = mark
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header() == "✓ 1 f693 %s saved (+2 todoist · f693.md ✓) · NOT closed: %s" % (LABEL, why)
    assert not rig.hidden(), "still open in Todoist: the row comes back"
    assert not f.exists(), "nothing left to file"


def test_child_no_mark_never_calls_run_py(rig):
    f = rig.child_file(no_mark=True)
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 0
    assert rig.kinds() == ["token", "todoist", "todoist"]


def test_child_unreadable_file_reports_the_path(rig):
    rig.rec.mkdir(parents=True)
    f = rig.rec / "pending-bad.json"
    f.write_text("{not json")
    rig.hide.write_text("card-f693\n")
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header().startswith("✗ 1 f693: save FAILED (answers file unreadable")
    assert rig.header().endswith("answers in %s" % f)
    assert f.exists() and rig.events == [] and not rig.hidden()


def test_child_crash_still_unhides_and_reports_the_path(rig):
    f = rig.child_file()
    rig.hide.write_text("card-f693\n")
    rig.monkeypatch.setattr(m, "task_content", lambda *a: 1 / 0)
    assert m.main(["--from-json", str(f), "--report-hdr"]) == 1
    assert rig.header().startswith("✗ 1 f693: save FAILED (ZeroDivisionError")
    assert rig.header().endswith("answers in %s" % f)
    assert f.exists() and not rig.hidden()


def test_dry_run_of_a_pending_file_writes_nothing(rig, capsys):
    f = rig.child_file()
    assert m.main(["--from-json", str(f), "--dry-run"]) == 0
    assert rig.events == [] and f.exists() and rig.doc.read_text() == DOC0
    assert "todoist: " + TASKS[0] in capsys.readouterr().out


def test_mark_habit_confirms_only_a_reported_close(monkeypatch):
    class P:
        def __init__(self, rc, out, err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err
    spec = importlib.util.spec_from_file_location("f693_mark", HERE / "1f693.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    outs = iter([P(0, "  %s\n" % CLOSED), P(0, ""), P(0, "  ✓ 1 f693 → 1n+!Q46\n"), P(0, ""),
                 P(1, "", "boom\n"), P(0, "")])
    calls = []
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or next(outs))
    assert mod.mark_habit() == (True, CLOSED)
    assert mod.mark_habit() == (False, "✓ 1 f693 → 1n+!Q46")
    assert mod.mark_habit() == (False, "boom")
    assert [c[-1] for c in calls] == ["1 f693", "--refresh-cache"] * 3
    monkeypatch.setattr(mod, "mark_habit", lambda: (False, "the line"))
    assert mod.mark_done() == "the line"
