#!/usr/bin/env python3
"""net-probe.sh: one TSV row per tick; alert only after DEGRADED_TICKS consecutive bad
ticks while the link is up; recovery resets. Network is stubbed (ping/curl)."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "net-probe.sh"

PING_OK = 'echo "5 packets transmitted, 5 packets received, 0.0% packet loss"; echo "round-trip min/avg/max/stddev = 20.0/31.5/50.0/9.0 ms"'
PING_BAD = 'echo "5 packets transmitted, 1 packets received, 80.0% packet loss"; echo "round-trip min/avg/max/stddev = 900/900/900/0 ms"'


def _env(tmp_path, ping_body, curl_body='printf 0.25'):
    home = tmp_path / "home"; home.mkdir(exist_ok=True)
    bin_ = tmp_path / "bin"; bin_.mkdir(exist_ok=True)
    stubs = {"ping": ping_body, "curl": curl_body, "osascript": "exit 0",
             "ipconfig": 'echo "  SSID : stubnet"', "netstat": 'echo "default 10.0.0.1 UGScg en0"'}
    for name, body in stubs.items():
        f = bin_ / name; f.write_text(f"#!/bin/sh\n{body}\n"); f.chmod(0o755)
    return dict(os.environ, HOME=str(home), XDG_STATE_HOME=str(tmp_path / "state"),
                NET_PROBE_PING=str(bin_ / "ping"), NET_PROBE_LOG=str(tmp_path / "net.log"),
                PATH=f"{bin_}:/usr/bin:/bin")


def _tick(env):
    r = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    alerts = Path(env["HOME"]) / "vault" / "z_ibx" / "alerts.jsonl"
    return Path(env["NET_PROBE_LOG"]).read_text(), alerts.read_text() if alerts.exists() else ""


def test_ok_tick_logs_a_row_and_no_alert(tmp_path):
    log, alerts = _tick(_env(tmp_path, PING_OK))
    rows = log.strip().splitlines()
    assert rows[0].startswith("ts\tssid") and len(rows) == 2
    assert rows[1].split("\t")[1] == "stubnet" and rows[1].endswith("\tok") and alerts == ""


def test_alert_only_after_three_consecutive_degraded_ticks(tmp_path):
    env = _env(tmp_path, PING_BAD)
    _, a1 = _tick(env); _, a2 = _tick(env); log, a3 = _tick(env)
    assert a1 == "" and a2 == ""
    assert "link_degraded" in a3 and a3.count("\n") == 1
    assert log.count("\tdegraded") == 3
    _, a4 = _tick(env)                       # throttled: still one alert
    assert a4.count("\n") == 1


def test_recovery_resets_the_counter(tmp_path):
    env = _env(tmp_path, PING_BAD)
    _tick(env); _tick(env)
    _tick(_env(tmp_path, PING_OK))           # same tmp_path => same state dir
    _, alerts = _tick(_env(tmp_path, PING_BAD))
    assert alerts == ""


def test_https_failure_alone_counts_as_degraded(tmp_path):
    log, _ = _tick(_env(tmp_path, PING_OK, curl_body="exit 28"))
    assert log.strip().splitlines()[-1].endswith("\tdegraded")
