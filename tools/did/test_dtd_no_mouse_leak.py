#!/usr/bin/env python3
"""Regression: SGR mouse escape sequences must never leak into dtd's input box.

Bug (2026-07-05): the query filled with literal `^[[<34;41;15M...` — SGR
mouse-MOTION events typed as text. fzf subscribes to click + SGR scroll
(1000/1006) only; motion events (1002/1003, left enabled by a child program
or forwarded by cmux) fall through fzf's parser into the query.

Current design (2026-07-15 revision — supersedes the original --no-mouse fix):
this fzf build ERRORS on --no-mouse ("unknown option" kills the list pipe), so
mouse stays ON deliberately for scroll support. Instead, every fzf launch and
every interactive execute() script resets the tracking modes to fzf's own
subscription — motion OFF (1002l/1003l), click+SGR ON (1000h/1006h).

2026-07-27: the ⌃⏎ value-prompt script (DONEEOF) was the one interactive
script without the cleanup, and it also needs the tty-input DRAIN (like
SPLITEOF): scroll bursts buffered while the prompt sat open dumped into the
query as literal text on resume ("input pane in dtd is a mess").

2026-09-01: the fix had only ever been ported to 4 of the ~10 scripts that
take real wall-clock time (curl/toggl calls, sleep-polling loops, subprocess
spawns) while fzf is suspended — the leak isn't specific to scripts with an
explicit `read` prompt, it's any script slow enough for a scroll/click burst
to land while nothing is consuming it. Ported to the remaining ones
(STARTEOF, DELETEEOF, ARMEOF, APPLYEOF, UNDOEOF, AGENTEOF) plus a new
REFRESHEOF (ctrl-r's refresh-cache command, pulled out of the inline
--bind string so it has room for the fix). Both RESET_SEQ and DRAIN are
checked for all of them, not just RESET_SEQ — the earlier split undersold
what DEFEREOF/EDITEOF already had (both), and the leak mechanism doesn't
distinguish an interactive prompt from any other blocking window.

2026-09-11: LISTEOF (the list-generation script every reload() binding runs,
including the auto-reload watcher's `reload($DTD_LIST ...)` POST fired on
every single FIFO-worker completion) was the one blocking script never
ported. fzf's reload(...) blocks its own tty read while the reload command
runs, same as execute() does, so this was the same leak class — just never
classified as one because it isn't launched via execute()/execute-silent.
Because the auto-reload watcher fires it once per completed task, this
window recurred constantly while dtd was "processing" a batch, matching a
user report that scrolling only got typed into the query during processing.
"""
import re
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()

RESET_SEQ = r"\033[?1002l\033[?1003l\033[?1000h\033[?1006h"
DRAIN = "while read -t 0.05 -k 1 _discard"


def _heredoc(eof: str) -> str:
    m = re.search(f"<< '?{eof}'?\n", DTD)
    assert m, f"heredoc {eof} not found"
    end = DTD.index(f"\n{eof}\n", m.start())
    return DTD[m.start():end]


def test_fzf_mouse_stays_on_no_flag():
    # --no-mouse is an unknown option to this fzf build (bug 2026-07-15) and
    # would kill fzf at launch. Mouse-on is deliberate (scroll navigation).
    m = re.search(r"fzf --prompt.*?--header-first", DTD, re.DOTALL)
    assert m, "fzf invocation not found"
    assert "--no-mouse" not in m.group(0)


def test_mouse_modes_normalized_before_each_fzf_launch():
    i_fzf = DTD.index("fzf --prompt")
    loop_start = DTD.rindex("while true; do", 0, i_fzf)
    pre_launch = DTD[loop_start:i_fzf]
    assert RESET_SEQ in pre_launch, (
        "each fzf launch must strip stray motion modes / restore click+scroll")


BLOCKING_SCRIPTS = (
    "DEFEREOF", "EDITEOF", "SPLITEOF", "DONEEOF",
    "STARTEOF", "DELETEEOF", "ARMEOF", "APPLYEOF", "UNDOEOF", "AGENTEOF",
    "REFRESHEOF", "LISTEOF",
)


def test_interactive_execute_scripts_reset_mouse_modes():
    # Every script that takes the tty while fzf is suspended must normalize
    # the tracking modes before fzf resumes.
    for eof in BLOCKING_SCRIPTS:
        assert RESET_SEQ in _heredoc(eof), \
            f"{eof} script must reset mouse modes on exit"


def test_prompt_window_scripts_drain_buffered_tty_input():
    # Any script slow enough for a scroll/click burst to land while it's
    # running (an explicit read-prompt, a curl call, a sleep-poll loop, a
    # subprocess spawn) accumulates that burst in the tty buffer; without a
    # drain the burst types itself into fzf's query on resume. Not limited to
    # scripts with an interactive prompt — see the module docstring.
    for eof in BLOCKING_SCRIPTS:
        assert DRAIN in _heredoc(eof), \
            f"{eof} must drain buffered tty input before fzf resumes"


def test_list_reload_script_resets_mouse_modes_after_python_not_in_stdout():
    # Regression (2026-09-11): scrolling while dtd was "processing" typed
    # into the query. LISTEOF is what every reload() binding runs, including
    # the auto-reload watcher's POST fired on every FIFO-worker completion —
    # so it recurs constantly during processing, unlike the one-shot
    # execute() scripts. fzf blocks its own tty read for the duration of a
    # reload(...) command exactly as it does for execute(...), so this needed
    # the same fix; it had just never been classified as part of that family.
    body = _heredoc("LISTEOF")
    py_end = body.rindex('" "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"')
    after_py = body[py_end:]
    assert RESET_SEQ in after_py and DRAIN in after_py, (
        "LISTEOF must reset mouse modes and drain tty input after the "
        "python list-generation call, before fzf resumes")
    # The reset/drain must target /dev/tty explicitly, not stdout — stdout
    # here is the row data fzf reads back as the reload payload; writing the
    # reset sequence there would corrupt the list instead of the query.
    assert "> /dev/tty" in after_py and "< /dev/tty" in after_py
