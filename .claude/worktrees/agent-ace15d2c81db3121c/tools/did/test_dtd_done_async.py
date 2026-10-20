#!/usr/bin/env python3
"""Regression (2026-08-01): "can you figure out and fix the dtd invariant?"
-- the actual FIFO-loss root cause, not just the detector built earlier.

Root cause, confirmed via fzf's own man page (`man fzf`, execute-silent):
"fzf will not be responsive until the command is complete. For asynchronous
execution, start your command as a background process (i.e. appending &)."
done.sh (bound to alt-enter via execute-silent) was NOT backgrounded, so a
rapid second alt-enter landing while fzf was still blocked processing the
FIRST done.sh invocation was silently lost -- never reaching $DTD_FIFO at
all. Ruled out the pipe/reader itself first: stress-tested dtd's exact
`exec 3>fifo` + `while true; read -t 2; done < fifo` construct with 100
concurrent single-line writers (including UTF-8 emoji payloads matching real
completions) and got zero loss every time.

Fix: split done.sh's fast, order-critical optimistic hide (writes the fzf
row id to $REMOVED.ids so reload() sees it disappear immediately -- no
python3 needed, just the raw id) into a tiny standalone script,
$DTD_DONE_HIDE. The router now runs that synchronously (near-instant) then
backgrounds the FULL done.sh (resolve, quick-close, FIFO push, tty-drain)
for the no-prompt (execute-silent) case, so fzf regains responsiveness in
milliseconds instead of blocking through done.sh's whole tail. done.sh
itself is UNCHANGED and still does its own copy of the hide when it runs a
moment later -- a duplicate id line in a set-membership file is a no-op.

The value-prompt habits (cpap/xk20/xk22/xk26/i444/hiit/...) are NOT touched
-- they still route through execute (foreground, needs a real tty for the
prompt) and must stay fully synchronous; backgrounding a prompt would break
it outright.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()


def _router_body() -> str:
    m = re.search(
        r"cat > \"\$DTD_DONE_ROUTER\" << ROUTEREOF\n(.*?)\nROUTEREOF",
        SRC, re.S)
    assert m, "could not find the DTD_DONE_ROUTER heredoc in dtd.sh"
    return m.group(1)


def _hide_body() -> str:
    m = re.search(
        r"cat > \"\$DTD_DONE_HIDE\" << HIDEEOF\n(.*?)\nHIDEEOF",
        SRC, re.S)
    assert m, "could not find the DTD_DONE_HIDE heredoc in dtd.sh"
    return m.group(1)


# ── Structural ────────────────────────────────────────────────────────────

def test_no_prompt_branch_backgrounds_done_sh():
    """The execute-silent (no-prompt) branch must background the full
    done.sh invocation -- this is the actual fix; without the trailing '&'
    fzf stays blocked for done.sh's whole synchronous tail again."""
    body = _router_body()
    m = re.search(r'printf \'execute-silent\(([^\']*)\)\'', body)
    assert m, "no-prompt branch must still emit an execute-silent(...) action"
    action = m.group(1)
    assert action.rstrip().endswith("&"), \
        f"done.sh must be backgrounded in the no-prompt path, got: {action!r}"


def test_no_prompt_branch_runs_the_fast_hide_first():
    """The hide must run BEFORE done.sh is backgrounded (synchronously, so
    reload() -- which fires right after execute-silent returns -- sees the
    item already hidden)."""
    body = _router_body()
    line = next(l for l in body.splitlines() if "execute-silent(" in l)
    hide_pos = line.find('"$DTD_DONE_HIDE"')
    done_pos = line.find('"$DTD_DONE"')
    assert hide_pos != -1 and done_pos != -1, \
        f"router line must reference both scripts by their outer vars: {line!r}"
    assert hide_pos < done_pos, "the fast hide must be positioned before done.sh in the command"


def test_prompt_branch_is_unchanged_still_fully_synchronous():
    """Prompt-needing habits (cpap/xk20/...) must NOT be backgrounded --
    they need a live tty for the interactive prompt, which cannot run
    detached from fzf."""
    body = _router_body()
    m = re.search(r'printf \'execute\(([^\']*)\)\'', body)
    assert m, "prompt branch must still emit a plain execute(...) action"
    action = m.group(1)
    assert "&" not in action, \
        f"prompt-needing habits must stay synchronous, got: {action!r}"
    assert "DONE_HIDE" not in action, \
        "prompt path doesn't need the fast-hide split -- it's already synchronous"


def test_hide_script_only_touches_removed_ids_by_id():
    body = _hide_body()
    assert 'echo "\\$1" >> "\\$REMOVED.ids"' in body
    # Must NOT attempt any python3 resolve -- the whole point is to avoid it.
    assert "python3" not in body
    assert "DTD_RESOLVE" not in body


def test_done_sh_itself_is_unmodified_by_the_split():
    """done.sh must still do its own (now-redundant-but-harmless) hide,
    quick-close, FIFO push, and tty-drain exactly as before -- the fix is
    purely in HOW it gets invoked (backgrounded), not in its own body."""
    assert 'printf \'%s\\t%s\\n\' "\\$1" "\\$clean" > "\\$FIFO"' in SRC
    assert "quick-close.py" in SRC


# ── Functional: the hide script actually does the right thing ─────────────

def _write_hide_script(tmp_path, dtd_removed_path):
    """The extracted heredoc body references the OUTER script's $DTD_REMOVED
    (expanded at heredoc-write-time in the real dtd.sh, since HIDEEOF is
    unquoted) -- provide it as an env var here rather than re-declaring
    REMOVED= ourselves, so we're testing the exact source text, not a
    paraphrase of it. The body's `\\$1`/`\\$REMOVED` are escaped in dtd.sh's
    SOURCE so the OUTER cat-heredoc doesn't expand them at write-time; when
    dtd.sh actually runs, that escaping is consumed and the generated file
    has plain $1/$REMOVED. Replicate that un-escaping here too, or the
    extracted text isn't valid standalone zsh (a bare `\\$1` is a literal
    '$1' string, not a variable reference)."""
    script_path = tmp_path / "hide.sh"
    script_path.write_text(_hide_body().replace("\\$", "$") + "\n")
    script_path.chmod(0o755)
    return script_path


def test_hide_script_writes_id_and_exits_fast(tmp_path):
    removed = tmp_path / "removed"
    script_path = _write_hide_script(tmp_path, removed)
    import os, time
    env = {**os.environ, "DTD_REMOVED": str(removed)}
    start = time.monotonic()
    subprocess.run(["zsh", str(script_path), "task123"], check=True,
                   capture_output=True, text=True, timeout=5, env=env)
    elapsed = time.monotonic() - start
    assert (removed.with_suffix(".ids")).read_text().strip() == "task123"
    assert elapsed < 1.0, f"hide script must be near-instant (no python3), took {elapsed:.2f}s"


def test_hide_script_noop_when_id_empty(tmp_path):
    removed = tmp_path / "removed"
    script_path = _write_hide_script(tmp_path, removed)
    import os
    env = {**os.environ, "DTD_REMOVED": str(removed)}
    subprocess.run(["zsh", str(script_path)], check=True,
                   capture_output=True, text=True, timeout=5, env=env)
    assert not (removed.with_suffix(".ids")).exists()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
