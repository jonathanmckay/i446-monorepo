"""Regression tests for dream-launch.sh watchdog + backfill pointer (v174).

1. Watchdog used `find ... -newer PROMPT.md -print -quit`: first match in
   directory order, not the newest file, so active runs were killed as stalled.
2. Backfill advance used `date -j -f FMT "$D" -v-1d`: BSD date ignores -v after
   the operand and writes "Fri Oct 30 03:00:01 PDT 2026" (live pointer on ix,
   2026-10-31), which the next run resets to yesterday.

Folded into landing (2026-10-31): the launcher now delegates to
dream-watchdog-lib.sh (dream_activity_age) and dream-backfill-pointer.sh, so
these tests check that the launcher wires those in and that they behave.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
LAUNCH = HERE / "dream-launch.sh"
LIB = HERE / "dream-watchdog-lib.sh"
POINTER = HERE / "dream-backfill-pointer.sh"
SRC = LAUNCH.read_text()


def test_launcher_uses_helpers():
    assert not re.search(r"^[^#]*-print -quit", SRC, re.M)
    assert re.search(r"^[^#]*FILE_AGE=\$\(dream_activity_age ", SRC, re.M)
    assert re.search(r"^[^#]*dream-backfill-pointer\.sh", SRC, re.M)
    assert not re.search(r"^[^#]*date -j -f [^|]*-v-1d", SRC, re.M)


def test_watchdog_picks_newest(tmp_path):
    now = time.time()
    (tmp_path / "PROMPT.md").write_text("p")
    os.utime(tmp_path / "PROMPT.md", (now - 7200, now - 7200))
    for i in range(30):  # many stale-but-newer-than-PROMPT files, one fresh
        f = tmp_path / f"f{i:02d}.md"
        f.write_text("x")
        os.utime(f, (now - 3600, now - 3600))
    os.utime(tmp_path / "f17.md", (now - 5, now - 5))
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; dream_activity_age "{tmp_path}" "{tmp_path}/PROMPT.md" {int(now)}'],
        capture_output=True, text=True, check=True).stdout.strip()
    assert int(out) <= 6


def test_watchdog_counts_launcher_log(tmp_path):
    now = time.time()
    (tmp_path / "PROMPT.md").write_text("p")
    os.utime(tmp_path / "PROMPT.md", (now - 7200, now - 7200))
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / "agent-run.log"
    log.write_text("l")
    os.utime(log, (now - 3, now - 3))
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; dream_activity_age "{tmp_path}" "{tmp_path}/PROMPT.md" {int(now)}'],
        capture_output=True, text=True, check=True).stdout.strip()
    assert int(out) <= 4


def test_backfill_advance_writes_iso_date(tmp_path):
    state = tmp_path / "ptr"
    state.write_text("2026-10-30\n")
    out = subprocess.run([str(POINTER), str(state), "2026-03-01"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["2026-10-30", "2026-10-30"]
    assert state.read_text().strip() == "2026-10-29"
