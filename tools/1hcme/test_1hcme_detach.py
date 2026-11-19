"""1hcme --detach (2026-11-17): "move saving ... to the background so that the
UI is more snappy, and then we can have the bottom notification letting me
know when it is saved."

The form process writes the answer to a file and starts one detached child;
the child (--from-json) does the 0s897 write, then the habit mark, and
reports in dtd's status line. Nothing here touches Excel, Todoist or a real
child: every outbound call is a fake, and an unexpected one fails the test.
"""
import datetime as dt
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CLOSED = "  ✓ 1 hcme → 1n+!X9 = 1m, 0分!Q +10 + todoist closed\n"
CREDITED = "  ✓ 1 hcme → 1n+!X9 = 1m, 0分!Q +10\n"
ROLLED = "  = 1 hcme already done this week: 1 hcme (next due 2026-11-12); no credit, card left alone\n"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("one_hcme_detach", HERE / "1hcme.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["one_hcme_detach"] = m
    spec.loader.exec_module(m)
    return m


class Env:
    """One dtd session: header file, hide file, recovery dir, call log."""

    def __init__(self, hm, tmp_path, monkeypatch, in_dtd=True):
        self.hm = hm
        self.calls = []          # every outbound call, in order
        self.spawned = []        # argv of each detached child
        self.announced = []      # the child's final status lines
        self.hdr = tmp_path / "hdr"
        self.hdr.write_text("before\n")
        self.hide = tmp_path / "removed.ids"
        self.rec = tmp_path / "recovery"
        self.run_py = types.SimpleNamespace(returncode=0, stdout=CLOSED, stderr="")
        self.write_error = None
        self.spawn_error = None
        self.answer = "calm\ntired"
        monkeypatch.setattr(hm, "RECOVERY_DIR", self.rec)
        monkeypatch.setattr(hm, "LOG", tmp_path / "1hcme.log")
        for k in ("DTD_HDR_FILE", "DTD_PORT_FILE", "DTD_HDRGEN_FILE", "DTD_TASK_ID",
                  "DTD_HIDE_IDS_FILE", "FORM_DETACH_PARENT"):
            monkeypatch.delenv(k, raising=False)
        if in_dtd:
            monkeypatch.setenv("DTD_HDR_FILE", str(self.hdr))
            monkeypatch.setenv("DTD_TASK_ID", "T1")
            monkeypatch.setenv("DTD_HIDE_IDS_FILE", str(self.hide))
        trec = {"row": 300, "cells": {"A": ("11.1", (255, 255, 255), (0, 0, 0))}}
        monkeypatch.setattr(hm, "load", lambda target, today: (
            hm.window(target, today), {0: trec}, None, trec))
        self.blank_0l = set()    # habit days whose 0n!S is blank
        self.unreadable = set()  # habit days the daemon could not read
        self.read_days = []
        self.made = []           # ensure_0l_cards calls

        def _read_0l(day):
            self.read_days.append(day)
            if day in self.unreadable:
                return None
            return "" if day in self.blank_0l else "1"
        monkeypatch.setattr(hm, "read_0l", _read_0l)
        monkeypatch.setattr(hm, "ensure_0l_cards", lambda days, today: (
            self.made.append(list(days)) or [(d, "created") for d in days]))
        monkeypatch.setattr(hm, "read_answer", lambda: self.answer)
        monkeypatch.setattr(hm, "write_answer", self._write)
        monkeypatch.setattr(hm, "run_bounded", self._run)
        monkeypatch.setattr(hm.neon_excel, "batch_write",
                            lambda *a, **k: pytest.fail("no daemon call in these tests"))
        monkeypatch.setattr(hm.form_detach, "spawn_detached", self._spawn)
        monkeypatch.setattr(hm.form_detach, "announce",
                            lambda msg, **k: self.announced.append(msg) or True)
        monkeypatch.setattr(hm.shutil, "which", lambda name: None)   # never `cmux close-surface`

    def _write(self, row, text):
        self.calls.append(("write", row, text, self.hidden()))
        if self.write_error:
            raise self.write_error
        return "OK: 0s897!X%d" % row

    def _run(self, argv, **kw):
        if argv[-1] == "--refresh-cache":
            self.calls.append(("refresh", self.hidden()))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        assert argv[1:] == [str(self.hm.DID_RUN), self.hm.HABIT], argv
        self.calls.append(("run.py", self.hidden()))
        if isinstance(self.run_py, BaseException):
            raise self.run_py
        return self.run_py

    def _spawn(self, argv, log, env=None):
        if self.spawn_error:
            raise self.spawn_error
        # dtd_hide rule: the row is hidden BEFORE the child exists.
        self.spawned.append((list(argv), self.hidden(), self.pending()))
        return types.SimpleNamespace(pid=4242)

    def hidden(self):
        return self.hide.exists() and "T1" in self.hide.read_text().split()

    def pending(self):
        return sorted(self.rec.glob("pending-*.json")) if self.rec.exists() else []

    def form(self, *args):
        return self.hm.main(["--today", "2026-11-05", *args])

    def kinds(self):
        return [c[0] for c in self.calls]

    def spool(self, **over):
        self.rec.mkdir(parents=True, exist_ok=True)
        p = self.rec / "pending-2026-11-05-1.json"
        data = {"target": "2026-11-05", "row": 300, "label": "11.1",
                "text": "• calm\n• tired", "no_mark": False}
        data.update(over)
        p.write_text(json.dumps(data, ensure_ascii=False))
        return p

    def child(self, path):
        return self.hm.main(["--from-json", str(path), "--unlink-json", "--report-hdr"])


@pytest.fixture
def env(mod, tmp_path, monkeypatch):
    return Env(mod, tmp_path, monkeypatch)


# ── the form process ────────────────────────────────────────────────────────

def test_detach_spawns_one_child_and_writes_nothing_itself(env):
    assert env.form("--detach") == 0
    assert env.calls == []                       # no Excel, no run.py, no refresh
    assert len(env.spawned) == 1
    argv, hidden_at_spawn, files_at_spawn = env.spawned[0]
    assert hidden_at_spawn is True               # hide BEFORE spawn
    assert len(files_at_spawn) == 1              # answers on disk BEFORE spawn
    path = files_at_spawn[0]
    assert argv == [sys.executable, str((HERE / "1hcme.py").resolve()),
                    "--from-json", str(path), "--unlink-json", "--report-hdr"]
    assert env.hdr.read_text() == "⏳ 1hcme week 11.1: saving in the background …\n"
    assert env.announced == []                   # the final line is the child's


def test_detach_answers_file_is_what_the_child_needs(env):
    env.form("--detach")
    (path,) = env.pending()
    assert path.parent == env.rec and path.name.startswith("pending-2026-11-05-")
    assert json.loads(path.read_text()) == {
        "target": "2026-11-05", "row": 300, "label": "11.1",
        "text": "• calm\n• tired", "no_mark": False}


def test_detach_no_mark_leaves_the_card_visible(env):
    assert env.form("--detach", "--no-mark") == 0
    assert len(env.spawned) == 1 and env.spawned[0][1] is False   # never hidden
    assert not env.hidden()
    assert json.loads(env.pending()[0].read_text())["no_mark"] is True


def test_spawn_failure_falls_back_to_the_inline_save(env):
    env.spawn_error = OSError("fork: resource temporarily unavailable")
    assert env.form("--detach") == 0
    assert env.kinds() == ["write", "run.py", "refresh"]          # today's save, today's order
    assert env.calls[0][1:3] == (300, "• calm\n• tired")
    assert not env.hidden()                       # the hide was undone
    assert env.pending() == []                    # nothing left pending
    assert env.hdr.read_text() == "before\n"      # wrapper writes its own tick


def test_spawn_failure_then_failed_write_keeps_the_answer(env):
    env.spawn_error = OSError("nope")
    env.write_error = RuntimeError("daemon echo_mismatch")
    assert env.form("--detach") == 1              # never 0 with the answer unwritten
    assert (env.rec / "2026-11-05.txt").read_text() == "• calm\n• tired"


def test_unwritable_answers_file_saves_inline(env, monkeypatch):
    blocker = env.rec.parent / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(env.hm, "RECOVERY_DIR", blocker / "sub")  # mkdir fails: not a directory
    assert env.form("--detach") == 0
    assert env.spawned == [] and env.kinds() == ["write", "run.py", "refresh"]
    assert not env.hidden()


def test_cancelled_form_spawns_nothing(env):
    env.answer = ""                               # Ctrl+C / empty answer
    assert env.form("--detach") == 1
    assert env.spawned == [] and env.calls == [] and env.pending() == []
    assert not env.hidden() and env.hdr.read_text() == "before\n"


def test_without_detach_the_save_is_inline_as_before(env):
    assert env.form() == 0
    assert env.spawned == [] and env.pending() == []
    assert env.kinds() == ["write", "run.py", "refresh"]
    assert not env.hide.exists() and env.hdr.read_text() == "before\n"


def test_from_text_never_detaches(env):
    assert env.form("--detach", "--from-text", "steady") == 0
    assert env.spawned == [] and env.kinds() == ["write", "run.py", "refresh"]


def test_mark_done_still_returns_run_py_last_line(env):
    assert env.hm.mark_done() == CLOSED.strip()
    assert env.kinds() == ["run.py", "refresh"]


# ── the detached child (--from-json --unlink-json --report-hdr) ─────────────

def test_child_writes_then_marks_and_reports_saved(env, monkeypatch):
    monkeypatch.setattr(env.hm, "load", lambda *a: pytest.fail("the child reads no sheet"))
    env.hm.dtd_hide.hide_card()
    path = env.spool()
    assert env.child(path) == 0
    assert env.kinds() == ["write", "run.py", "refresh"]
    assert env.calls[0][1:3] == (300, "• calm\n• tired")
    assert env.announced == ["✓ 1hcme week 11.1 saved · 1 hcme closed"]
    assert not path.exists()                      # consumed
    assert env.hidden()                           # closed card stays hidden


def test_child_no_mark_only_writes(env):
    path = env.spool(no_mark=True)
    assert env.child(path) == 0
    assert env.kinds() == ["write"]
    assert env.announced == ["✓ 1hcme week 11.1 saved (OK: 0s897!X300)"]
    assert not path.exists()


def test_child_write_failure_keeps_a_replayable_file_and_unhides(env):
    env.hm.dtd_hide.hide_card()
    path = env.spool()
    env.write_error = RuntimeError("0s897 write failed: echo_mismatch")
    assert env.child(path) == 1
    assert "run.py" not in env.kinds()            # no habit credit for an unwritten answer
    (msg,) = env.announced
    assert msg.startswith("✗ 1hcme week 11.1 save FAILED (0s897 write: ")
    assert "echo_mismatch" in msg and msg.endswith("answers in %s" % path)
    assert not env.hidden()                       # the card is still open: show it
    assert env.kinds()[-1] == "refresh" and env.calls[-1][1] is False   # reload sees it unhidden
    # ...and the file replays as is once Excel is back.
    env.write_error = None
    env.calls.clear()
    env.announced.clear()
    assert env.child(path) == 0
    assert env.calls[0][1:3] == (300, "• calm\n• tired") and not path.exists()
    assert env.announced == ["✓ 1hcme week 11.1 saved · 1 hcme closed"]


def test_child_write_timeout_is_a_failure_not_a_crash(env):
    env.hm.dtd_hide.hide_card()
    path = env.spool()
    env.write_error = subprocess.TimeoutExpired("ix-osa", 90)
    assert env.child(path) == 1
    assert env.announced[0].endswith("answers in %s" % path) and path.exists()
    assert not env.hidden()


def test_child_unconfirmed_close_unhides_before_the_reload(env):
    env.hm.dtd_hide.hide_card()
    env.run_py = types.SimpleNamespace(returncode=0, stdout=CREDITED, stderr="")
    path = env.spool()
    assert env.child(path) == 1
    assert env.kinds() == ["write", "run.py", "refresh"]
    assert env.calls[1][1] is True and env.calls[2][1] is False   # hidden → unhidden → refresh
    (msg,) = env.announced
    assert msg.startswith("✗ 1hcme week 11.1 saved (0s897!X300) and credited")
    assert "NOT closed" in msg and "do not /did" in msg   # run.py already credited 1n+
    assert not path.exists()                      # the answer IS in Excel


def test_child_run_py_failure_and_timeout_unhide(env):
    for failure in (types.SimpleNamespace(returncode=1, stdout="", stderr="✗ excel daemon down\n"),
                    subprocess.TimeoutExpired("run.py", 120)):
        env.hm.dtd_hide.hide_card()
        env.run_py = failure
        env.announced.clear()
        assert env.child(env.spool()) == 1
        assert not env.hidden()
        (msg,) = env.announced
        assert msg.startswith("✗ 1hcme week 11.1 saved (0s897!X300), but marking 1 hcme done FAILED (")


def test_child_already_done_this_week_is_not_an_error(env):
    env.hm.dtd_hide.hide_card()
    env.run_py = types.SimpleNamespace(returncode=0, stdout=ROLLED, stderr="")
    assert env.child(env.spool()) == 0
    assert env.announced == ["✓ 1hcme week 11.1 saved · 1 hcme was already done this week, card left alone"]
    assert not env.hidden()                       # card left open in Todoist


def test_child_unreadable_file_reports_the_path(env):
    env.hm.dtd_hide.hide_card()
    env.rec.mkdir(parents=True)
    path = env.rec / "pending-2026-11-05-9.json"
    path.write_text("{not json")
    assert env.child(path) == 1
    assert "write" not in env.kinds()
    assert env.announced[0].startswith("✗ 1hcme save FAILED (answers file unreadable")
    assert env.announced[0].endswith("answers in %s" % path) and path.exists()
    assert not env.hidden()


def test_manual_replay_keeps_the_file_and_the_status_line(env):
    path = env.spool()
    assert env.hm.main(["--from-json", str(path)]) == 0
    assert env.announced == [] and path.exists()
    assert env.kinds() == ["write", "run.py", "refresh"]


def test_stale_pending_files_are_named_at_the_next_run(env, capsys):
    import os
    path = env.spool()
    assert env.hm.stale_pending() == []           # a save in flight is not stale
    old = dt.datetime(2026, 11, 5).timestamp()
    os.utime(path, (old, old))
    assert env.hm.stale_pending() == [path]
    env.answer = ""
    env.form()
    assert "--from-json %s" % path in capsys.readouterr().out


# ── 0l gate (2026-11-19): "/1hcme enforces that all the 0s are filled out",
# then "create tasks for the days that I need to finish 0l". The --today
# 2026-11-05 run reviews 10/29-11/4, so the 0l habit days are 10/30-11/5. ────

D = dt.date


def test_a_blank_0l_day_blocks_the_check_in_and_gets_a_card(env, capsys):
    env.blank_0l = {D(2026, 11, 2)}
    assert env.form("--from-text", "calm") == 1
    assert env.calls == [] and env.spawned == []          # nothing written, nothing marked
    assert env.made == [[D(2026, 11, 2)]]
    out = capsys.readouterr().out
    assert "0l 11.2" in out and "0s about Su 11/1" in out and "card created" in out
    assert "card left open" in out


def test_all_0l_done_saves_as_before_and_makes_no_card(env):
    assert env.form("--from-text", "calm") == 0
    assert env.kinds()[0] == "write" and env.made == []


def test_todays_0l_is_the_live_cards_job(env):
    env.blank_0l = {D(2026, 11, 5)}                       # 0s about We 11/4, due today
    assert env.form("--from-text", "calm") == 0
    assert D(2026, 11, 5) not in env.read_days and env.made == []


def test_an_unreadable_day_does_not_block(env):
    env.unreadable = {D(2026, 11, 2)}
    assert env.form("--from-text", "calm") == 0 and env.made == []


def test_skip_0s_check_asks_anyway_and_makes_no_card(env):
    env.blank_0l = {D(2026, 11, 2)}
    assert env.form("--skip-0s-check", "--from-text", "calm") == 0
    assert env.kinds()[0] == "write" and env.made == [] and env.read_days == []


def test_print_context_reports_without_creating_cards(env, capsys):
    env.blank_0l = {D(2026, 10, 30), D(2026, 11, 4)}
    assert env.form("--print-context") == 0
    out = capsys.readouterr().out
    assert "0l 10.30" in out and "0l 11.4" in out and env.made == []


def test_cards_are_made_once_and_shaped_like_a_deferred_0l(mod, tmp_path, monkeypatch):
    cache = tmp_path / "task-queue.json"
    cache.write_text(json.dumps({"updated": "x", "today": [
        {"id": "1", "content": "0l 11.16 (8) [20]"}, {"id": "2", "content": "0l (8) [20]"}]}))
    monkeypatch.setattr(mod.state_paths, "TASK_QUEUE", cache)
    created, refreshed = [], []
    fake = types.SimpleNamespace(create_task=lambda *a: created.append(a))
    monkeypatch.setattr(importlib.util, "spec_from_file_location",
                        lambda name, path: types.SimpleNamespace(
                            loader=types.SimpleNamespace(exec_module=lambda m: None)))
    monkeypatch.setattr(importlib.util, "module_from_spec", lambda spec: fake)
    monkeypatch.setattr(mod, "_refresh_cache", lambda: refreshed.append(1))
    res = mod.ensure_0l_cards([D(2026, 11, 16), D(2026, 11, 18)], D(2026, 11, 19))
    assert res == [(D(2026, 11, 16), "exists"), (D(2026, 11, 18), "created")]
    assert created == [("0l 11.18 (8) [20]", ["0neon", "g245"], None, "2026-11-19")]
    assert refreshed == [1]
