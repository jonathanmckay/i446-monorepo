#!/usr/bin/env python3
"""Regression (2026-10-23): "when I went to split a task, after I was done it
does not appear in dtd (even though it's in the todo list)". split.sh's
optimistic background-write hide (2026-10-19) appended the task name to the
session's REMOVED list unconditionally, so a remainder kept on TODAY (same
name) was name-hidden for the rest of the session. The hide must fire only
when the task leaves today: closed (keep 0) or remainder due later.

Runs the real block, cut out of the split.sh heredoc in dtd.sh, under zsh."""
import datetime as dt
import re
import subprocess
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()
BODY = DTD.split("cat > \"$DTD_SPLIT\" << 'SPLITEOF'", 1)[1].split("\nSPLITEOF", 1)[0]
HIDE = re.search(r"(if \[\[ \"\$keep_rem\" == 0 .*?\nfi\n)", BODY, re.S).group(1)


def _run(tmp_path, keep, due):
    removed = tmp_path / "removed"
    removed.write_text("")
    script = f'clean="Get appraisal, buy units"; keep_rem="{keep}"; rem_due="{due}"; REMOVED="{removed}"\n' + HIDE
    subprocess.run(["zsh", "-c", script], check=True)
    return removed.read_text()


def test_remainder_kept_today_is_not_hidden(tmp_path):
    assert _run(tmp_path, "1", "") == ""
    assert _run(tmp_path, "1", dt.date.today().isoformat()) == ""


def test_closed_or_deferred_split_still_hides_at_once(tmp_path):
    assert _run(tmp_path, "0", "") == "get appraisal, buy units\n"
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert _run(tmp_path, "1", tomorrow) == "get appraisal, buy units\n"


def test_no_unconditional_name_hide_left_in_split():
    for line in BODY.splitlines():
        if ">> \"$REMOVED\"" in line:
            assert line.startswith("  "), f"unguarded REMOVED write: {line!r}"
