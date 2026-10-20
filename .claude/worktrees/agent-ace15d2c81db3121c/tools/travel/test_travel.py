#!/usr/bin/env python3
"""Tests for /travel — the explicit DTD/Janus timezone override command.

Covers: city/IANA zone resolution, state file read/write, switch/home/status
output, and that a resync failure never blocks the TZ switch itself.
"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, travel_file):
    spec = importlib.util.spec_from_file_location("travel_t", HERE / "travel.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["travel_t"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TRAVEL_FILE", travel_file)
    monkeypatch.setattr(mod.daytime, "TRAVEL_FILE", travel_file)
    # Never actually shell out or signal a real janus process in tests.
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(mod, "_notify_janus", lambda: None)
    return mod


def test_resolve_zone_accepts_raw_iana(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "travel.json")
    assert mod.resolve_zone("Asia/Seoul") == "Asia/Seoul"


def test_resolve_zone_accepts_city_alias_case_insensitive(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "travel.json")
    assert mod.resolve_zone("Tokyo") == "Asia/Tokyo"
    assert mod.resolve_zone("tokyo") == "Asia/Tokyo"
    assert mod.resolve_zone("  TOKYO  ") == "Asia/Tokyo"


def test_resolve_zone_rejects_unknown(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "travel.json")
    with pytest.raises(ValueError, match="Narnia"):
        mod.resolve_zone("Narnia")


def test_switch_writes_active_tz_and_timestamp(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    mod = _load(monkeypatch, f)
    rc = mod.cmd_switch("Tokyo")
    assert rc == 0
    state = json.loads(f.read_text())
    assert state["active_tz"] == "Asia/Tokyo"
    assert "switched_at_utc" in state
    assert state["home_tz"] == str(mod.daytime.HOME_TZ)


def test_switch_bad_input_does_not_write_state(tmp_path, monkeypatch, capsys):
    f = tmp_path / "travel.json"
    mod = _load(monkeypatch, f)
    rc = mod.cmd_switch("Narnia")
    assert rc == 1
    assert not f.exists()
    assert "Narnia" in capsys.readouterr().err


def test_home_clears_state(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"active_tz": "Asia/Tokyo"}))
    mod = _load(monkeypatch, f)
    rc = mod.cmd_home()
    assert rc == 0
    assert not f.exists()


def test_home_is_a_noop_when_not_traveling(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"  # never created
    mod = _load(monkeypatch, f)
    rc = mod.cmd_home()
    assert rc == 0


def test_status_reflects_active_override(tmp_path, monkeypatch, capsys):
    f = tmp_path / "travel.json"
    mod = _load(monkeypatch, f)
    mod.cmd_switch("Asia/Tokyo")
    capsys.readouterr()
    mod.cmd_status()
    out = capsys.readouterr().out
    assert "traveling: Asia/Tokyo" in out


def test_status_reflects_no_override(tmp_path, monkeypatch, capsys):
    f = tmp_path / "travel.json"
    mod = _load(monkeypatch, f)
    mod.cmd_status()
    out = capsys.readouterr().out
    assert "not traveling" in out


def test_resync_step_failure_does_not_raise(tmp_path, monkeypatch):
    """A resync step (absorb-remote / refresh-cache) failing must not block
    the TZ switch, which has already been committed to disk by the time
    resync runs — resync is best-effort by design."""
    f = tmp_path / "travel.json"
    mod = _load(monkeypatch, f)

    def _boom(*a, **kw):
        raise RuntimeError("subprocess exploded")
    monkeypatch.setattr(mod.subprocess, "run", _boom)

    rc = mod.cmd_switch("Tokyo")
    assert rc == 0
    assert json.loads(f.read_text())["active_tz"] == "Asia/Tokyo"


def test_daytime_active_zone_honors_switch(tmp_path, monkeypatch):
    """End-to-end: after a switch, lib/daytime.py's active_zone() (the
    single source of truth every DTD/Janus TZ read goes through) resolves
    to the new zone."""
    from zoneinfo import ZoneInfo
    f = tmp_path / "travel.json"
    mod = _load(monkeypatch, f)
    mod.cmd_switch("Asia/Tokyo")
    assert mod.daytime.active_zone() == ZoneInfo("Asia/Tokyo")
    mod.cmd_home()
    assert mod.daytime.active_zone() != ZoneInfo("Asia/Tokyo")
