"""Regression test: Outlook intake sources skip fast on ix with no remote tunnel.

v160-v166: on ix the headless dream run hit MSAL keychain -61, Agency fell
back to interactive auth nobody completed, and outlook_inbox/outlook_calendar
each burned the 30s source timeout. With no AGENCY_REMOTE_<NAME>_PORT set they
now return {"status": "skipped", ...} without touching agency_mcp.

Run: python3 -m pytest scripts/test_dream_intake_outlook_skip.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "dream_intake", Path(__file__).with_name("dream-intake.py"))
_di = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_di)


@pytest.fixture
def no_agency(monkeypatch):
    """Make any agency_mcp import explode, proving the skip never reaches it."""
    monkeypatch.setitem(sys.modules, "agency_mcp", None)
    for k in ("AGENCY_REMOTE_MAIL_PORT", "AGENCY_REMOTE_CALENDAR_PORT"):
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize("fn,env", [
    ("_outlook_inbox", "AGENCY_REMOTE_MAIL_PORT"),
    ("_outlook_calendar", "AGENCY_REMOTE_CALENDAR_PORT"),
])
def test_ix_without_remote_port_skips(monkeypatch, no_agency, fn, env):
    monkeypatch.setattr(_di, "_on_ix", lambda: True)
    out = getattr(_di, fn)()
    assert out["status"] == "skipped"
    assert env in out["reason"]


def test_skip_surfaces_as_top_level_status(monkeypatch, no_agency):
    monkeypatch.setattr(_di, "_on_ix", lambda: True)
    res = _di.run_sources(selected=["outlook_inbox", "outlook_calendar"], timeout=5)
    assert {r["status"] for r in res.values()} == {"skipped"}


def test_remote_port_set_on_ix_does_not_skip(monkeypatch):
    monkeypatch.setattr(_di, "_on_ix", lambda: True)
    monkeypatch.setenv("AGENCY_REMOTE_CALENDAR_PORT", "7001")
    assert _di._outlook_skip("calendar") is None


def test_off_ix_does_not_skip(monkeypatch):
    monkeypatch.setattr(_di, "_on_ix", lambda: False)
    monkeypatch.delenv("AGENCY_REMOTE_MAIL_PORT", raising=False)
    assert _di._outlook_skip("mail") is None
