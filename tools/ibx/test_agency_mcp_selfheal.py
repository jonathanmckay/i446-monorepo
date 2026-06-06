"""Regression tests for agency_mcp self-healing.

Bug: pidfile reuse keeps one Agency server per type alive indefinitely; a
wedged process (calendar server up 2d3h, calls timing out at 95s+) poisoned
every caller until killed by hand. call_tool must kill + respawn + retry once
on timeout, but NOT on tool-level errors (the server answered; a fresh
process won't change the answer).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agency_mcp as am


def test_timeout_triggers_restart_and_retry(monkeypatch):
    calls = {"n": 0}
    restarts = []

    def fake_once(host, port, server, tool, args, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise am.AgencyTimeout("No result (wedged)")
        return {"ok": True}

    monkeypatch.setattr(am, "_call_tool_once", fake_once)
    monkeypatch.setattr(am, "get_server", lambda name: 12345)
    monkeypatch.setattr(am, "restart_server", lambda name: restarts.append(name))
    monkeypatch.delenv("AGENCY_REMOTE_CALENDAR_PORT", raising=False)

    result = am.call_tool("calendar", "ListEvents", {})
    assert result == {"ok": True}
    assert restarts == ["calendar"], "wedged server must be restarted exactly once"
    assert calls["n"] == 2


def test_tool_error_does_not_restart(monkeypatch):
    restarts = []

    def fake_once(host, port, server, tool, args, timeout):
        raise RuntimeError({"code": -32000, "message": "bad arguments"})

    monkeypatch.setattr(am, "_call_tool_once", fake_once)
    monkeypatch.setattr(am, "get_server", lambda name: 12345)
    monkeypatch.setattr(am, "restart_server", lambda name: restarts.append(name))
    monkeypatch.delenv("AGENCY_REMOTE_CALENDAR_PORT", raising=False)

    try:
        am.call_tool("calendar", "ListEvents", {})
        raise AssertionError("expected RuntimeError")
    except am.AgencyTimeout:
        raise AssertionError("tool error must not be classified as timeout")
    except RuntimeError:
        pass
    assert restarts == [], "tool-level errors must not trigger a server restart"


def test_remote_endpoint_never_restarts(monkeypatch):
    restarts = []

    def fake_once(host, port, server, tool, args, timeout):
        raise am.AgencyTimeout("remote wedged")

    monkeypatch.setattr(am, "_call_tool_once", fake_once)
    monkeypatch.setattr(am, "restart_server", lambda name: restarts.append(name))
    monkeypatch.setenv("AGENCY_REMOTE_CALENDAR_PORT", "9999")

    try:
        am.call_tool("calendar", "ListEvents", {})
        raise AssertionError("expected AgencyTimeout")
    except am.AgencyTimeout:
        pass
    assert restarts == [], "remote servers are not ours to restart"


def test_timeout_exception_is_runtimeerror_subclass():
    # Existing callers catch RuntimeError; AgencyTimeout must stay compatible.
    assert issubclass(am.AgencyTimeout, RuntimeError)
