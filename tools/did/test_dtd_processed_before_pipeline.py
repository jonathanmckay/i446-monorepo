#!/usr/bin/env python3
"""Regression (2026-08-02): "invariant in dtd" — task 6gHVV7fjPwqfvq76 ("i447")
was pushed onto $DTD_FIFO, read, and processed by did-fast.py (proven: its own
"already done today (skipped re-close to avoid recurrence drift)" warning
survived to $DTD_LOG.err) — but NOTHING after that in the same worker
iteration ran: no undo-journal entry, no "? i447" log fallback, no
$DTD_PROCESSED_IDS record. The invariant checker (test_dtd_fifo_invariant.py)
then correctly computed a real set difference and — accurately, given its
inputs — reported "$DTD_FIFO race", even though the FIFO demonstrably
delivered the message; the loss was somewhere AFTER did-fast returned.

Isolated: re-running the exact did-fast.py invocation, and separately piping
its exact output shape through undo-fast.py and jq in isolation, reproduced
no hang, crash, or error — so the precise interrupting trigger (this was a
~16h-old dtd session spanning an overnight sleep/wake) was never pinned down.

Fix: stop trusting that "did-fast returned control to the shell" implies
"the rest of this iteration will run". Write $DTD_PROCESSED/$DTD_PROCESSED_IDS
immediately after capturing did-fast's result + exit code, BEFORE the
undo-journal pipe and jq extraction — so whatever strikes those later steps
can no longer make the id look "never processed" to the invariant checker.
Also add an explicit non-zero-exit/empty-output branch so a genuine did-fast
failure is a visible "✗ ... (did-fast exit N, no output)" log line instead of
silently producing nothing.
"""
import re
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

def test_processed_ids_write_comes_before_the_undo_fast_pipe():
    body = _worker_body()
    did_fast_call = body.index('result=$(python3 "$DID_FAST" --task-id')
    processed_ids_write = body.index('echo "${task_id:-$task_clean}" >> "$DTD_PROCESSED_IDS"')
    undo_pipe = body.index('python3 "$UNDO_FAST" --journal-done')
    assert did_fast_call < processed_ids_write < undo_pipe, (
        "the processed-id must be recorded right after did-fast returns, "
        "before the undo-journal pipe — recording it only at the END of the "
        "iteration (after undo-fast/jq) is exactly what let a post-did-fast "
        "interruption make a delivered completion look 'never processed'")


def test_exit_code_is_captured_and_checked():
    body = _worker_body()
    assert "rc=$?" in body
    assert 'if [[ $rc -ne 0 || -z "$result" ]]; then' in body


def test_failure_branch_logs_visibly_instead_of_falling_through_silently():
    body = _worker_body()
    m = re.search(r'if \[\[ \$rc -ne 0 \|\| -z "\$result" \]\]; then\n(.*?)\n *fi\n', body, re.S)
    assert m, "could not find the did-fast failure branch"
    branch = m.group(1)
    assert 'did-fast exit $rc' in branch
    assert '>> "$DTD_LOG"' in branch
    assert "continue" in branch


# ── Functional: run the real worker body against a real FIFO ──────────────

def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def _run_one_completion(tmp_path, did_fast_body, undo_fast_body):
    """Push exactly one FIFO line through the real worker body (extracted
    verbatim from dtd.sh) with stubbed did-fast/undo-fast, then return the
    resulting processed.ids/log contents."""
    did_fast = _stub(tmp_path, "did_fast_stub.py", did_fast_body)
    undo_fast = _stub(tmp_path, "undo_fast_stub.py", undo_fast_body)

    fifo = tmp_path / "fifo"
    hdr = tmp_path / "hdr"
    log = tmp_path / "log"
    log_err = tmp_path / "log.err"
    pushed = tmp_path / "pushed"
    processed = tmp_path / "processed"
    processed_ids = tmp_path / "processed.ids"
    journal = tmp_path / "journal"
    stop = tmp_path / "stop"

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
: > "$DTD_PUSHED"; : > "$DTD_PROCESSED"; : > "$DTD_PROCESSED_IDS"; : > "$DTD_LOG"; : > "$DTD_LOG.err"

{body}
WORKER_PID=$!

exec 3>"$DTD_FIFO"
printf 'realTaskId123\\ti447\\n' >&3
sleep 0.5

touch "$DTD_STOP"
exec 3>&-

for _i in $(seq 1 40); do
  kill -0 $WORKER_PID 2>/dev/null || break
  sleep 0.1
done
"""
    script_path = tmp_path / "harness.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)
    subprocess.run(["zsh", str(script_path)], capture_output=True, text=True, timeout=15)
    return {
        "processed_ids": processed_ids.read_text() if processed_ids.exists() else "",
        "log": log.read_text() if log.exists() else "",
    }


def test_processed_id_recorded_even_when_undo_fast_crashes_after_did_fast_succeeds(tmp_path):
    """Reproduces the exact observed shape: did-fast completes normally with
    a "future_skipped-only, empty results" response (the "already done
    today" skip), but the downstream undo-fast step fails hard. Before the
    fix, a failure here (or anything else after the did-fast call) meant the
    id never made it into $DTD_PROCESSED_IDS at all."""
    did_fast_body = (
        "#!/usr/bin/env python3\n"
        "print('{\"results\": [], \"agent_needed\": [], "
        "\"future_skipped\": [{\"id\": \"realTaskId123\", \"name\": \"i447\"}]}')\n"
    )
    undo_fast_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n"
    out = _run_one_completion(tmp_path, did_fast_body, undo_fast_body)
    assert "realTaskId123" in out["processed_ids"], (
        "the id must be recorded as processed even though the undo-fast "
        "step that runs AFTER it failed -- this is the exact 2026-08-02 gap")
    assert "? i447" in out["log"]


def test_processed_id_recorded_and_failure_visible_when_did_fast_itself_fails(tmp_path):
    """If did-fast.py itself exits non-zero / produces no stdout, the
    iteration must still record the id as processed (it WAS dequeued and
    attempted) and must leave a visible failure line, not silently nothing."""
    did_fast_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n"
    undo_fast_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    out = _run_one_completion(tmp_path, did_fast_body, undo_fast_body)
    assert "realTaskId123" in out["processed_ids"]
    assert "did-fast exit 3" in out["log"]


def test_normal_success_path_still_logs_ok_and_records_processed(tmp_path):
    """The fix must not break the ordinary, everything-worked path."""
    did_fast_body = (
        "#!/usr/bin/env python3\n"
        "print('{\"results\": [{\"name\": \"i447\", \"step\": \"0n\", "
        "\"todoist\": {\"closed\": true}}], \"agent_needed\": []}')\n"
    )
    undo_fast_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    out = _run_one_completion(tmp_path, did_fast_body, undo_fast_body)
    assert "realTaskId123" in out["processed_ids"]
    assert "✓" in out["log"] and "i447" in out["log"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
