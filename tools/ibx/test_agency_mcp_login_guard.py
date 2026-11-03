"""Regression tests for agency_mcp's interactive-login guard (2026-09-25).

Bug: Agency has no silent mode. With an invalid Entra token cache it opened
a NEW Safari tab on login.microsoftonline.com at every server start and on
every 401, then waited 15 minutes; the unattended dream run and dashboard
jobs produced 50-90 tabs a day for weeks. Fix: if a sign-in tab is already
open and unfinished, refuse to start a server or forward a call
(EntraLoginPending). One tab is the cue to sign in; a second one is noise.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agency_mcp as am

LOGIN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize?x=1"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(am, "_LOGIN_TAB_CACHE", {"at": 0.0, "url": None})
    monkeypatch.setattr(am, "_LOGIN_MARKER", tmp_path / "login-pending.json")
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
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: None)
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
    # Stub Chrome too: since entra_login_pending now consults
    # `_safari_login_tab() or _chrome_login_tab()`, leaving Chrome unstubbed
    # runs the real (slow, 10s-per-host) AppleScript on every call, which blows
    # the _LOGIN_TAB_TTL cache and makes this burst-caching assertion fail.
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: None)
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


def test_osascript_failure_is_unknown_not_no_tab(monkeypatch):
    # Automation permission denied / timeout -> unknown (was None, which read
    # as "no tab" and let Agency open another one; see the 2026-10-28 block).
    class R:
        returncode = 1
        stdout = ""
    monkeypatch.setattr(am, "_safari_running", lambda: True)
    monkeypatch.setattr(am.subprocess, "run", lambda *a, **k: R())
    assert am._safari_login_tab() is am._UNKNOWN


def test_unknown_scan_without_marker_does_not_block(monkeypatch):
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: am._UNKNOWN)
    monkeypatch.setattr(am, "get_server", lambda name: 1)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: {"ok": True})
    assert am.call_tool("mail", "ListMailFolders", {}) == {"ok": True}


def test_pending_error_is_runtimeerror_for_existing_callers():
    assert issubclass(am.EntraLoginPending, RuntimeError)


# --- Chrome (2026-10-18) -----------------------------------------------------
# AzureAuth opens the sign-in tab in the DEFAULT browser. On ix that is Chrome,
# and the guard only ever looked at Safari, so it never fired there: ~30
# tabs/day (janus-mobile on every phone page load + the 03:00 dream run)
# until the box OOMed and starved Excel.

def test_pending_tab_in_chrome_is_detected(monkeypatch):
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: LOGIN_URL)
    assert am.entra_login_pending(force=True) == LOGIN_URL


def test_call_tool_refuses_while_chrome_login_tab_open(monkeypatch):
    started, called = [], []
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: LOGIN_URL)
    monkeypatch.setattr(am, "get_server", lambda name: started.append(name) or 1)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: called.append(a) or {})
    with pytest.raises(am.EntraLoginPending):
        am.call_tool("calendar", "ListEvents", {})
    assert started == [] and called == []


def test_chrome_check_uses_chrome_applescript_and_never_launches_it(monkeypatch):
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)

        class R:
            returncode = 0
            stdout = LOGIN_URL + "\n" if cmd[0] == "osascript" else "123\n"
        return R()

    monkeypatch.setattr(am.subprocess, "run", fake_run)
    assert am._chrome_login_tab() == LOGIN_URL
    assert ran[0][:3] == ["pgrep", "-x", "Google Chrome"], "must check Chrome is running first"
    assert 'tell application "Google Chrome"' in ran[1][2]

    ran.clear()
    monkeypatch.setattr(am.subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd) or type("R", (), {"returncode": 1, "stdout": ""})())
    assert am._chrome_login_tab() is None
    assert all(c[0] == "pgrep" for c in ran), "Chrome not running -> no AppleScript (would launch it)"


# --- cron can't read Chrome tabs (2026-10-28) ---------------------------------
# Under cron (03:00 dream run, its outlook subagents) macOS denies osascript
# Apple Events access to Chrome, so every tab scan failed, read as "no tab",
# and Agency opened another sign-in tab every 15-30 min from 03:00 to 05:00
# (~26/day piling up in Chrome on ix). An unanswered sign-in now leaves a
# ledger marker that blocks further starts when the tabs can't be read.

def _cron_context(monkeypatch):
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: am._UNKNOWN)


def test_cron_timeout_on_signin_opens_only_one_tab(monkeypatch):
    _cron_context(monkeypatch)
    spawned = []
    monkeypatch.setattr(am, "_read_pidfile", lambda name: (None, None))
    monkeypatch.setattr(am, "_start_server",
                        lambda name: spawned.append(name) or (type("P", (), {"pid": 1})(), 7000))
    monkeypatch.setattr(am, "restart_server", lambda name: am._servers.pop(name, None))

    def waiting_on_signin(*a):
        raise am.AgencyTimeout("no result: Agency is waiting for a browser sign-in")
    monkeypatch.setattr(am, "_call_tool_once", waiting_on_signin)

    with pytest.raises(am.EntraLoginPending):
        am.call_tool("mail", "SearchMessagesQueryParameters", {})
    assert spawned == ["mail"], "the self-heal respawn must not open a second tab"

    # The next dream subagent 15 minutes later is refused before touching Agency.
    am._LOGIN_TAB_CACHE.update(at=0.0, url=None)
    with pytest.raises(am.EntraLoginPending):
        am.call_tool("calendar", "ListEvents", {})
    assert spawned == ["mail"]


def test_marker_expires_after_ttl(monkeypatch):
    _cron_context(monkeypatch)
    am._mark_login_pending("mail", "call timed out")
    assert am.entra_login_pending(force=True)
    now = am.time.time()
    monkeypatch.setattr(am.time, "time", lambda: now + am._LOGIN_MARKER_TTL + 1)
    assert am.entra_login_pending(force=True) is None


def test_visible_no_tab_clears_marker(monkeypatch):
    # Interactive context that CAN read Chrome sees the tab is gone (signed
    # in or closed): the ledger must not keep blocking.
    am._mark_login_pending("mail", "call timed out")
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: None)
    assert am.entra_login_pending(force=True) is None
    assert not am._LOGIN_MARKER.exists()


def test_successful_call_clears_marker(monkeypatch):
    am._mark_login_pending("mail", "call timed out")
    monkeypatch.setattr(am, "_safari_login_tab", lambda: None)
    monkeypatch.setattr(am, "_chrome_login_tab", lambda: LOGIN_URL)
    monkeypatch.setattr(am, "entra_login_pending", lambda force=False: None)
    monkeypatch.setattr(am, "get_server", lambda name: 1)
    monkeypatch.setattr(am, "_call_tool_once", lambda *a: {"ok": True})
    assert am.call_tool("mail", "ListMailFolders", {}) == {"ok": True}
    assert not am._LOGIN_MARKER.exists()


def test_failed_server_start_marks_pending(monkeypatch):
    # Agency exits before printing a port: its stdout is a pipe at EOF
    # (watchdog-fix reads the raw fd with select/os.read, not readline).
    r, w = os.pipe()
    os.close(w)
    class P:
        pid = 1
        stdout = type("S", (), {"fileno": lambda self: r, "readline": lambda self: ""})()
        def kill(self): pass
    monkeypatch.setattr(am.subprocess, "Popen", lambda *a, **k: P())
    t = iter(range(0, 1000, 31))
    monkeypatch.setattr(am.time, "time", lambda: next(t))
    with pytest.raises(RuntimeError):
        am._start_server("mail")
    assert am._LOGIN_MARKER.exists()
