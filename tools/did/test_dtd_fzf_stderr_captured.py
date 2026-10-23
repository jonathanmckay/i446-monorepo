"""fzf's error text survives a crash (2026-10-22): two rc=2 exits with no
keypress, mid background reload, left nothing to diagnose because fzf wrote the
error to the tty and dtd's exit clear wiped it. stderr now appends to a log,
one header line per session; fzf draws on /dev/tty, so the UI is unaffected."""
from pathlib import Path

DTD = (Path(__file__).parent / "dtd.sh").read_text()


def test_fzf_stderr_goes_to_a_log_with_a_session_header():
    i = DTD.index('fzf_output=$(eval "$DTD_LIST_CMD" | fzf ')
    j = DTD.index("_fzf_rc=$?", i)
    assert '2>>"$DTD_FZF_ERR")' in DTD[i:j]
    head = DTD[i - 600:i]
    assert 'DTD_FZF_ERR="$STATE_DIR/dtd-fzf.err"' in head
    assert 'print -r -- "=== $(date' in head and "$DTD_ID" in head


def test_bgrefresh_script_is_cleaned_up_on_exit():
    cleanup = next(l for l in DTD.splitlines() if l.startswith('rm -f "$DTD_FIFO" "$DTD_HDR"') and "$DTD_FORM" in l)
    assert '"$DTD_BGREFRESH"' in cleanup
