#!/usr/bin/env python3
"""Regression (2026-11-05): "I tried saving an updated task name and it didn't
take". One ctrl-p save with a new name and pts done renamed the task, then the
split rewrote the remainder from the dtd cache snapshot, which still held the
old title (the watcher and bgrefresh `cp` the live cache over it). The split
writer must name the remainder from the live task it already fetched.

Runs the real lines, cut out of the split.sh heredoc in dtd.sh, under zsh, so
the double-quoted `python3 -c` body's escaping is exercised too."""
import re
import subprocess
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()
BODY = DTD.split("cat > \"$DTD_SPLIT\" << 'SPLITEOF'", 1)[1].split("\nSPLITEOF", 1)[0]
LIVE = re.search(r"^# The live title.*?\nclean = .*?\n", BODY, re.S | re.M)


def _clean_from(content):
    assert LIVE, "split writer no longer takes its name from the live task"
    src = f"import re\nclean = 'old cached name'\nprev_content = {content!r}\n"
    script = 'python3 -c "' + src.replace('"', '\\"') + LIVE.group(0) + 'print(clean)"'
    return subprocess.run(["zsh", "-c", script], check=True, capture_output=True,
                          text=True).stdout.strip()


def test_remainder_takes_the_live_name_over_a_stale_cache():
    assert _clean_from("Sign for Jamie + packet (15) [45]") == "Sign for Jamie + packet"


def test_live_name_strips_every_estimate_style():
    assert _clean_from("write memo (30) [20] {5}") == "write memo"
    assert _clean_from("xk20 [1/m] (20)") == "xk20"


def test_blank_live_title_keeps_the_cached_name():
    assert _clean_from("") == "old cached name"


def test_live_name_is_set_before_any_name_is_used():
    assert BODY.index("prev_content = task.get") < BODY.index("# The live title") \
        < BODY.index("today_label = done_desc if done_desc else clean")
