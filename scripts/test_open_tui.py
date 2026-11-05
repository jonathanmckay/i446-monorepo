#!/usr/bin/env python3
"""open-tui.sh (2026-11-05): survey skills gave up on Ix because they spoke cmux
only. Stub cmux/tmux/osascript record their argv; real zsh runs the launcher."""
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPT = HERE / "open-tui.sh"
SKILLS = HERE.parent / "skills" / "claude-skills"
CMD = 'python3 ~/x.py "a b" it\'s'


def _run(tmp_path, stubs, *args, env=None):
    log = tmp_path / "log"
    for name, body in stubs.items():
        f = tmp_path / name
        f.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{log}"\n{body}\n')
        f.chmod(0o755)
    e = {k: v for k, v in os.environ.items() if k not in ("TMUX", "SSH_CONNECTION")}
    e.update(env or {})
    # ZDOTDIR: ~/.zshenv would put Homebrew's real tmux ahead of the stub.
    e.update(ZDOTDIR=str(tmp_path), PATH=f"{tmp_path}:/usr/bin:/bin")
    p = subprocess.run([str(SCRIPT), *args], env=e, capture_output=True, text=True)
    return p, (log.read_text() if log.exists() else "")


@pytest.fixture
def no_real_mux(tmp_path):
    return tmp_path


def test_cmux_wins_and_runs_all_three_steps(no_real_mux):
    p, log = _run(no_real_mux, {"cmux": 'echo "OK surface:6 pane:3 workspace:1"', "tmux": ""},
                  "0s", CMD, env={"TMUX": "/s,1,0"})
    assert p.returncode == 0 and "cmux tab" in p.stdout
    assert "cmux new-surface --type terminal" in log
    assert f"cmux respawn-pane --surface surface:6 --command {CMD}" in log
    assert "cmux focus-pane --pane pane:3" in log
    assert "tmux " not in log


def test_tmux_opens_a_window_on_ix(no_real_mux):
    p, log = _run(no_real_mux, {"tmux": ""}, "1hcme", CMD, env={"TMUX": "/s,1,0"})
    assert p.returncode == 0 and "tmux window" in p.stdout
    assert log.startswith("tmux new-window -n 1hcme zsh -lic ")
    assert CMD in log


def test_tmux_failure_falls_through_to_the_manual_line(no_real_mux):
    p, _ = _run(no_real_mux, {"tmux": "exit 1"}, "0s", CMD, env={"TMUX": "/s,1,0"})
    assert p.returncode == 3 and f"run: ! {CMD}" in p.stdout


def test_nowhere_to_open_says_what_to_run(no_real_mux):
    p, _ = _run(no_real_mux, {}, "0s", CMD, env={"SSH_CONNECTION": "1 2 3 4"})
    assert p.returncode == 3 and f"run: ! {CMD}" in p.stdout


def _real_window(tmp_path, *args):
    """Launcher against a private tmux server; returns whether the pane survived."""
    sock = Path(tempfile.mkdtemp(dir="/tmp")) / "s"   # tmux caps socket paths near 100 chars
    subprocess.run(["tmux", "-S", str(sock), "new-session", "-d", "-s", "t", "sleep 30"], check=True)
    try:
        tmux_env = subprocess.run(["tmux", "-S", str(sock), "display", "-p", "#{socket_path},#{pid},0"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        p = subprocess.run([str(SCRIPT), *args], capture_output=True, text=True,
                           env={**os.environ, "TMUX": tmux_env, "PATH": os.environ["PATH"]})
        assert p.returncode == 0, p.stderr
        for _ in range(60):
            subprocess.run(["sleep", "0.1"])
            names = subprocess.run(["tmux", "-S", str(sock), "list-windows", "-t", "t", "-F", "#W"],
                                   capture_output=True, text=True).stdout.split()
            if "form" not in names or (tmp_path / "ran").exists():
                break
        subprocess.run(["sleep", "0.5"])
        return "form" in subprocess.run(["tmux", "-S", str(sock), "list-windows", "-t", "t", "-F", "#W"],
                                        capture_output=True, text=True).stdout.split()
    finally:
        subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)


@pytest.mark.skipif(subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
                    reason="no tmux")
def test_real_tmux_window_runs_quoted_command_and_closes(tmp_path):
    out = tmp_path / "ran"
    assert not _real_window(tmp_path, "form", f"print -r -- \"a b\" 'c' > {out}")
    assert out.read_text() == "a b c\n"


@pytest.mark.skipif(subprocess.run(["which", "tmux"], capture_output=True).returncode != 0,
                    reason="no tmux")
def test_real_tmux_holds_a_failed_form_unless_told_not_to(tmp_path):
    assert _real_window(tmp_path, "form", f"touch {tmp_path}/ran; false")
    (tmp_path / "ran").unlink()
    assert not _real_window(tmp_path, "--no-hold", "form", f"touch {tmp_path}/ran; false")


@pytest.mark.parametrize("skill", ["0s", "1s", "1hcme", "xk887", "1f693", "moralaudit"])
def test_survey_skills_launch_through_open_tui(skill):
    text = (SKILLS / skill / "SKILL.md").read_text()
    assert "scripts/open-tui.sh" in text
    assert not re.search(r"cmux (new-surface|respawn-pane|focus-pane)", text)
