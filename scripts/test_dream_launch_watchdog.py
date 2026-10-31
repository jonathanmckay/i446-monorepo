"""Regression tests for dream-launch.sh watchdog + backfill pointer (v174).

1. Watchdog used `find ... -newer PROMPT.md -print -quit`: first match in
   directory order, not the newest file, so active runs were killed as stalled.
2. Backfill advance used `date -j -f FMT "$D" -v-1d`: BSD date ignores -v after
   the operand and writes "Fri Oct 30 03:00:01 PDT 2026" (live pointer on ix,
   2026-10-31), which the next run resets to yesterday.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

LAUNCH = Path(__file__).parent / "dream-launch.sh"
SRC = LAUNCH.read_text()


def _line(pattern: str) -> str:
    m = re.search(pattern, SRC, re.M)
    assert m, pattern
    return m.group(0).strip()


def test_watchdog_picks_newest(tmp_path):
    assert not re.search(r"^[^#]*-print -quit", SRC, re.M)
    expr = _line(r"^\s*NEWEST_MTIME=.*$")
    now = time.time()
    (tmp_path / "PROMPT.md").write_text("p")
    os.utime(tmp_path / "PROMPT.md", (now - 7200, now - 7200))
    for i in range(30):  # many stale-but-newer-than-PROMPT files, one fresh
        f = tmp_path / f"f{i:02d}.md"
        f.write_text("x")
        os.utime(f, (now - 3600, now - 3600))
    os.utime(tmp_path / "f17.md", (now - 5, now - 5))
    log = tmp_path / "launch.log"
    log.write_text("l")
    os.utime(log, (now - 5000, now - 5000))
    out = subprocess.run(["bash", "-c", f'{expr}; echo "$NEWEST_MTIME"'],
                         env={**os.environ, "RUN_DIR": str(tmp_path), "LOG": str(log)},
                         capture_output=True, text=True, check=True).stdout.strip()
    assert abs(int(out) - (now - 5)) < 2


def test_backfill_advance_writes_iso_date():
    expr = _line(r"^\s*NEXT_BACKFILL=.*$")
    out = subprocess.run(["bash", "-c", f'BACKFILL_DATE=2026-10-30; {expr}; echo "$NEXT_BACKFILL"'],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "2026-10-29"
