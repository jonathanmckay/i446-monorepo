#!/usr/bin/env python3
"""Regression (2026-10-05): a dtd web swipe-complete must call did-fast the way
terminal dtd does: annotation-stripped name + --task-id.

Bug: /api/done passed the raw card ("i447 (15) [5]"). did-fast read [5] as a
deliberate points override and kept "(15)" in the name, so the 0n header
"i447" never matched: the recurring card was closed (so dtd hid it as done)
while the 0n habit stayed unmarked and +5 landed on a domain column instead.
Hit 早餐, wake up, i444, i447, charge and tmrw in one 05:51 phone session."""
import importlib.util
import subprocess
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("dtd_web_done", Path(__file__).parent / "dtd.py")
dtd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dtd)


def _capture(monkeypatch):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"results": []}', stderr="")
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)
    return calls


def test_api_done_sends_stripped_name_and_task_id(monkeypatch):
    calls = _capture(monkeypatch)
    client = dtd.app.test_client()
    r = client.post("/api/done", json={"id": "6gHVV7fjPwqfvq76", "content": "i447 (15) [5]"})
    assert r.status_code == 200
    args = calls[-1]
    assert args[-1] == "i447", f"did-fast must get the bare habit name, got {args[-1]!r}"
    assert args[args.index("--task-id") + 1] == "6gHVV7fjPwqfvq76"


def test_variable_value_survives_cleaning(monkeypatch):
    # the variable-input path sends "<title> <value>"; the value must stay
    calls = _capture(monkeypatch)
    dtd.complete("hiit 25", "X1")
    assert calls[-1][-1] == "hiit 25"


def test_no_id_still_works(monkeypatch):
    calls = _capture(monkeypatch)
    dtd.complete("wake up (15) [6]")
    assert "--task-id" not in calls[-1] and calls[-1][-1] == "wake up"
