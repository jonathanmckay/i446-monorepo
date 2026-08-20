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


def test_didfast_treats_hiit_as_variable_minutes():
    # 2026-07-28: hiit made variable so completion always asks for minutes
    # instead of silently logging the manifest card's fixed (10) default.
    df = (Path(__file__).resolve().parent / "did-fast.py").read_text()
    m = re.search(r'VARIABLE_0N = \{([^}]*)\}', df)
    assert '"hiit"' in m.group(1)


def test_done_script_prompts_for_kid_minutes():
    m = re.search(r"cat > \"\$DTD_DONE\" << DONEEOF\n(.*?)\nDONEEOF", SRC, re.S)
    body = m.group(1)
    for h in ("xk20", "xk22", "xk26", "i444", "hiit", "新闻"):
        assert re.search(r'%s\) _ip=' % h, body), f"{h} must have a completion prompt"
    assert 'clean="\\$clean \\$_iv"' in body, "typed value must be appended to the completion"


def test_router_gives_kid_habits_a_tty():
    m = re.search(r"cat > \"\$DTD_DONE_ROUTER\" << ROUTEREOF\n(.*?)\nROUTEREOF", SRC, re.S)
    body = m.group(1)
    assert re.search(r'cpap\|xk20\|xk22\|xk26\|i444\|hiit\|新闻\|', body), \
        "value-prompt habits must route to execute (tty)"


def test_mainloop_arg_case_includes_kid_habits():
    assert "cpap|ibx\\ s897|ibx\\ i9|ibx\\ m5x2|xk20|xk22|xk26|i444|hiit|新闻)" in SRC


def test_i444_zero_survives_done_script_sanitizer():
    """i444's whole point (2026-07-24): typing 0 records 'none needed today'.
    The DONE script's digit sanitizer + non-empty check must let a literal
    '0' through — [[ -n "0" ]] is true in zsh, so '0' must be appended."""
    m = re.search(r"cat > \"\$DTD_DONE\" << DONEEOF\n(.*?)\nDONEEOF", SRC, re.S)
    body = m.group(1)
    # The append must be gated on non-empty, NOT on non-zero (2026-08-20:
    # split into dated/undated branches so deferred catch-up copies route
    # their typed value as an explicit [N] override instead of a bare
    # number — see test_dated_copies_prompt_and_use_points_override below).
    assert 'if [[ -n "\\$_iv" ]]; then' in body
    assert 'clean="\\$clean \\$_iv"' in body
    assert '"\\$_iv" != "0"' not in body and '-gt 0' not in body


def test_dated_copies_prompt_and_use_points_override():
    """2026-08-20 bug: /defer stamps a deferred daily/weekly habit's origin
    date into its one-off copy's name ("xk26 7.21", defer-fast.py's
    _dated_copy_content), so it doesn't re-claim the habit's own 0n/1n+
    column on completion. But the stamp also made the copy fail the exact
    "xk26" case match below, silently skipping the number prompt and using
    the card's static default points on completion. All three places
    that special-case these habits (done script, done-router, main-loop
    arg case) must strip the stamp before matching, and the done script
    (and the main-loop timer-autofill path) must route the typed/detected
    value as an explicit [N] points override for dated copies, since they
    fall through to the generic Todoist path where a bare trailing number
    is silently discarded as an unused time_value."""
    df = (Path(__file__).resolve().parent / "did-fast.py").read_text()
    assert re.search(r'VARIABLE_0N = \{[^}]*"xk20"[^}]*"xk22"[^}]*"xk26"', df)

    done = re.search(r"cat > \"\$DTD_DONE\" << DONEEOF\n(.*?)\nDONEEOF", SRC, re.S).group(1)
    assert "clean_base=" in done and "_dated=" in done
    assert 'case "\\$clean_base" in' in done
    assert 'clean="\\$clean [\\$_iv]"' in done, \
        "dated copies must append the typed value as [N], not a bare number"

    router = re.search(r"cat > \"\$DTD_DONE_ROUTER\" << ROUTEREOF\n(.*?)\nROUTEREOF", SRC, re.S).group(1)
    assert "_t_base=" in router
    assert 'case "\\$_t_base" in' in router, \
        "router must match on the date-stripped name or dated copies never get a tty to prompt on"

    mainloop = re.search(r'clean_lower=\$\(echo "\$clean" \| tr .*?\n(.*?)\n  esac', SRC, re.S).group(1)
    assert "clean_base=" in mainloop and '_dated=""' in mainloop
    assert 'case "$clean_base" in' in mainloop
    assert 'clean="$clean [$timer_mins]"' in mainloop


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
