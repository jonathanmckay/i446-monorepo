"""1i9 --detach (2026-11-17): "move saving ... to the background so that the
UI is more snappy, and then we can have the bottom notification letting me
know when it is saved."

The form process writes the answers to a file and starts one detached child;
the child (--from-json) does the i9+m5x2 write, the habit mark, the cache
refresh and the claim release, then reports in dtd's status line. Nothing
here touches Excel, Todoist, Toggl or a real child: every outbound call is a
fake, and an unexpected one fails the test.
"""
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CLOSED = "  ✓ 1 i9 → 1n+!R47 = 1m, 0分!R +30 + todoist closed\n"
CREDITED = "  ✓ 1 i9 → 1n+!R47 = 1m, 0分!R +30\n"
ROLLED = "  = 1 i9 already done this week: 1 i9 (next due 2026-11-23); no credit, card left alone\n"
ANSWERS = {"week": "2026-11-08", "row": 47, "label": "11.2", "rating": "MA",
           "done": "shipped X\nmet Y", "exceed": "ship Z", "minutes": 2134}


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("one_i9_detach", HERE / "1i9.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["one_i9_detach"] = m
    spec.loader.exec_module(m)
    return m


class Env:
    """One dtd session: header file, hide file, recovery dir, call log."""

    def __init__(self, m, tmp_path, monkeypatch):
        self.m = m
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
        self.rating = "MA"
        self.texts = ["shipped X\nmet Y", "ship Z"]
        monkeypatch.setattr(m, "RECOVERY_DIR", self.rec)
        monkeypatch.setattr(m, "LOG", tmp_path / "1i9.log")
        for k in ("DTD_PORT_FILE", "DTD_HDRGEN_FILE", "FORM_DETACH_PARENT"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("DTD_HDR_FILE", str(self.hdr))
        monkeypatch.setenv("DTD_TASK_ID", "T9")
        monkeypatch.setenv("DTD_HIDE_IDS_FILE", str(self.hide))
        monkeypatch.setattr(m, "resolve_week", lambda arg, today: (
            dt.date(2026, 11, 8), dt.date(2026, 11, 14), 47, "11.2"))
        monkeypatch.setattr(m, "i9_minutes", lambda s, e: 2134)
        monkeypatch.setattr(m, "i9_points", lambda s, e: 288)
        monkeypatch.setattr(m, "check_label", lambda row, label: "11.2")
        monkeypatch.setattr(m, "ask_rating", self._rating)
        answers = iter(self.texts)
        monkeypatch.setattr(m, "ask_multiline", lambda q: next(answers))
        monkeypatch.setattr(m, "write_row", self._write)
        monkeypatch.setattr(m, "run_bounded", self._run)
        monkeypatch.setattr(m, "_osa", lambda *a, **k: pytest.fail("no ix-osa in these tests"))
        monkeypatch.setattr(m.neon_excel, "batch_write",
                            lambda *a, **k: pytest.fail("no daemon call in these tests"))
        monkeypatch.setattr(m.form_detach, "spawn_detached", self._spawn)
        monkeypatch.setattr(m.form_detach, "announce",
                            lambda msg, **k: self.announced.append(msg) or True)

    def _rating(self):
        if not self.rating:
            raise SystemExit("aborted: no rating")
        return self.rating

    def _write(self, row, rating, done, exceed, minutes, label=None):
        self.calls.append(("write", (row, rating, done, exceed, minutes, label), self.hidden()))
        if self.write_error:
            raise self.write_error
        return "OK: i9+m5x2!row %d" % row

    def _run(self, argv, **kw):
        if argv[-1] == "--refresh-cache":
            self.calls.append(("refresh", None, self.hidden()))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[-1] == "release":
            assert argv[1] == str(self.m.AGENT_CLAIMS)
            self.calls.append(("release", None, self.hidden()))
            return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")
        assert argv[1:] == [str(self.m.DID_RUN), self.m.HABIT], argv
        self.calls.append(("run.py", None, self.hidden()))
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
        return self.hide.exists() and "T9" in self.hide.read_text().split()

    def pending(self):
        return sorted(self.rec.glob("pending-*.json")) if self.rec.exists() else []

    def kinds(self):
        return [c[0] for c in self.calls]

    def spool(self, **over):
        self.rec.mkdir(parents=True, exist_ok=True)
        p = self.rec / "pending-2026-11-08-1.json"
        p.write_text(json.dumps(dict(ANSWERS, **over), ensure_ascii=False))
        return p

    def child(self, path):
        return self.m.main(["--from-json", str(path), "--unlink-json", "--report-hdr"])


@pytest.fixture
def env(mod, tmp_path, monkeypatch):
    return Env(mod, tmp_path, monkeypatch)


WRITE_ARGS = (47, "MA", "shipped X\nmet Y", "ship Z", 2134, "11.2")
INLINE = ["write", "run.py", "refresh", "release"]


# ── the form process ────────────────────────────────────────────────────────

def test_detach_spawns_one_child_and_writes_nothing_itself(env):
    assert env.m.main(["--detach"]) == 0
    assert env.calls == []                       # no Excel, no run.py, no refresh, no release
    assert len(env.spawned) == 1
    argv, hidden_at_spawn, files_at_spawn = env.spawned[0]
    assert hidden_at_spawn is True               # hide BEFORE spawn
    assert len(files_at_spawn) == 1              # answers on disk BEFORE spawn
    path = files_at_spawn[0]
    assert argv == [sys.executable, str((HERE / "1i9.py").resolve()),
                    "--from-json", str(path), "--unlink-json", "--report-hdr"]
    assert env.hdr.read_text() == "⏳ 1i9 week 11.2: saving in the background …\n"
    assert env.announced == []                   # the final line is the child's


def test_detach_answers_file_is_what_the_child_needs(env):
    env.m.main(["--detach"])
    (path,) = env.pending()
    assert path.parent == env.rec and path.name.startswith("pending-2026-11-08-")
    assert json.loads(path.read_text()) == ANSWERS


def test_spawn_failure_falls_back_to_the_inline_save(env):
    env.spawn_error = OSError("fork: resource temporarily unavailable")
    assert env.m.main(["--detach"]) == 0
    assert env.kinds() == INLINE                  # today's save, today's order
    assert env.calls[0][1] == WRITE_ARGS
    assert not env.hidden()                       # the hide was undone
    assert env.pending() == []                    # written: nothing left pending
    assert env.hdr.read_text() == "before\n"      # wrapper writes its own tick


def test_spawn_failure_then_failed_write_keeps_the_answers(env, capsys):
    env.spawn_error = OSError("nope")
    env.write_error = RuntimeError("i9+m5x2 write failed: echo_mismatch")
    assert env.m.main(["--detach"]) == 1          # never 0 with the answers unwritten
    (path,) = env.pending()
    assert json.loads(path.read_text()) == ANSWERS
    assert str(path) in capsys.readouterr().out
    assert env.kinds() == ["write"]               # no habit credit for an unwritten row


def test_unwritable_answers_file_saves_inline(env, monkeypatch):
    blocker = env.rec.parent / "blocker"
    blocker.write_text("")
    monkeypatch.setattr(env.m, "RECOVERY_DIR", blocker / "sub")   # mkdir fails: not a directory
    assert env.m.main(["--detach"]) == 0
    assert env.spawned == [] and env.kinds() == INLINE
    assert not env.hidden()


def test_cancelled_form_spawns_nothing(env):
    env.rating = ""                               # blank rating aborts
    with pytest.raises(SystemExit) as e:
        env.m.main(["--detach"])
    assert e.value.code not in (0, None)
    assert env.spawned == [] and env.calls == [] and env.pending() == []
    assert not env.hidden() and env.hdr.read_text() == "before\n"


def test_empty_got_done_spawns_nothing(env):
    env.texts[0] = ""
    assert env.m.main(["--detach"]) == 1
    assert env.spawned == [] and env.calls == [] and env.pending() == []
    assert not env.hidden()


def test_without_detach_the_save_is_inline_as_before(env, capsys):
    assert env.m.main([]) == 0
    assert env.spawned == [] and env.pending() == []
    assert env.kinds() == INLINE and env.calls[0][1] == WRITE_ARGS
    assert not env.hide.exists() and env.hdr.read_text() == "before\n"
    out = capsys.readouterr().out
    assert "written to i9+m5x2!row 47 (B, D, E, F)" in out and "habit: " + CLOSED.strip() in out


def test_without_detach_a_failed_write_reads_as_before(env, capsys):
    env.write_error = RuntimeError("boom")
    assert env.m.main([]) == 1
    assert "answers were not saved; rerun /1-i9 to try again" in capsys.readouterr().out
    assert env.kinds() == ["write"]


def test_mark_done_still_returns_a_string(env):
    # tools/dtd/dtd_forms.py concatenates it.
    assert env.m.mark_done() == CLOSED.strip()
    assert env.kinds() == ["run.py", "refresh"]


# ── the detached child (--from-json --unlink-json --report-hdr) ─────────────

def test_child_does_the_same_writes_in_the_same_order(env, monkeypatch):
    for name in ("resolve_week", "i9_minutes", "i9_points", "check_label"):
        monkeypatch.setattr(env.m, name, lambda *a, **k: pytest.fail("the child reads nothing"))
    env.m.dtd_hide.hide_card()
    path = env.spool()
    assert env.child(path) == 0
    assert env.kinds() == INLINE
    assert env.calls[0][1] == WRITE_ARGS
    assert env.announced == ["✓ 1i9 week 11.2 saved · 1 i9 closed"]
    assert not path.exists()                      # consumed
    assert env.hidden()                           # closed card stays hidden


def test_child_write_failure_keeps_a_replayable_file_and_unhides(env):
    env.m.dtd_hide.hide_card()
    path = env.spool()
    env.write_error = RuntimeError("i9+m5x2 write failed: echo_mismatch")
    assert env.child(path) == 1
    assert "run.py" not in env.kinds()            # no habit credit for an unwritten row
    (msg,) = env.announced
    assert msg.startswith("✗ 1i9 week 11.2 save FAILED (i9+m5x2 write: ")
    assert "echo_mismatch" in msg and msg.endswith("answers in %s" % path)
    assert not env.hidden()                       # the card is still open: show it
    assert env.calls[-1][0] == "refresh" and env.calls[-1][2] is False
    # ...and the file replays as is once Excel is back.
    env.write_error = None
    env.calls.clear()
    env.announced.clear()
    assert env.child(path) == 0
    assert env.calls[0][1] == WRITE_ARGS and not path.exists()
    assert env.announced == ["✓ 1i9 week 11.2 saved · 1 i9 closed"]


def test_child_write_timeout_is_a_failure_not_a_crash(env):
    env.m.dtd_hide.hide_card()
    path = env.spool()
    env.write_error = subprocess.TimeoutExpired("ix-osa", 120)
    assert env.child(path) == 1
    assert env.announced[0].endswith("answers in %s" % path) and path.exists()
    assert not env.hidden()


def test_child_unconfirmed_close_unhides_before_the_reload(env):
    env.m.dtd_hide.hide_card()
    env.run_py = types.SimpleNamespace(returncode=0, stdout=CREDITED, stderr="")
    path = env.spool()
    assert env.child(path) == 1
    assert env.kinds() == INLINE
    assert env.calls[1][2] is True and env.calls[2][2] is False   # hidden → unhidden → refresh
    (msg,) = env.announced
    assert msg.startswith("✗ 1i9 week 11.2 saved (i9+m5x2!row 47) and credited")
    assert "NOT closed" in msg and "do not /did" in msg   # run.py already credited 1n+
    assert not path.exists()                      # the answers ARE in Excel


def test_child_run_py_failure_and_timeout_unhide(env):
    for failure in (types.SimpleNamespace(returncode=1, stdout="", stderr="✗ excel daemon down\n"),
                    subprocess.TimeoutExpired("run.py", 120)):
        env.m.dtd_hide.hide_card()
        env.run_py = failure
        env.announced.clear()
        env.calls.clear()
        assert env.child(env.spool()) == 1
        assert not env.hidden()
        assert env.kinds()[-1] == "release"       # the claim is released either way, as inline
        (msg,) = env.announced
        assert msg.startswith("✗ 1i9 week 11.2 saved (i9+m5x2!row 47), but marking 1 i9 done FAILED (")


def test_child_already_done_this_week_is_not_an_error(env):
    env.m.dtd_hide.hide_card()
    env.run_py = types.SimpleNamespace(returncode=0, stdout=ROLLED, stderr="")
    assert env.child(env.spool()) == 0
    assert env.announced == ["✓ 1i9 week 11.2 saved · 1 i9 was already done this week, card left alone"]
    assert not env.hidden()                       # card left open in Todoist


def test_child_unreadable_or_incomplete_file_reports_the_path(env):
    env.m.dtd_hide.hide_card()
    for bad in ({"rating": "??"}, {"done": " "}):
        path = env.spool(**bad)
        env.announced.clear()
        assert env.child(path) == 1
        assert "write" not in env.kinds()
        assert env.announced[0].startswith("✗ 1i9 save FAILED (answers file unreadable")
        assert env.announced[0].endswith("answers in %s" % path) and path.exists()
    assert not env.hidden()


def test_manual_replay_keeps_the_file_and_the_status_line(env):
    path = env.spool()
    assert env.m.main(["--from-json", str(path)]) == 0
    assert env.announced == [] and path.exists()
    assert env.kinds() == INLINE


def test_stale_pending_files_are_named_at_the_next_run(env, capsys):
    path = env.spool()
    assert env.m.stale_pending() == []            # a save in flight is not stale
    old = dt.datetime(2026, 11, 8).timestamp()
    os.utime(path, (old, old))
    assert env.m.stale_pending() == [path]
    env.m.main(["--print-context"])
    assert "--from-json %s" % path in capsys.readouterr().out
