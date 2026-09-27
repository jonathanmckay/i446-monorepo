"""Regression tests for agency_mcp's interactive-login guard (2026-09-25).

Bug: Agency has no silent mode. With an invalid Entra token cache it opened
a NEW Safari tab on login.microsoftonline.com at every server start and on
every 401, then waited 15 minutes; the unattended dream run and dashboard
jobs produced 50-90 tabs a day for weeks. Fix: if a sign-in tab is already
open and unfinished, refuse to start a server or forward a call
(EntraLoginPending). One tab is the cue to sign in; a second one is noise.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agency_mcp as am

LOGIN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?x=1"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(am, "_LOGIN_TAB_CACHE", {"at": 0.0, "url": None})
    monkeypatch.delenv("AGENCY_REMOTE_CALENDAR_PORT", raising=False)
    am._servers.clear()


def test_call_tool_refuses_while_login_tab_open(monkeypatch):
    started, called = [], []
    monkeypatch.setattr(am, "_safari_login_tab", lambda: LOGIN_URL)
    monkeypatch.setattr(am, "get_server", lambda name: started.append(name) or 1)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: called.append(a) or {})
    with pytest.raises(am.EntraLoginPending) as ei:
        am.call_tool("calendar", "ListEvents", {})
    assert "login.microsoftonline.com" in str(ei.value)
    assert started == [] and called == [], "must not touch Agency at all"


def test_get_server_refuses_fresh_start_while_login_tab_open(monkeypatch):
    spawned = []
    monkeypatch.setattr(am, "_safari_login_tab", lambda: LOGIN_URL)
    monkeypatch.setattr(am, "_read_pidfile", lambda name: (None, None))
    monkeypatch.setattr(am, "_start_server", lambda name: spawned.append(name) or (None, 1))
    with pytest.raises(am.EntraLoginPending):
        am.get_server("mail")
    assert spawned == []


def test_get_server_reuses_healthy_server_without_checking_safari(monkeypatch):
    # Adopting an already-running server opens no tab; the per-call guard in
    # call_tool covers the 401 case. So no AppleScript here.
    checks = []
    monkeypatch.setattr(am, "_safari_login_tab", lambda: checks.append(1) or LOGIN_URL)
    monkeypatch.setattr(am, "_read_pidfile", lambda name: (4242, 5555))
    monkeypatch.setattr(am, "_is_alive", lambda pid: True)
    monkeypatch.setattr(am, "_port_responsive", lambda port: True)
    assert am.get_server("mail") == 5555
    assert checks == []


def test_no_login_tab_means_normal_path(monkeypatch):
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "get_server", lambda name: 1)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: {"ok": True})
    assert am.call_tool("calendar", "ListEvents", {}) == {"ok": True}


def test_remote_endpoint_skips_guard(monkeypatch):
    # The tab check is local; a remote Agency's browser is not ours.
    monkeypatch.setenv("AGENCY_REMOTE_CALENDAR_PORT", "9999")
    monkeypatch.setattr(am, "_safari_login_tab", lambda: LOGIN_URL)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: {"remote": True})
    assert am.call_tool("calendar", "ListEvents", {}) == {"remote": True}


def test_guard_is_cached_per_burst(monkeypatch):
    calls = []
    monkeypatch.setattr(am, "_safari_login_tab", lambda: calls.append(1) or None)
    monkeypatch.setattr(am, "get_server", lambda name: 1)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: {})
    for _ in range(5):
        am.call_tool("mail", "ListMailFolders", {})
    assert len(calls) == 1
    assert am.entra_login_pending(force=True) is None and len(calls) == 2


def test_safari_not_running_never_launches_it(monkeypatch):
    ran = []
    monkeypatch.setattr(am, "_safari_running", lambda: False)
    monkeypatch.setattr(am.subprocess, "run", lambda *a, **k: ran.append(a) or None)
    assert am._safari_login_tab() is None
    assert ran == [], "no osascript when Safari is closed (tell application would launch it)"


def test_osascript_failure_does_not_block(monkeypatch):
    # Automation permission denied / timeout -> unknown -> let the call through.
    class R:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(am, "_safari_running", lambda: True)
    monkeypatch.setattr(am.subprocess, "run", lambda *a, **k: R())
    assert am._safari_login_tab() is None


def test_pending_error_is_runtimeerror_for_existing_callers():
    assert issubclass(am.EntraLoginPending, RuntimeError)
