#!/usr/bin/env python3
"""Regression (2026-10-11): marking a task done in dtd must stop the running
Toggl/janus timer for THAT task. stop_matching_toggl compared names exactly,
so a timer whose description kept an annotation ("post babyssiter ad [15]",
started from janus/tg) or a ritual card ("😈 -1g", whose timer reads "-1g")
kept running. Ritual-only completions also returned before the stop step.
An unrelated running timer must never be stopped.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


@pytest.fixture()
def df(monkeypatch):
    spec = importlib.util.spec_from_file_location("did_fast_stop", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_stop"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_toggl(monkeypatch, current: str):
    calls = []

    def run(argv, **kw):
        calls.append(argv[-1])
        if argv[-1] == "current":
            return subprocess.CompletedProcess(argv, 0, current + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "Stopped: x (12min)\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


@pytest.mark.parametrize("timer,item", [
    ("post babyssiter ad [15]", "post babyssiter ad"),   # janus kept [N]
    ("-1g", "😈 -1g"),                                     # ritual card
    ("Source Logging (30)", "source logging"),
    ("1s", "1s"),
])
def test_stops_timer_for_completed_task(df, monkeypatch, timer, item):
    calls = _fake_toggl(monkeypatch, f"Running: 08:25-running {timer} @g245 (running) [id:1]")
    out = df.stop_matching_toggl([item])
    assert out and out["stopped"] and "stop" in calls
    assert out["minutes"] == 12


def test_leaves_unrelated_timer_running(df, monkeypatch):
    calls = _fake_toggl(monkeypatch, "Running: 08:25-running 1s @g245 (running) [id:1]")
    assert df.stop_matching_toggl(["1 s897", "1g", "s"]) is None
    assert "stop" not in calls


def test_no_timer_running(df, monkeypatch):
    calls = _fake_toggl(monkeypatch, "No timer running")
    assert df.stop_matching_toggl(["1s"]) is None
    assert calls == ["current"]


def test_ritual_only_completion_reaches_the_stop(df, monkeypatch):
    """The ritual-only early return in main() must call stop_matching_toggl."""
    src = (_HERE / "did-fast.py").read_text()
    early = src[src.index("        if not items:\n            # Ritual-only completion"):]
    early = early[:early.index("_emit(")]
    assert "stop_matching_toggl(" in early
    assert '[e["name"] for e in ritual_entries]' in src.split("all_names = ", 1)[1].split("\n", 1)[0]
