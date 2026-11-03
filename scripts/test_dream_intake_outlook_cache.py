"""v175 (2026-11-01): on cache-only hosts (ix) dream-intake must read the Outlook
calendar from straylight's pushed ~/.cache/janus/outlook-YYYY-MM-DD.json and
never touch Agency. Before this, outlook_calendar spawned `agency mcp calendar`
on ix, which parked 15 min in interactive Entra auth (keychain -61) and held
the intake for 31 min. Also covers the FDA diagnostic line.

Run: python3 -m pytest scripts/test_dream_intake_outlook_cache.py
"""
import datetime
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "dream_intake_cache", Path(__file__).with_name("dream-intake.py"))
_di = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_di)


# Outlook-cache calendar tests moved to test_dream_intake.py with the
# meta-mail-cache implementation (dream/2026.11.03/train); FDA tests stay.


def test_fda_binary_prefers_cron():
    chain = [(10, "/usr/bin/python3"), (9, "/bin/bash"), (8, "/usr/sbin/cron")]
    assert _di.fda_binary(chain) == "/usr/sbin/cron"


def test_fda_binary_terminal_app():
    chain = [(10, "/usr/bin/python3"), (9, "-zsh"),
             (8, "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")]
    assert _di.fda_binary(chain) == "/System/Applications/Utilities/Terminal.app"


def test_fda_diagnostic_one_line(monkeypatch):
    monkeypatch.setattr(_di, "fda_binary", lambda chain=None: "/usr/sbin/cron")
    results = {
        "browser_history": {"status": "ok", "data": {"error": "authorization denied: [Errno 1]"}},
        "screen_time": {"status": "ok", "data": {"error": "SIP-blocked or DB locked: authorization denied"}},
        "imessage_recent": {"status": "error", "data": None,
                            "error": "PermissionError: [Errno 1] Operation not permitted"},
        "gmail_inbox": {"status": "ok", "data": {}},
    }
    line = _di.fda_diagnostic(results)
    assert "\n" not in line
    assert "browser_history, screen_time, imessage_recent" in line
    assert "/usr/sbin/cron" in line


def test_fda_diagnostic_none_when_clean():
    assert _di.fda_diagnostic({"browser_history": {"status": "ok", "data": {"unique_urls": 3}}}) is None


def test_browser_history_permission_error_is_data(monkeypatch, tmp_path):
    home = tmp_path
    (home / "Library" / "Safari").mkdir(parents=True)
    (home / "Library" / "Safari" / "History.db").write_text("x")
    monkeypatch.setattr(_di.Path, "home", classmethod(lambda cls: home))

    def deny(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(_di.shutil, "copy2", deny)
    out = _di._browser_history()
    assert _di._is_fda_denial(out["error"])
