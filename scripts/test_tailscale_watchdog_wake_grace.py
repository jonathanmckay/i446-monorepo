#!/usr/bin/env python3
"""tailscale-watchdog.sh wake grace (2026-11-08): the first failed probe after
Straylight sleeps is Tailscale still reconnecting, not Ix down. All three
ix_unreachable alerts (11-02, 11-05, 11-08) were that, so inside the grace
window the watchdog remediates but does not alert; awake outages still do."""
import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "tailscale-watchdog.sh"


def _run(tmp_path, last_tick_ago, woke_ago=None, internet_up=True):
    home = tmp_path / "home"
    (home / "i446-monorepo" / "scripts").mkdir(parents=True)
    state = tmp_path / "state" / "jm"
    state.mkdir(parents=True)
    now = int(time.time())
    (state / "tailscale-watchdog.lasttick").write_text(str(now - last_tick_ago))
    if woke_ago is not None:
        (state / "tailscale-watchdog.woke").write_text(str(now - woke_ago))
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    stubs = {"ts": 'echo "100.64.0.1 ix mckay@ macOS -"',   # status looks fine, so FIX=probe_failed
             "ssh": "exit 255", "osascript": "exit 0", "open": "exit 0", "sleep": "exit 0",
             "ping": "exit 0" if internet_up else "exit 2"}
    for name, body in stubs.items():
        f = bin_ / name
        f.write_text(f"#!/bin/sh\n{body}\n")
        f.chmod(0o755)
    env = dict(os.environ, HOME=str(home), XDG_STATE_HOME=str(tmp_path / "state"),
               TAILSCALE_BIN=str(bin_ / "ts"), NET_PROBE_BIN=str(bin_ / "ping"), PATH=f"{bin_}:/usr/bin:/bin")
    rc = subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30).returncode
    alerts = home / "vault" / "z_ibx" / "alerts.jsonl"
    log = (home / "i446-monorepo" / "scripts" / ".tailscale-watchdog-health.log").read_text()
    return rc, alerts.read_text() if alerts.exists() else "", log


def test_first_failed_run_after_sleep_does_not_alert(tmp_path):
    rc, alerts, log = _run(tmp_path, last_tick_ago=7 * 3600)
    assert rc == 1 and alerts == ""
    assert "in wake grace, not alerting" in log


def test_still_in_grace_on_the_next_tick(tmp_path):
    rc, alerts, log = _run(tmp_path, last_tick_ago=300, woke_ago=600)
    assert alerts == "" and "in wake grace" in log


def test_outage_while_awake_still_alerts(tmp_path):
    rc, alerts, log = _run(tmp_path, last_tick_ago=300, woke_ago=3 * 3600)
    assert rc == 1 and "ix_unreachable_persisted" in alerts


def test_internet_down_is_not_a_tailscale_fault(tmp_path):
    """2026-11-09: hotspot dropped for 20 min; the watchdog restarted Tailscale twice for it."""
    rc, alerts, log = _run(tmp_path, last_tick_ago=300, woke_ago=3 * 3600, internet_up=False)
    assert rc == 1 and alerts == ""
    assert "internet down" in log and "detected" not in log
