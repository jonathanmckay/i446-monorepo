"""-1g form (2026-10-16): three optional lines -> build-order block goals (on ix)
+ Todoist #-1g tasks, no Claude subprocess. Save logic tested with the writers
mocked; the planning step is pure."""
import importlib.machinery
import importlib.util
import io
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def form():
    loader = importlib.machinery.SourceFileLoader("one_g_form", str(HERE / "-1g-form.py"))
    spec = importlib.util.spec_from_loader("one_g_form", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_plan_goals_get_points_domain_and_plain_todos_split(form):
    p = form.plan(["ship the dtd form @i447 {12}", "call dentist [5]", ""], "午")
    assert p["goals"] == [{"text": "ship the dtd form {12}", "domain": "i447"}]
    assert p["todos"] == [{"text": "call dentist [5]", "domain": "i9"}]


def test_plan_defaults_points_and_block_domain(form):
    p = form.plan(["write the memo", "", ""], "酉")
    assert p["goals"] == [{"text": "write the memo {10}", "domain": "m5x2"}]


def test_all_blank_writes_nothing(form, monkeypatch):
    monkeypatch.setattr(form, "_append_on_ix", lambda *a: pytest.fail("must not write"))
    out = io.StringIO()
    assert form.save(["", "", ""], block="午", out=out) == 0
    assert "nothing written" in out.getvalue()


def test_save_appends_on_ix_creates_tasks_refreshes_and_closes_ritual(form, monkeypatch):
    appended, created, ran = [], [], []
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g: (appended.append((b, g)) or (g, "ix")))

    class T:
        @staticmethod
        def create_task(content, **kw):
            created.append((content, kw))
            return {"id": "x"}
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    out = io.StringIO()
    rc = form.save(["fix janus @i447", "", "call dentist [5]"], block="巳", out=out)
    assert rc == 0
    assert appended == [("巳", ["fix janus {10}"])]
    assert created[0] == ("fix janus {10}", {"labels": ["#-1g", "i447"], "due_string": "today",
                                            "priority": 4, "project_id": form.GOALS_PROJECT})
    assert created[1] == ("call dentist [5]", {"labels": ["i9"], "due_string": "today"})
    tails = [c[-2:] for c in ran]
    assert ["--refresh-cache"] == tails[0][-1:] and tails[1] == ["--ritual", "-1g"]
    assert "1 goals, 1 todos" in out.getvalue()


def test_todos_only_do_not_close_the_ritual(form, monkeypatch):
    ran = []

    class T:
        @staticmethod
        def create_task(content, **kw):
            return {"id": "x"}
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    form.save(["buy milk [3]", "", ""], block="巳", out=io.StringIO())
    assert all("--ritual" not in c for c in ran)


def test_todoist_failure_is_reported_nonzero(form, monkeypatch):
    monkeypatch.setattr(form, "_append_on_ix", lambda b, g: (g, "ix"))

    class T:
        @staticmethod
        def create_task(content, **kw):
            raise RuntimeError("boom")
    monkeypatch.setitem(sys.modules, "todoist", T)
    monkeypatch.setattr(form.subprocess, "run", lambda cmd, **kw: None)
    out = io.StringIO()
    assert form.save(["a goal", "", ""], block="巳", out=out) == 1
    assert "FAILED" in out.getvalue()


def test_form_has_escape_save_and_ctrl_q_discard(form):
    src = (HERE / "-1g-form.py").read_text()
    assert '@kb.add("escape", eager=True)' in src and '@kb.add("c-q")' in src
