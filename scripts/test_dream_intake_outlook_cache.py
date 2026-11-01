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


def _ev(subject, day, hh, tz="Pacific Standard Time"):
    return {"subject": subject, "start": f"{day}T{hh:02d}:00:00.0000000",
            "end": f"{day}T{hh:02d}:30:00.0000000", "start_tz": tz, "end_tz": tz,
            "is_all_day": False}


@pytest.fixture
def ix_cache(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "agency_mcp", None)  # any Agency use explodes
    monkeypatch.setattr(_di, "_on_ix", lambda: True)
    monkeypatch.setattr(_di, "OUTLOOK_CACHE_DIR", tmp_path)
    monkeypatch.delenv("AGENCY_REMOTE_CALENDAR_PORT", raising=False)
    return tmp_path


def test_calendar_reads_cache_without_agency(ix_cache):
    today = datetime.date.today()
    tomorrow = today + datetime.timedelta(days=1)
    (ix_cache / f"outlook-{today}.json").write_text(json.dumps([_ev("BL:JM 1:1", today, 13)]))
    (ix_cache / f"outlook-{tomorrow}.json").write_text(
        json.dumps([_ev("ES:JM 1:1", tomorrow, 14), _ev("BL:JM 1:1", today, 13)]))
    t0 = time.time()
    out = _di._outlook_calendar()
    assert time.time() - t0 < 2
    assert "status" not in out
    assert [e["subject"] for e in out["events"]] == ["BL:JM 1:1", "ES:JM 1:1"]  # de-duped, sorted
    assert out["source"].startswith("cache:")
    assert out["missing_days"] == [str(today - datetime.timedelta(days=1))]
    assert out["cache_age_min"] < 1 and not out.get("stale")


def test_remote_calendar_port_still_uses_cache_on_ix(ix_cache, monkeypatch):
    monkeypatch.setenv("AGENCY_REMOTE_CALENDAR_PORT", "7001")
    (ix_cache / f"outlook-{datetime.date.today()}.json").write_text("[]")
    out = _di._outlook_calendar()
    assert out["count"] == 0 and "status" not in out


def test_stale_cache_flagged(ix_cache):
    f = ix_cache / f"outlook-{datetime.date.today()}.json"
    f.write_text(json.dumps([_ev("x", datetime.date.today(), 9)]))
    old = time.time() - 3 * 3600
    os.utime(f, (old, old))
    out = _di._outlook_calendar()
    assert out["stale"] is True and out["cache_age_min"] >= 179


def test_run_sources_calendar_ok_on_ix(ix_cache):
    (ix_cache / f"outlook-{datetime.date.today()}.json").write_text(
        json.dumps([_ev("x", datetime.date.today(), 9)]))
    res = _di.run_sources(selected=["outlook_calendar", "outlook_inbox"], timeout=5)
    assert res["outlook_calendar"]["status"] == "ok"
    assert res["outlook_inbox"]["status"] == "skipped"


# --- FDA diagnostic -------------------------------------------------------

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
