"""Tests for open-form.sh: tier selection with stub cmux/tmux binaries."""
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).with_name("open-form.sh")


def _stub(bindir: Path, name: str, body: str) -> None:
    p = bindir / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, *, cmux=None, tmux=None, env_extra=None, cmd="python3 x.py '2026-10-30'; echo hi"):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "log"
    if cmux is not None:
        _stub(bindir, "cmux", f'echo "cmux $*" >> {log}\n{cmux}')
    if tmux is not None:
        _stub(bindir, "tmux", f'echo "tmux $*" >> {log}\n{tmux}')
    base = "/usr/bin:/bin"
    env = {"PATH": f"{bindir}:{base}", "HOME": str(tmp_path)}
    env.update(env_extra or {})
    p = subprocess.run(["bash", str(SCRIPT), "0s-1030", cmd], capture_output=True, text=True, env=env)
    return p, (log.read_text() if log.exists() else "")


TMUX_OK = '''case "$1" in
  display-message) echo cc-055349 ;;
  list-clients) echo "/dev/ttys001: cc-055349" ;;
esac'''
TMUX_NOCLIENT = '''case "$1" in
  display-message) echo cc-055349 ;;
  list-clients) ;;
esac'''
IN_TMUX = {"TMUX": "/tmp/tmux-501/default,1,0", "TMUX_PANE": "%3"}


def test_cmux_success(tmp_path):
    p, log = _run(tmp_path, cmux='[ "$1" = new-surface ] && echo "OK surface:6 pane:3 workspace:1"; exit 0')
    assert p.returncode == 0
    assert p.stdout.strip() == "OPENED cmux surface:6"
    assert "respawn-pane --surface surface:6 --command python3 x.py '2026-10-30'; echo hi" in log
    assert "focus-pane --pane pane:3" in log


def test_cmux_failure_falls_through_to_tmux(tmp_path):
    p, log = _run(tmp_path, cmux="exit 1", tmux=TMUX_OK, env_extra=IN_TMUX)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.strip() == "OPENED tmux cc-055349:0s-1030"
    assert "new-window -t cc-055349: -n 0s-1030" in log


def test_no_cmux_tmux_attached(tmp_path):
    p, log = _run(tmp_path, tmux=TMUX_OK, env_extra=IN_TMUX)
    assert p.returncode == 0
    assert "display-message -p -t %3 #S" in log
    # command is wrapped: cmux vars stripped, sh -c guards the whole string
    assert "env -u CMUX_SURFACE_ID" in log and "sh -c" in log


def test_tmux_without_attached_client_is_not_surfaced(tmp_path):
    p, _ = _run(tmp_path, tmux=TMUX_NOCLIENT, env_extra=IN_TMUX)
    assert p.returncode == 10
    assert p.stdout.startswith("NO_SURFACE")


def test_neither_exits_10(tmp_path):
    p, _ = _run(tmp_path, tmux=TMUX_OK)  # tmux installed but Claude not inside it
    assert p.returncode == 10


def test_forced_chat(tmp_path):
    p, log = _run(tmp_path, cmux="echo OK surface:1 pane:1", env_extra={"OPEN_FORM_MODE": "chat"})
    assert p.returncode == 10 and log == ""


def test_forced_tmux_skips_cmux(tmp_path):
    p, log = _run(tmp_path, cmux="echo OK surface:1 pane:1", tmux=TMUX_OK,
                  env_extra={**IN_TMUX, "OPEN_FORM_MODE": "tmux"})
    assert p.returncode == 0 and "cmux" not in log.replace("CMUX", "")


def test_dry_run_executes_nothing(tmp_path):
    p, log = _run(tmp_path, cmux="echo OK surface:1 pane:1", env_extra={"OPEN_FORM_DRY_RUN": "1"})
    assert p.returncode == 0
    assert "DRY: cmux respawn-pane" in p.stdout
    assert "respawn-pane" not in log


def test_wrapped_command_runs_with_quotes(tmp_path):
    """The tmux wrapper string must execute the original compound command intact."""
    out = tmp_path / "ran"
    real = f"echo \"it's\" > {out}; echo second >> {out}"
    p, log = _run(tmp_path, tmux=TMUX_OK, env_extra={**IN_TMUX, "OPEN_FORM_DRY_RUN": "1"}, cmd=real)
    assert p.returncode == 0
    line = next(l for l in p.stdout.splitlines() if "new-window" in l)
    # recover the last argv element from the DRY line and run it like tmux would
    wrapped = subprocess.run(["bash", "-c", f"set -- {line[len('DRY: '):]}; printf %s \"${{@: -1}}\""],
                             capture_output=True, text=True).stdout
    subprocess.run(["bash", "-c", wrapped], check=True, env={**os.environ, "CMUX_SURFACE_ID": "x"})
    assert out.read_text() == "it's\nsecond\n"


def test_usage(tmp_path):
    p = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
    assert p.returncode == 2
