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


# --- 2026-11-05 (v178): stall with 20 drafts shipped NO brief ----------------
# Launcher died under set -e right after the STALL line (no "finished", no
# READY). End-to-end: real dream-launch.sh, fake HOME, fake claude.

FAKE_CLAUDE = r'''#!/bin/bash
[[ "${1:-}" == "--version" ]] && { echo fake; exit 0; }
RUN=$(ls -d "$HOME"/vault/i447/i446/.dream-runs/*-v* | tail -1)
prompt=$(cat)
n=$(cat "$HOME/calls" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$HOME/calls"
mode=$(sed -n "${n}p" "$HOME/modes")
case "$mode" in
  stall)  echo "# Draft A" > "$RUN/drafts/a.md"; sleep 60 ;;
  exit0)  echo "# Draft A" > "$RUN/drafts/a.md"; exit 0 ;;
  exit1)  echo "# Draft A" > "$RUN/drafts/a.md"; exit 1 ;;
  brief)  [[ "$prompt" == *"Complete Pass 3"* ]] && echo "# real brief" > "$RUN/morning-brief.md"; exit 0 ;;
esac
'''


def _launch(tmp_path: Path, modes, deadline=600):
    home = tmp_path / "home"
    base = home / "vault" / "i447" / "i446"
    base.mkdir(parents=True)
    (base / "dream-prompt-base.md").write_text("Dream {{VERSION}} {{RUN_DIR}}\n")
    (home / "modes").write_text("\n".join(modes) + "\n")
    fake = tmp_path / "claude"; fake.write_text(FAKE_CLAUDE); fake.chmod(0o755)
    env = {"HOME": str(home), "PATH": os.environ["PATH"],
           "CLAUDE_CODE_OAUTH_TOKEN": "test", "DREAM_CLAUDE": str(fake),
           "DREAM_LOCK": str(tmp_path / "lock"), "DREAM_STALL_THRESHOLD": "2",
           "DREAM_WATCHDOG_INTERVAL": "1", "DREAM_DEADLINE_SEC": str(deadline)}
    rc = subprocess.run(["bash", str(LAUNCH)], env=env, timeout=120).returncode
    run = next((base / ".dream-runs").glob("*-v*"))
    log = (run / "logs" / "agent-run.log").read_text()
    calls = int((home / "calls").read_text())
    return rc, run, log, calls


def test_stall_then_pass3_retry_ships_brief(tmp_path):
    rc, run, log, calls = _launch(tmp_path, ["stall", "brief"])
    assert rc == 0 and calls == 2, log
    assert "STALL detected (full-run)" in log and "retrying Pass 3" in log
    assert (run / "morning-brief.md").read_text() == "# real brief\n"
    assert (run / "READY").exists() and "complete. Run dir" in log


def test_clean_exit_without_brief_still_retries(tmp_path):
    # budget/turn limit: claude exits 0 with drafts but no brief
    rc, run, log, calls = _launch(tmp_path, ["exit0", "brief"])
    assert calls == 2 and (run / "morning-brief.md").read_text() == "# real brief\n", log


def test_nonzero_exit_retries(tmp_path):
    rc, run, log, calls = _launch(tmp_path, ["exit1", "brief"])
    assert calls == 2 and (run / "morning-brief.md").read_text() == "# real brief\n", log


def test_retry_runs_past_deadline(tmp_path):
    # hard DEADLINE already spent: pass3 still gets PASS3_GRACE
    rc, run, log, calls = _launch(tmp_path, ["stall", "brief"], deadline=1)
    assert calls == 2 and (run / "morning-brief.md").read_text() == "# real brief\n", log


def test_both_passes_fail_fallback_brief(tmp_path):
    rc, run, log, calls = _launch(tmp_path, ["stall", "stall"])
    brief = (run / "morning-brief.md").read_text()
    assert "FALLBACK brief" in brief and "drafts/a.md" in brief and "Draft A" in brief
    assert (run / "READY").exists() and "FALLBACK brief written: 1 items" in log


def test_fallback_noop_without_drafts_or_with_brief(tmp_path):
    rd = tmp_path / "r"; (rd / "drafts").mkdir(parents=True)
    sh = f'set -euo pipefail; source "{LIB}"; dream_fallback_brief "$1" v0 1'
    subprocess.run(["bash", "-c", sh, "_", str(rd)], check=True)
    assert not (rd / "morning-brief.md").exists()
    (rd / "drafts" / "x.md").write_text("# X\n"); (rd / "morning-brief.md").write_text("keep")
    subprocess.run(["bash", "-c", sh, "_", str(rd)], check=True)
    assert (rd / "morning-brief.md").read_text() == "keep"
