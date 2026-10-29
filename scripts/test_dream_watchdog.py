"""Regression (2026-10-26): the dream-launch.sh stall watchdog killed v166,
v167 and v168 ~15-20 min after claude started, regardless of activity.

`find RUN_DIR -type f -not -name "*.log" -newer PROMPT.md -print -quit`
returned the FIRST match (dream-intake.json, written once at end of intake),
not the newest, and excluded logs/agent-run.log. Then `kill -9` on the
already-dead PID returned 1 under `set -e`, aborting the launcher before
pass3-retry, READY and the morning email.
"""
import os
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
LIB = HERE / "dream-watchdog-lib.sh"
LAUNCH = HERE / "dream-launch.sh"
NOW = 2_000_000_000


def _touch(p: Path, mtime: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    os.utime(p, (mtime, mtime))


def _age(run_dir: Path) -> int:
    out = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; '
                       f'dream_activity_age "$1" "$1/PROMPT.md" {NOW}', "_", str(run_dir)],
        check=True, capture_output=True, text=True)
    return int(out.stdout.strip())


def _run_dir(tmp_path: Path) -> Path:
    rd = tmp_path / "run"
    _touch(rd / "PROMPT.md", NOW - 3 * 3600)           # templated at 03:00
    _touch(rd / "dream-intake.json", NOW - 1800)       # written once, end of intake
    return rd


def test_newest_not_first_match(tmp_path):
    rd = _run_dir(tmp_path)
    _touch(rd / "drafts" / "a.md", NOW - 60)           # active a minute ago
    assert _age(rd) == 60


def test_heartbeat_counts(tmp_path):
    rd = _run_dir(tmp_path)
    _touch(rd / "logs" / "heartbeat", NOW - 30)
    assert _age(rd) == 30


def test_agent_log_counts(tmp_path):
    rd = _run_dir(tmp_path)
    _touch(rd / "logs" / "agent-run.log", NOW - 10)
    assert _age(rd) == 10


def test_genuine_stall_detected(tmp_path):
    rd = _run_dir(tmp_path)
    _touch(rd / "logs" / "agent-run.log", NOW - 1700)
    assert _age(rd) == 1700                              # > STALL_THRESHOLD 1500


def test_nothing_newer_falls_back_to_prompt(tmp_path):
    rd = tmp_path / "run"
    _touch(rd / "PROMPT.md", NOW - 100)
    assert _age(rd) == 100


def test_kill_tree_survives_set_e_on_dead_pid():
    dead = subprocess.Popen(["true"]); dead.wait()
    out = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; dream_kill_tree {dead.pid}; echo survived'],
        capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == "survived"


def test_kill_tree_kills_live_process():
    p = subprocess.Popen(["sleep", "60"])
    subprocess.run(["bash", "-c", f'source "{LIB}"; dream_kill_tree {p.pid}'], timeout=20)
    assert p.wait(timeout=5) != 0


def test_launcher_uses_lib_and_tolerates_nonzero():
    src = LAUNCH.read_text()
    assert subprocess.run(["bash", "-n", str(LAUNCH)]).returncode == 0
    assert "-print -quit" not in src
    assert "dream_activity_age" in src
    # every _run_claude invocation must be guarded so set -e cannot abort the launcher
    calls = [l for l in src.splitlines() if re.match(r'\s*_run_claude "', l)]
    assert calls and all("|| EXIT_CODE=$?" in l for l in calls), calls
    assert re.search(r'python3 "\$AUDIT_SCRIPT".*\|\| AUDIT_RC=\$\?', src)


def test_reap_keepalive_kills_only_this_runs_keepalive(tmp_path):
    """v171 (2026-10-28): RUN/tmp/keepalive.sh outlived claude by ~22h."""
    mine, other = tmp_path / "run", tmp_path / "other-run"
    for rd in (mine, other):
        (rd / "tmp").mkdir(parents=True)
        (rd / "tmp" / "keepalive.sh").write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
    ka = subprocess.Popen(["/bin/sh", str(mine / "tmp" / "keepalive.sh"), str(mine)])
    bystander = subprocess.Popen(["/bin/sh", str(other / "tmp" / "keepalive.sh"), str(other)])
    try:
        out = subprocess.run(
            ["bash", "-c", f'set -euo pipefail; source "{LIB}"; dream_reap_keepalive "$1"; echo ok',
             "_", str(mine)], capture_output=True, text=True, timeout=20)
        assert out.stdout.strip() == "ok"
        assert ka.wait(timeout=5) != 0
        assert bystander.poll() is None
    finally:
        for p in (ka, bystander):
            if p.poll() is None:
                p.kill()


def test_reap_keepalive_noop_when_none_running(tmp_path):
    out = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; dream_reap_keepalive "$1"; echo ok',
         "_", str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == "ok"


def test_launcher_bg_wait_and_keepalive_reaping():
    src = LAUNCH.read_text()
    # v171: print mode killed background subagents 10 min after the last turn.
    assert re.search(r"^export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0$", src, re.M)
    assert re.search(r"^trap '.*dream_reap_keepalive \"\$RUN_DIR\".*' EXIT$", src, re.M)
    # the lib must be sourced before the trap that calls into it
    assert src.index("dream-watchdog-lib.sh\"") < src.index("dream_reap_keepalive \"$RUN_DIR\"; rm -f")
    # the audit window opens at claude start, not launcher start
    assert '--since "${CLAUDE_START_UTC:-$RUN_START_UTC}"' in src
