"""Tests for backup-health.py checks and the backup-health-alert.sh hook."""

import importlib.util
import json
import os
import subprocess
import time

import pytest

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("backup_health", os.path.join(HERE, "backup-health.py"))
bh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bh)

ALERT = os.path.join(HERE, "backup-health-alert.sh")


def _touch(path, age_hours=0, size=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)
    t = time.time() - age_hours * 3600
    os.utime(path, (t, t))


def test_onedrive_fresh_snapshot_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "ONEDRIVE_BACKUPS", str(tmp_path))
    _touch(str(tmp_path / "vault-backup-20260728.tar.zst"), age_hours=2, size=2_000_000)
    monkeypatch.setattr(os.path, "getsize", lambda p: 2_000_000_000)
    assert bh.check_onedrive() is None


def test_onedrive_old_snapshot_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "ONEDRIVE_BACKUPS", str(tmp_path))
    _touch(str(tmp_path / "vault-backup-20260601.tar.zst"), age_hours=24 * 10, size=10)
    err = bh.check_onedrive()
    assert err and "days old" in err


def test_onedrive_missing_dir_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "ONEDRIVE_BACKUPS", str(tmp_path / "nope"))
    err = bh.check_onedrive()
    assert err and "no snapshots" in err


def test_onedrive_stale_partial_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(bh, "ONEDRIVE_BACKUPS", str(tmp_path))
    _touch(str(tmp_path / "vault-backup-20260728.tar.zst"), age_hours=2)
    monkeypatch.setattr(os.path, "getsize", lambda p: 2_000_000_000)
    _touch(str(tmp_path / "vault-backup-20260720.tar.zst.partial"), age_hours=72)
    err = bh.check_onedrive()
    assert err and "partial" in err


def _run_alert(env_home, health, stamp):
    env = dict(os.environ, BACKUP_HEALTH_JSON=str(health), BACKUP_ALERT_STAMP=str(stamp))
    r = subprocess.run(["bash", ALERT], capture_output=True, text=True, env=env, timeout=15)
    return r.stdout.strip()


def test_alert_silent_when_healthy(tmp_path):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"ok": True, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "failures": []}))
    out = _run_alert(tmp_path, health, tmp_path / "stamp")
    assert out == ""


def test_alert_fires_on_failure(tmp_path):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"ok": False, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  "failures": ["onedrive: newest snapshot is 12.0 days old"]}))
    out = _run_alert(tmp_path, health, tmp_path / "stamp")
    assert "BACKUP ALERT" in out and "12.0 days" in out
    assert json.loads(out)["context"]  # valid hook context JSON


def test_alert_fires_on_stale_verdict(tmp_path):
    health = tmp_path / "health.json"
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 60 * 3600))
    health.write_text(json.dumps({"ok": True, "ts": old, "failures": []}))
    out = _run_alert(tmp_path, health, tmp_path / "stamp")
    assert "stale" in out


def test_alert_rate_limited(tmp_path):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"ok": False, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  "failures": ["x"]}))
    stamp = tmp_path / "stamp"
    out1 = _run_alert(tmp_path, health, stamp)
    out2 = _run_alert(tmp_path, health, stamp)
    assert "BACKUP ALERT" in out1 and out2 == ""
