"""dtd crashed with rc=2 and no error text (39 times, 2026-10-11..30), mostly
~30-60 s after a timer start. Cause: fzf <= 0.74.4 waits for a key in
select(2) and gives up after 100 consecutive EINTRs (tui/light_unix.go,
maxSelectTries) -> Fatal event -> silent exit 2. While a timer runs dtd
reloads every second, and each spawned child's SIGCHLD interrupts that wait,
so an idle picker hit the limit. fzf 0.74.5 retries EINTR without a limit.
dtd needs that fzf."""
import re
import shutil
import subprocess

import pytest

MIN = (0, 74, 5)


def test_installed_fzf_does_not_count_eintr_against_the_key_wait():
    fzf = shutil.which("fzf")
    if not fzf:
        pytest.skip("fzf not on PATH")
    out = subprocess.run([fzf, "--version"], capture_output=True, text=True).stdout
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", out.strip())
    assert m, out
    have = tuple(int(x) for x in m.groups())
    assert have >= MIN, (f"fzf {'.'.join(map(str, have))} exits rc=2 after 100 "
                         f"EINTRs while idle; run `brew upgrade fzf`")
