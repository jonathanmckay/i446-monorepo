"""Tests for lib/daytime.py — the shared TZ-resolution module.

Covers: default (no /travel override) follows system local time; a valid
override wins; a malformed/missing override falls back cleanly; --export
emits shell-consumable KEY=VALUE lines.
"""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent


def _load(monkeypatch, travel_file):
    spec = importlib.util.spec_from_file_location("daytime_t", HERE / "daytime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["daytime_t"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TRAVEL_FILE", travel_file)
    return mod


def test_no_override_file_uses_system_local(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "travel.json")  # does not exist
    assert mod.active_zone() == dt.datetime.now().astimezone().tzinfo
    assert not mod.is_traveling()


def test_valid_override_wins(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"active_tz": "Asia/Tokyo"}))
    mod = _load(monkeypatch, f)
    assert mod.active_zone() == ZoneInfo("Asia/Tokyo")
    assert mod.is_traveling()
    assert mod.today() == dt.datetime.now(ZoneInfo("Asia/Tokyo")).date()


def test_malformed_json_falls_back_to_system_local(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text("{not valid json")
    mod = _load(monkeypatch, f)
    assert mod.active_zone() == dt.datetime.now().astimezone().tzinfo
    assert not mod.is_traveling()


def test_invalid_tz_name_falls_back_to_system_local(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"active_tz": "Not/A_Real_Zone"}))
    mod = _load(monkeypatch, f)
    assert mod.active_zone() == dt.datetime.now().astimezone().tzinfo


def test_override_with_no_active_tz_key_is_not_traveling(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"home_tz": "America/Los_Angeles"}))
    mod = _load(monkeypatch, f)
    assert not mod.is_traveling()
    assert mod.active_zone() == dt.datetime.now().astimezone().tzinfo


def test_local_now_and_today_are_consistent(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"active_tz": "Pacific/Auckland"}))
    mod = _load(monkeypatch, f)
    assert mod.local_now().date() == mod.today()
    assert mod.today_iso() == mod.today().isoformat()


def test_home_now_ignores_override(tmp_path, monkeypatch):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"active_tz": "Asia/Tokyo"}))
    mod = _load(monkeypatch, f)
    assert mod.home_now().tzinfo == mod.HOME_TZ


def test_export_emits_expected_keys(tmp_path, monkeypatch, capsys):
    f = tmp_path / "travel.json"
    f.write_text(json.dumps({"active_tz": "Asia/Tokyo"}))
    mod = _load(monkeypatch, f)
    out = mod._export()
    for key in ("LOCAL_TODAY=", "LOCAL_HOUR=", "LOCAL_TIME=", "ACTIVE_TZ=Asia/Tokyo", "TRAVELING=1"):
        assert key in out, out


def test_export_traveling_flag_is_0_without_override(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path / "travel.json")
    assert "TRAVELING=0" in mod._export()
