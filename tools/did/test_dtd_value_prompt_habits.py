#!/usr/bin/env python3
"""Regression: xk20/xk22/xk26 prompt for a value on completion, like cpap.

Change (2026-07-14): xk20 (Theo) / xk22 (Ren) / xk26 (Rori) academic-time habits
now ask for minutes when completed via alt-enter, and the typed number is
appended so did-fast writes it to the habit's 0n column (they are already
VARIABLE_0N in did-fast). Mirrors cpap's completion prompt in all three places
cpap is special-cased: the DONE script, the done-router (tty routing), and the
main-loop arg case.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def test_didfast_treats_kid_habits_as_variable_minutes():
    df = (Path(__file__).resolve().parent / "did-fast.py").read_text()
    assert re.search(r'VARIABLE_0N = \{[^}]*"xk20"[^}]*"xk22"[^}]*"xk26"', df)


def test_done_script_prompts_for_kid_minutes():
    m = re.search(r"cat > \"\$DTD_DONE\" << DONEEOF\n(.*?)\nDONEEOF", SRC, re.S)
    body = m.group(1)
    for h in ("xk20", "xk22", "xk26", "i444", "新闻"):
        assert re.search(r'%s\) _ip=' % h, body), f"{h} must have a completion prompt"
    assert 'clean="\\$clean \\$_iv"' in body, "typed value must be appended to the completion"


def test_router_gives_kid_habits_a_tty():
    m = re.search(r"cat > \"\$DTD_DONE_ROUTER\" << ROUTEREOF\n(.*?)\nROUTEREOF", SRC, re.S)
    body = m.group(1)
    assert re.search(r'cpap\|xk20\|xk22\|xk26\|i444\|新闻\|', body), \
        "value-prompt habits must route to execute (tty)"


def test_mainloop_arg_case_includes_kid_habits():
    assert "cpap|ibx\\ s897|ibx\\ i9|ibx\\ m5x2|xk20|xk22|xk26|i444|新闻)" in SRC


def test_i444_zero_survives_done_script_sanitizer():
    """i444's whole point (2026-07-24): typing 0 records 'none needed today'.
    The DONE script's digit sanitizer + non-empty check must let a literal
    '0' through — [[ -n "0" ]] is true in zsh, so '0' must be appended."""
    m = re.search(r"cat > \"\$DTD_DONE\" << DONEEOF\n(.*?)\nDONEEOF", SRC, re.S)
    body = m.group(1)
    # The append must be gated on non-empty, NOT on non-zero.
    assert '[[ -n "\\$_iv" ]] && clean="\\$clean \\$_iv"' in body
    assert '"\\$_iv" != "0"' not in body and '-gt 0' not in body


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
