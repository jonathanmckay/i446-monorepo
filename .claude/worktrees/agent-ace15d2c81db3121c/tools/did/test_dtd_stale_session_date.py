#!/usr/bin/env python3
"""Regression (2026-08-08): "-1n tasks are not rendering at the top" (in
practice: not rendering at all).

fzf's own --bind strings (reload($DTD_RELOAD), enter/alt-enter/ctrl-s/...)
are captured as STATIC text the moment fzf launches. Live observed: this
file's own fzf process launched 2026-08-07 09:30 and was still running
2026-08-08 07:15 -- a single continuous session routinely spans many hours,
crossing midnight. DTD_LIST_CMD (-> DTD_RELOAD) used to bake in $LOCAL_TODAY
as a literal string at construction time, so every keypress-triggered
reload for the rest of that fzf session kept re-sending the date from
whenever fzf happened to launch. The list generator's today_tasks filter
(`t['due'] <= today`) then excluded every card genuinely due today --
including -1neon ritual cards.

Fix: DTD_LIST_CMD embeds the date argument as a literal, UNEVALUATED shell
command substitution ("$(date +%Y-%m-%d)") instead of a pre-expanded
variable. Bash only expands $LOCAL_TODAY once, at assignment time; a $(...)
left unescaped-until-use is re-evaluated by whatever shell later actually
RUNS the command string -- which is exactly what happens both for the
initial `eval "$DTD_LIST_CMD" | fzf` pipe and for every fzf reload()/
execute() action for the rest of that fzf process's life, however long it
stays open. This is a bash-quoting fix, not a change to the list-generator
python payload -- the payload keeps trusting its argv today (several
existing tests, e.g. test_dtd_0neon_completed_recurring_hidden.py, pin a
fixed reference date through that argv and must keep working)."""
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

DTD_PATH = Path(__file__).resolve().parent / "dtd.sh"
DTD = DTD_PATH.read_text()


def _dtd_list_cmd_line() -> str:
    line = next(l for l in DTD.splitlines() if l.strip().startswith('DTD_LIST_CMD="$DTD_LIST'))
    return line.strip()


def test_date_arg_is_a_literal_unescaped_command_substitution():
    """Structural: the date argument must be an UNQUOTED-FOR-EVAL
    $(date ...) substitution, not a pre-expanded shell variable like
    $LOCAL_TODAY -- single-quoting it (an earlier draft of this fix) would
    also be wrong, since single quotes suppress command substitution even
    under a later eval/shell re-run."""
    line = _dtd_list_cmd_line()
    assert '$LOCAL_TODAY' not in line, (
        "DTD_LIST_CMD still bakes in $LOCAL_TODAY as a literal -- fzf's "
        "--bind strings freeze this at launch, so a long-open session goes "
        "stale past midnight (2026-08-08 bug)")
    assert '\\$(date +%Y-%m-%d)' in line, (
        "expected an escaped, deferred \\$(date +%Y-%m-%d) substitution in "
        "DTD_LIST_CMD so it re-evaluates on every reload, not just at "
        "construction time")
    # It must NOT be single-quoted (that would suppress the substitution
    # when the string is later run through a shell).
    m = re.search(r"'\\\$\(date \+%Y-%m-%d\)'", line)
    assert not m, (
        "the date substitution is single-quoted -- single quotes prevent "
        "command substitution from being evaluated even when the string is "
        "later eval'd/executed by a shell, defeating the whole fix")


def test_reevaluates_live_on_every_shell_run_not_just_once(tmp_path):
    """Behavioral: build DTD_LIST_CMD exactly as dtd.sh does (with a stub
    list script standing in for the real python payload) using a MOCKED
    `date` command, then eval it twice under two different fake "now"
    values -- simulating two reload() invocations on either side of a
    midnight rollover within the SAME long-lived fzf session. Both must
    reflect the current fake date, proving the substitution is re-evaluated
    live each run rather than frozen from the first construction."""
    # Stub `date` that reads the fake "now" from an env var so the test
    # controls it without touching the real system clock.
    fake_date_bin = tmp_path / "date"
    fake_date_bin.write_text(dedent("""\
        #!/bin/sh
        if [ "$1" = "+%Y-%m-%d" ]; then
            echo "$FAKE_TODAY"
        else
            exit 1
        fi
        """))
    fake_date_bin.chmod(0o755)

    # Stub list script standing in for $DTD_LIST -- just echoes its argv so
    # the test can see exactly what date string was actually passed through.
    stub_list = tmp_path / "list.sh"
    stub_list.write_text("#!/bin/sh\necho \"ARGS:$*\"\n")
    stub_list.chmod(0o755)

    line = _dtd_list_cmd_line()
    assert line.startswith("DTD_LIST_CMD=")

    harness = f"""
set -e
export PATH="{tmp_path}:$PATH"
DTD_LIST="{stub_list}"
DTD_CACHE_FILE="/tmp/cache.json"
DTD_DONE_FILE="/tmp/done.json"
DTD_REMOVED="/tmp/removed"
COLUMNS=120
DTD_SKIPPED="/tmp/skipped"
DTD_TIMER="/tmp/timer"
DTD_VIEW="/tmp/view"
DTD_BLOCKPICK="/tmp/blockpick"
{line}
eval "$DTD_LIST_CMD"
"""

    r1 = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                         env={"FAKE_TODAY": "2026-08-07", "PATH": "/usr/bin:/bin"})
    r2 = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                         env={"FAKE_TODAY": "2026-08-08", "PATH": "/usr/bin:/bin"})

    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    assert "2026-08-07" in r1.stdout, f"expected the day-1 fake date, got: {r1.stdout!r}"
    assert "2026-08-08" in r2.stdout, (
        f"same constructed command re-run with a LATER fake 'now' still "
        f"produced the old date -- the substitution was frozen instead of "
        f"re-evaluated live. stdout: {r2.stdout!r}")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
