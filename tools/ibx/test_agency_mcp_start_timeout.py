"""Regression (2026-10-26): _start_server looped on proc.stdout.readline(),
which blocks until Agency prints a line. With an expired Entra token Agency
waits ~30 min in interactive auth first, so the 30s deadline never fired and
dream-intake serialized four ~30-min starts (2h) every night."""
import os
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agency_mcp as am


def _fake_bin(tmp_path, body):
    p = tmp_path / "agency"
    p.write_text("#!/bin/bash\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_silent_server_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "AGENCY_BIN", _fake_bin(tmp_path, "sleep 60"))
    monkeypatch.setattr(am, "_PIDFILE_DIR", tmp_path / "pids")
    t0 = time.time()
    try:
        am._start_server("mail", timeout=1)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "no port" in str(e)
    assert time.time() - t0 < 4


def test_port_parsed_after_noise(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "AGENCY_BIN", _fake_bin(
        tmp_path, "echo starting; sleep 0.2; printf '5432'; sleep 0.1; echo; sleep 30"))
    monkeypatch.setattr(am, "_PIDFILE_DIR", tmp_path / "pids")
    proc, port = am._start_server("mail", timeout=5)
    try:
        assert port == 5432
    finally:
        proc.kill()


def test_early_exit_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "AGENCY_BIN", _fake_bin(tmp_path, "echo 'auth failed'; exit 1"))
    monkeypatch.setattr(am, "_PIDFILE_DIR", tmp_path / "pids")
    t0 = time.time()
    try:
        am._start_server("mail", timeout=10)
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass
    assert time.time() - t0 < 3
