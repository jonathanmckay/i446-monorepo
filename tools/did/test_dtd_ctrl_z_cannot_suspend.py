"""Regression (2026-10-24): "dtd cli just exited on its own". ^Z (dtd's undo
key) reached the kernel whenever the tty was in cooked mode (prompts, forms,
every `stty sane`), SIGTSTP stopped the whole dtd job, and the shell prompt came
back as if dtd had quit; the stopped copy kept holding the tty beside the next
launch. dtd now runs with no suspend character on its tty and restores it on
exit. (`trap '' TSTP` was tried first: zsh resets job-control signals to
default in children, so fzf and prompts still stopped.)"""
import re
import subprocess
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()


def test_no_suspend_char_for_dtds_lifetime():
    on = DTD.index("stty susp undef < /dev/tty")
    picker = DTD.index('fzf_output=$(eval "$DTD_LIST_CMD" | fzf ')
    off = DTD.index("stty susp '^Z' < /dev/tty")
    assert on < picker < off


def test_every_stty_sane_keeps_suspend_off():
    sane = [l for l in DTD.splitlines() if re.search(r"\bstty sane\b", l) and not l.strip().startswith("#")]
    assert sane and all("susp undef" in l for l in sane), [l for l in sane if "susp undef" not in l]


def test_stty_line_clears_suspend_on_a_real_pty():
    out = subprocess.run(["script", "-q", "/dev/null", "zsh", "-c",
                          'stty susp "^Z"; stty sane susp undef; stty -a'],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20).stdout
    assert re.search(r"(?<!d)susp = <undef>", out), out
