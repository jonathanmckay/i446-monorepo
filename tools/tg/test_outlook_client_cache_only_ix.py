"""Regression (2026-10-18): janus-mobile on ix fetched Outlook via Agency on
every phone page load because the launchd plist never set
JANUS_OUTLOOK_CACHE_ONLY=1; each fetch spawned an Agency calendar server that
opened a Chrome sign-in tab and waited 15 min. ix must be cache-only by
hostname, not only by an env var a launcher can forget."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import outlook_client as oc


def test_ix_is_cache_only_without_env(monkeypatch):
    monkeypatch.delenv("JANUS_OUTLOOK_CACHE_ONLY", raising=False)
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "Ix.local")
    assert oc.cache_only() is True


def test_env_flag_still_forces_cache_only(monkeypatch):
    monkeypatch.setenv("JANUS_OUTLOOK_CACHE_ONLY", "1")
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "straylight")
    assert oc.cache_only() is True


def test_other_hosts_fetch(monkeypatch):
    monkeypatch.delenv("JANUS_OUTLOOK_CACHE_ONLY", raising=False)
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "straylight")
    assert oc.cache_only() is False


def test_list_events_never_calls_agency_on_ix(monkeypatch, tmp_path):
    import datetime as dt
    monkeypatch.delenv("JANUS_OUTLOOK_CACHE_ONLY", raising=False)
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "ix")
    monkeypatch.setattr(oc, "CACHE_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(oc, "mcp", type("M", (), {
        "call_tool": staticmethod(lambda *a, **k: calls.append(a) or {})})())
    day = dt.datetime(2026, 10, 18, 0, 0)
    assert oc.list_events(day, day + dt.timedelta(days=1)) == []
    assert calls == [], "ix must read the pushed cache, never fetch from Agency"
