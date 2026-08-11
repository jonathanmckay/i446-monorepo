#!/usr/bin/env python3
"""Feature: swipe-left-to-start-timer in dtd web (2026-08-11).

start_timer() mirrors terminal dtd's `enter` binding (tools/did/dtd.sh's
DTD_START heredoc) exactly: ritual cards resolve their Toggl project from
the same RITUAL_DOMAIN table used for coloring; everything else falls back
to tg-fast.py --resolve (the same resolver /tg already uses). The current
timer is always stopped before the new one starts.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_start_timer_ritual_resolves_via_ritual_domain_not_tg_fast(monkeypatch):
    calls = []
    def fake_run(args, **kw):
        calls.append(args)
        return _Proc()
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    result = dtd.start_timer("😈 -1g (15) [15]")

    assert result == {"ok": True, "clean": "-1g", "project": "g245"}
    tg_calls = [c for c in calls if str(dtd.TG_FAST) in c]
    assert not tg_calls, "a ritual card must resolve via RITUAL_DOMAIN, never shell out to tg-fast"


def test_start_timer_plain_task_shells_out_to_tg_fast_resolve(monkeypatch):
    calls = []
    def fake_run(args, **kw):
        calls.append(args)
        if str(dtd.TG_FAST) in args:
            return _Proc(stdout="i9\n")
        return _Proc()
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    result = dtd.start_timer("notes (3) [8]")

    assert result == {"ok": True, "clean": "notes", "project": "i9"}
    resolve_calls = [c for c in calls if str(dtd.TG_FAST) in c]
    assert resolve_calls == [["/usr/bin/python3", str(dtd.TG_FAST), "--resolve", "notes"]]


def test_start_timer_always_stops_before_starting(monkeypatch):
    order = []
    def fake_run(args, **kw):
        if str(dtd.TOGGL_CLI) in args and "stop" in args:
            order.append("stop")
        elif str(dtd.TOGGL_CLI) in args and "start" in args:
            order.append("start")
        return _Proc()
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    dtd.start_timer("plain task")
    assert order == ["stop", "start"], "the current timer must be stopped before the new one starts"


def test_start_timer_surfaces_toggl_failure(monkeypatch):
    def fake_run(args, **kw):
        if str(dtd.TOGGL_CLI) in args and "start" in args:
            return _Proc(returncode=1, stderr="toggl API down")
        return _Proc()
    monkeypatch.setattr(dtd.subprocess, "run", fake_run)

    result = dtd.start_timer("plain task")
    assert result["ok"] is False
    assert "toggl API down" in result["error"]


def test_api_start_rejects_empty_content():
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/start", json={"content": "  "})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_api_start_wires_to_start_timer(monkeypatch):
    monkeypatch.setattr(dtd, "start_timer",
                        lambda raw: {"ok": True, "clean": raw, "project": "hcb"})
    dtd.app.config["TESTING"] = True
    client = dtd.app.test_client()
    r = client.post("/api/start", json={"content": "eat lunch"})
    d = r.get_json()
    assert d == {"ok": True, "clean": "eat lunch", "project": "hcb"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
