#!/usr/bin/env python3
"""Regression 2026-11-08: "I hit ctrl+l in the details page for 'search for
remote chief of staff' nothing happened." The details screen (edit-form.py)
had no ctrl-l branch: a checkbox focus swallowed \\x0c, any other field
dropped it. ctrl-l now opens the task's first link (title, notes or comments)
in its Chrome profile, or says there is none."""
import importlib.util
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("edit_form", HERE / "edit-form.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["edit_form"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_ctrl_l_reaches_open_link_from_any_field():
    ef = _load()
    for check in (True, False):
        hits = []
        fake = SimpleNamespace(focus=SimpleNamespace(check=check, multiline=False), msg="x",
                               open_link=lambda: hits.append(1))
        assert ef.Form.handle(fake, "\x0c") is None
        assert hits == [1]


def _form(ef, task, bundle):
    return SimpleNamespace(task=task, bundle=bundle, msg="")


def test_ctrl_l_opens_a_comment_link_in_the_task_profile(monkeypatch):
    ef = _load()
    calls = []
    monkeypatch.setattr(socket, "gethostname", lambda: "Straylight-Refit.local")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    form = _form(ef, {"id": "t", "content": "Search for CoS (60) [40]", "labels": ["m5x2"]},
                 {"task": {"content": "", "description": ""},
                  "comments": [{"content": "JD: https://jd.example/cos"}]})
    ef.Form.open_link(form)
    assert calls == [["open", "-na", "Google Chrome", "--args", "--profile-directory=Profile 11",
                      "--new-window", "https://jd.example/cos"]]
    assert "m5c7.com" in form.msg


def test_ctrl_l_without_a_link_says_so(monkeypatch):
    ef = _load()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    form = _form(ef, {"id": "t", "content": "plain (5)", "labels": []},
                 {"task": {}, "comments": [{"content": "draft 1a151ef6bcfc3115, see h335/f693/x.md"}]})
    ef.Form.open_link(form)
    assert form.msg == "no link on this task"
