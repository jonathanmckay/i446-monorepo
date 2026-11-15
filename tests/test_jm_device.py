"""jm-device.sh: the SessionStart line that tells the model which device JM is at.

Bug 2026-11-15: a session on Ix answered "it is in dtd" from Ix's dtd while JM
was looking at Straylight's. The hook must name the attached device, not the
session host."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "jm-device.sh"
STATUS = """100.114.46.109  ix                mckay@  macOS    -
100.110.77.24   fuchikoma         mckay@  android  offline
100.71.83.90    straylight-refit  mckay@  macOS    active; direct
"""


def _run(tmp_path: Path, ssh_connection: str | None, host: str = "ix") -> str:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".host-name").write_text(host + "\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "tailscale"
    fake.write_text("#!/bin/sh\ncat <<'EOF'\n" + STATUS + "EOF\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin"}   # no TMUX: the env var is the source
    if ssh_connection is not None:
        env["SSH_CONNECTION"] = ssh_connection
    out = subprocess.run(["/bin/zsh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_names_the_attached_device_not_the_session_host(tmp_path):
    line = _run(tmp_path, "100.71.83.90 60276 100.114.46.109 22")
    assert line.startswith("[device] JM is at straylight-refit (macOS)")
    assert "runs on ix" in line and "not ix" in line


def test_no_connection_means_jm_is_at_the_session_host(tmp_path):
    assert _run(tmp_path, None) == "[device] JM is at this machine (ix); the session runs here too."


def test_unknown_address_is_reported_as_the_address(tmp_path):
    line = _run(tmp_path, "100.99.99.99 1 100.114.46.109 22")
    assert line.startswith("[device] JM is at 100.99.99.99,")


def test_connection_from_the_host_itself_is_local(tmp_path):
    assert "at this machine (ix)" in _run(tmp_path, "100.114.46.109 1 100.114.46.109 22")
