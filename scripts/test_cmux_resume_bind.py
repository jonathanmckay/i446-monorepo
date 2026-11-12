"""scripts/cmux-resume-bind.sh: silent no-op outside a cmux surface; inside one it calls
`cmux surface resume set|clear` with the documented flags and never fails the caller."""
import os
import stat
import subprocess
from pathlib import Path

HELPER = Path(__file__).with_name("cmux-resume-bind.sh")


def _run(env, *args):
    return subprocess.run(["bash", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=10)


def _fake_cmux(tmp_path):
    fake = tmp_path / "bin" / "cmux"
    fake.parent.mkdir()
    fake.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$FAKE_LOG"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return fake


def test_noop_without_surface(tmp_path):
    env = {"PATH": f"{_fake_cmux(tmp_path).parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "FAKE_LOG": str(tmp_path / "log")}
    r = _run(env, "set", "dtd", "zsh -ic dtd")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert not (tmp_path / "log").exists()


def test_set_and_clear_inside_surface(tmp_path):
    log = tmp_path / "log"
    env = {"PATH": f"{_fake_cmux(tmp_path).parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "FAKE_LOG": str(log), "CMUX_SURFACE_ID": "00000000-0000-0000-0000-000000000000"}
    r = _run(env, "set", "dtd", "zsh -ic dtd")
    assert r.returncode == 0
    assert log.read_text().splitlines() == ["surface", "resume", "set", "--kind", "tmux",
                                            "--checkpoint", "dtd", "--shell", "zsh -ic dtd"]
    r = _run(env, "clear", "cc-taxes")
    assert r.returncode == 0
    assert log.read_text().splitlines() == ["surface", "resume", "clear", "--checkpoint", "cc-taxes"]


def test_failing_cmux_is_logged_not_raised(tmp_path):
    fake = _fake_cmux(tmp_path)
    fake.write_text('#!/bin/bash\necho boom >&2; exit 7\n')
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "CMUX_SURFACE_ID": "00000000-0000-0000-0000-000000000000"}
    r = _run(env, "set", "janus", "zsh -ic janus")
    assert r.returncode == 0
    assert "rc 7: boom" in (tmp_path / ".local/state/jm/cmux-resume-bind.log").read_text()
