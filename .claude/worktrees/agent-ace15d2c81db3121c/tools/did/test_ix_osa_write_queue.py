"""Regression: failed Neon writes must be queued for replay, not lost.

2026-07-20: a morning of dtd habit completions (cpap, wake up, charge, teams,
ibx s897, plus the {5}/[15] point appends for -1n and evening hcmc) never
reached the Neon workbook. dtd marks tasks completed optimistically, and
ix-osa.py's run() simply returned a failing IxResult when ssh-to-ix timed out
(undo journal 10:15:47: p_credit_error "ix unreachable ... (timeout)") or
Excel raised AppleEvent -1712 — the writes had no retry path and were silently
dropped. The fix: run() appends failed WRITE scripts to ix-write-queue.jsonl
(the file ix-drain-queue.sh replays). These tests pin that behavior.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_IX_PATH = Path.home() / ".claude/skills/_lib/ix-osa.py"

WRITE_SCRIPT = 'tell application "Microsoft Excel"\n    set value of cell "A1" to 1\nend tell'
READ_SCRIPT = 'tell application "Microsoft Excel"\n    return value of cell "A1"\nend tell'


def _load_ix_osa(queue_path: Path):
    import os
    os.environ["IX_WRITE_QUEUE"] = str(queue_path)
    spec = importlib.util.spec_from_file_location("ix_osa_under_test", _IX_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ix_osa_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def ix(tmp_path):
    queue = tmp_path / "queue.jsonl"
    mod = _load_ix_osa(queue)
    return mod, queue


def _queued(queue: Path):
    if not queue.exists():
        return []
    return [json.loads(l) for l in queue.read_text().splitlines() if l.strip()]


def test_timeout_queues_write(ix, monkeypatch):
    mod, queue = ix

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=30)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res = mod.run(WRITE_SCRIPT)
    assert res.returncode == 3
    entries = _queued(queue)
    assert len(entries) == 1
    assert entries[0]["script"] == WRITE_SCRIPT
    assert entries[0]["reason"] == "timeout"


def test_unreachable_queues_write(ix, monkeypatch):
    mod, queue = ix
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 255, stdout="", stderr="ssh: no route"),
    )
    res = mod.run(WRITE_SCRIPT)
    assert res.returncode == 3
    assert [e["reason"] for e in _queued(queue)] == ["unreachable"]


def test_appleevent_timeout_queues_write(ix, monkeypatch):
    mod, queue = ix
    stderr = "execution error: Microsoft Excel got an error: AppleEvent timed out. (-1712)"
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, stdout="", stderr=stderr),
    )
    res = mod.run(WRITE_SCRIPT)
    assert res.returncode == 2
    assert [e["reason"] for e in _queued(queue)] == ["appleevent-timeout"]


def test_logic_error_not_queued(ix, monkeypatch):
    """A deterministic AppleScript failure (bad sheet, date not found) would
    fail identically on replay — it must NOT be queued."""
    mod, queue = ix
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a, 1, stdout="", stderr="execution error: sheet not found (-1728)"),
    )
    res = mod.run(WRITE_SCRIPT)
    assert res.returncode == 2
    assert _queued(queue) == []


def test_read_script_never_queued(ix, monkeypatch):
    """Read-only scripts are side-effect-free; replaying them is pointless."""
    mod, queue = ix

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=30)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    res = mod.run(READ_SCRIPT)
    assert res.returncode == 3
    assert _queued(queue) == []


def test_success_not_queued(ix, monkeypatch):
    mod, queue = ix
    monkeypatch.setattr(
        mod.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, stdout="OK", stderr=""),
    )
    monkeypatch.setattr(mod, "_notify_janus", lambda script: None)
    res = mod.run(WRITE_SCRIPT)
    assert res.returncode == 0
    assert _queued(queue) == []
