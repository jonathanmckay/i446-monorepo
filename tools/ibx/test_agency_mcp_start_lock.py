"""v175 (2026-11-01): get_server() held the module _lock across _start_server,
so a calendar start parked in a 15-min Entra sign-in blocked the mail start
behind it (mail never even spawned on 11-01). Starts now hold a per-name lock.
Also: AGENCY_UNATTENDED=1 (dream-intake) must not write login-pending.json,
which refused every Agency caller on ix for 3 h after the 03:30 timeout."""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agency_mcp as am


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "_LOGIN_MARKER", tmp_path / "login-pending.json")
    monkeypatch.setattr(am, "_PIDFILE_DIR", tmp_path / "pids")
    monkeypatch.setattr(am, "_refuse_if_login_pending", lambda why: None)
    monkeypatch.setattr(am, "_servers", {})
    monkeypatch.setattr(am, "_start_locks", {})
    # Local-server path; refused on cache-only hosts (ix) since 216fb4f3.
    monkeypatch.setenv("AGENCY_ALLOW_LOCAL", "1")


def test_hung_start_does_not_block_other_server(monkeypatch):
    release = threading.Event()
    started = threading.Event()

    class P:
        pid = 99999

        def poll(self):
            return None

    def fake_start(name, timeout=None):
        if name == "calendar":
            started.set()
            release.wait(10)
            raise RuntimeError("no port")
        return P(), 4242

    monkeypatch.setattr(am, "_start_server", fake_start)
    t = threading.Thread(target=lambda: pytest.raises(RuntimeError, am.get_server, "calendar"))
    t.start()
    assert started.wait(2)
    t0 = time.time()
    try:
        assert am.get_server("mail") == 4242
        assert time.time() - t0 < 1, "mail start queued behind the hung calendar start"
    finally:
        release.set()
        t.join(5)


def test_same_name_starts_once(monkeypatch):
    calls = []

    class P:
        pid = 99998

        def poll(self):
            return None

    def fake_start(name, timeout=None):
        calls.append(name)
        time.sleep(0.3)
        return P(), 4343

    monkeypatch.setattr(am, "_start_server", fake_start)
    monkeypatch.setattr(am, "_port_responsive", lambda port: True)
    ts = [threading.Thread(target=am.get_server, args=("mail",)) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert calls == ["mail"]


def test_unattended_does_not_write_marker(monkeypatch):
    monkeypatch.setenv("AGENCY_UNATTENDED", "1")
    am._mark_login_pending("calendar", "call timed out")
    assert not am._LOGIN_MARKER.exists()


def test_attended_still_writes_marker(monkeypatch):
    monkeypatch.delenv("AGENCY_UNATTENDED", raising=False)
    am._mark_login_pending("calendar", "call timed out")
    assert am._LOGIN_MARKER.exists()


def test_unattended_start_timeout_leaves_no_marker(tmp_path, monkeypatch):
    import stat
    p = tmp_path / "agency"
    p.write_text("#!/bin/bash\nsleep 60\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(am, "AGENCY_BIN", str(p))
    monkeypatch.setenv("AGENCY_UNATTENDED", "1")
    with pytest.raises(RuntimeError):
        am._start_server("calendar", timeout=1)
    assert not am._LOGIN_MARKER.exists()
