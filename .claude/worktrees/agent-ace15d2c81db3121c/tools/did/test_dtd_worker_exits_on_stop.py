#!/usr/bin/env python3
"""Regression (2026-08-01): "dtd is hanging on the tasks that I close when I
try and ctrl+c close dtd".

Root cause: the 2026-08-01 FIFO-invariant-check redesign turned the worker's
completion loop into `while true; do if ! read -t 2 line; then <check>;
continue; fi; ... done < "$DTD_FIFO"`. `read -t 2` returns nonzero on BOTH a
real 2s idle timeout AND real EOF (all FIFO writers gone) -- zsh gives no way
to tell them apart from the exit status alone. The old code was a plain
`while IFS= read -r line; do ...; done < fifo`, which relied on `read`'s
nonzero return to END the loop on EOF. The redesign's `continue` runs
unconditionally in that branch, so the worker never noticed EOF and looped
forever once the main script did `exec 3>&-` on exit -- and once a FIFO has
zero writers, repeated reads return EOF instantly (no more 2s waits), so this
became a tight infinite loop that never let the background worker's process
exit. dtd's own exit-cleanup does `while kill -0 $WORKER_PID; do sleep 0.2;
done`, which then hung forever waiting for a worker that would never die --
exactly the "hangs on ctrl-c" symptom.

Fix: an explicit $DTD_STOP flag file. Cleanup touches it BEFORE closing fd 3;
the worker's timeout/EOF branch checks for it and `break`s instead of
`continue`s, so it only ever exits once the main script has actually asked
it to (real shutdown), not on every ordinary idle 2s tick.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()
LINES = SRC.splitlines()


def _worker_body() -> str:
    i0 = LINES.index("(")
    i1 = LINES.index(") &", i0)
    return "\n".join(LINES[i0:i1 + 1])


# ── Structural ────────────────────────────────────────────────────────────

def test_dtd_stop_flag_is_declared():
    assert 'DTD_STOP="/tmp/dtd-$DTD_ID.stop"' in SRC


def test_dtd_stop_cleaned_up_at_startup_and_exit():
    startup = SRC[:SRC.index("mkfifo \"$DTD_FIFO\"")]
    assert '"$DTD_STOP"' in startup, "startup sweep must clear a stale stop flag"
    tail = SRC[SRC.rindex("rm -f \"$DTD_FIFO\""):]
    assert '"$DTD_STOP"' in tail, "exit cleanup must remove the stop flag"


def test_worker_breaks_on_stop_flag_instead_of_looping_forever():
    body = _worker_body()
    assert '[[ -f "$DTD_STOP" ]] && break' in body, (
        "the timeout/EOF branch must be able to exit the loop -- an "
        "unconditional `continue` here is what caused the infinite spin")
    # The break check must come BEFORE the unconditional continue, in the
    # same branch, or it can never be reached.
    stop_pos = body.index('[[ -f "$DTD_STOP" ]] && break')
    branch_start = body.index('if ! IFS= read -r -t 2 line; then')
    continue_pos = body.index("continue", stop_pos)
    fi_pos = body.index("\n    fi\n", branch_start)
    assert branch_start < stop_pos < continue_pos < fi_pos, (
        "the stop check must live inside the read-timeout branch, before "
        "its continue")


def test_main_loop_signals_stop_before_closing_fd3():
    # There are two `exec 3>&-` sites in dtd.sh: the main loop's shutdown
    # close and the watcher subshell's own copy-close. Only the FIRST one
    # (the real shutdown) must be preceded by the touch.
    # Several subshells (watcher, ticker) close their own inherited copy of
    # fd 3 earlier in the file -- the real shutdown close is the LAST one,
    # right after the main select loop.
    close_pos = SRC.rindex('exec 3>&-')
    touch_pos = SRC.rindex('touch "$DTD_STOP"', 0, close_pos)
    assert touch_pos < close_pos, (
        "must signal $DTD_STOP before closing fd 3, or the worker can "
        "observe EOF before it knows to break on it")


# ── Functional: run the real worker body against a real FIFO ──────────────

def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def test_worker_process_actually_exits_after_stop_signal(tmp_path):
    """Runs the exact worker loop text from dtd.sh as a real background zsh
    job against a real named pipe, then reproduces the main script's exact
    shutdown sequence (touch stop, close the persistent writer fd) and
    asserts the worker's PID is gone shortly after -- bounded, so a
    regression fails fast instead of hanging this test."""
    did_fast = _stub(tmp_path, "did_fast_stub.py",
                      "#!/usr/bin/env python3\nprint('{\"results\":[]}')\n")
    undo_fast = _stub(tmp_path, "undo_fast_stub.py",
                       "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")

    fifo = tmp_path / "fifo"
    hdr = tmp_path / "hdr"
    log = tmp_path / "log"
    pushed = tmp_path / "pushed"
    processed = tmp_path / "processed"
    processed_ids = tmp_path / "processed.ids"
    journal = tmp_path / "journal"
    stop = tmp_path / "stop"
    result_marker = tmp_path / "worker_exited"

    body = _worker_body()
    script = f"""#!/bin/zsh
DTD_FIFO={fifo}
DTD_HDR={hdr}
DTD_LOG={log}
DTD_PUSHED={pushed}
DTD_PROCESSED={processed}
DTD_PROCESSED_IDS={processed_ids}
DTD_JOURNAL={journal}
DTD_STOP={stop}
DID_FAST={did_fast}
UNDO_FAST={undo_fast}
HOME={tmp_path}
mkfifo "$DTD_FIFO"
: > "$DTD_PUSHED"; : > "$DTD_PROCESSED"; : > "$DTD_PROCESSED_IDS"; : > "$DTD_LOG"

{body}
WORKER_PID=$!

exec 3>"$DTD_FIFO"
printf 'id1\\tfoo\\n' >&3
sleep 0.3

touch "$DTD_STOP"
exec 3>&-

for _i in $(seq 1 40); do
  kill -0 $WORKER_PID 2>/dev/null || break
  sleep 0.1
done

if kill -0 $WORKER_PID 2>/dev/null; then
  echo "HANG"
else
  echo "EXITED" > {result_marker}
  echo "EXITED"
fi
"""
    script_path = tmp_path / "harness.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    start = time.monotonic()
    r = subprocess.run(["zsh", str(script_path)], capture_output=True,
                        text=True, timeout=15)
    elapsed = time.monotonic() - start

    assert "EXITED" in r.stdout, (
        f"worker never exited after the stop signal (hung, matching the "
        f"reported bug) -- stdout={r.stdout!r} stderr={r.stderr!r}")
    assert result_marker.exists()
    assert elapsed < 10, f"worker took too long to notice shutdown: {elapsed:.1f}s"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
