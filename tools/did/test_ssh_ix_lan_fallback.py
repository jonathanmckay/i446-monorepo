#!/usr/bin/env python3
"""Regression (2026-10-17, "tasks from dtd are not getting into neon"):
ix's Tailscale node key expired at 16:00 on 2026-10-16. Every Neon write from
Straylight rides `ssh ix` (did-fast -> lib/neon/excel.py -> excel-http on ix),
and `Host ix` resolved only to the tailnet name, so 14 hours of dtd
completions were lost while ix sat healthy on the LAN as ix.local.

The fix is an ssh-config Match block above `Host ix` whose exec probe
(scripts/ix-tailnet-down.sh) exits 0 when the tailnet path is down, so ssh
takes HostName ix.local instead. This test drives the same block from a
temporary config with the probe forced each way."""
import os
import subprocess
from pathlib import Path

MONO = Path(__file__).resolve().parents[2]
PROBE = MONO / "scripts" / "ix-tailnet-down.sh"

CONFIG = f'''Match host ix exec "{PROBE}"
    HostName ix.local

Host ix
    HostName ix.tail9c51d5.ts.net
    User mckay
'''


def _resolved_hostname(tmp_path: Path, force: str) -> str:
    cfg = tmp_path / "config"
    cfg.write_text(CONFIG)
    env = dict(os.environ, IX_TAILNET_FORCE=force)
    out = subprocess.run(["ssh", "-G", "-F", str(cfg), "ix"], capture_output=True, text=True, env=env).stdout
    return next(l.split()[1] for l in out.splitlines() if l.startswith("hostname "))


def test_probe_script_is_executable_and_honours_force():
    assert os.access(PROBE, os.X_OK), PROBE
    assert subprocess.run([str(PROBE)], env=dict(os.environ, IX_TAILNET_FORCE="down")).returncode == 0
    assert subprocess.run([str(PROBE)], env=dict(os.environ, IX_TAILNET_FORCE="up")).returncode == 1


def test_tailnet_down_falls_back_to_lan(tmp_path):
    assert _resolved_hostname(tmp_path, "down") == "ix.local"


def test_tailnet_up_keeps_tailnet_hostname(tmp_path):
    assert _resolved_hostname(tmp_path, "up") == "ix.tail9c51d5.ts.net"


def test_live_ssh_config_carries_the_fallback():
    """The block has to be in the real ~/.ssh/config to do anything; this
    guards against it being dropped on this machine."""
    cfg = Path.home() / ".ssh" / "config"
    if not cfg.exists():
        return
    text = cfg.read_text()
    assert "ix-tailnet-down.sh" in text and text.index("ix-tailnet-down.sh") < text.index("Host ix\n"), \
        "Match host ix ... ix-tailnet-down.sh must precede Host ix in ~/.ssh/config"
