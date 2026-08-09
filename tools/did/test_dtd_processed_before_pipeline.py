#!/usr/bin/env python3
"""Regression (2026-08-02): "invariant in dtd" — happened TWICE the same day.

First incident: task 6gHVV7fjPwqfvq76 ("i447") was pushed onto $DTD_FIFO,
read, and processed by did-fast.py (proven: its own "already done today"
warning survived to $DTD_LOG.err) — but nothing after that in the same
worker iteration ran: no undo-journal entry, no "? i447" log fallback, no
$DTD_PROCESSED_IDS record. First fix: write $DTD_PROCESSED/$DTD_PROCESSED_IDS
immediately after capturing did-fast's result + exit code, before the
undo-journal pipe and jq extraction.

Second incident, same day, AFTER that fix was live: task 6hC5fV8W3qJxwm3R
("finish 1 g245 before m5x2") proved did-fast ran its ENTIRE pipeline
successfully — real Todoist close, real Neon ledger entry ("T did 1g" at
10:01:19) — yet nothing after it in the loop ran, not even with the first
fix in place. That proves the interruption strikes somewhere in or around
the `result=$(...)` capture itself, EARLIER than "after did-fast returns" —
the first fix's placement was still too late.

Both times, re-running the exact did-fast.py invocation, and separately
piping its exact output shape through undo-fast.py and jq in isolation,
reproduced no hang, crash, or error — the precise interrupting trigger was
never pinned down either time.

Final fix: stop trying to find a safe point "after" anything. Write
$DTD_PROCESSED/$DTD_PROCESSED_IDS the INSTANT a line is dequeued from the
FIFO — before calling did-fast at all. This makes the invariant checker
strictly accurate (it can only ever mean "genuinely never read from the
FIFO"), and makes it structurally impossible for ANY downstream step —
did-fast itself, undo-fast, jq, whatever the unidentified interrupter is —
to make a dequeued line look like a FIFO loss. A silent post-did-fast gap
(this exact class, twice now) becomes a separate, lower-severity symptom:
the real work still lands (proven both times); only the shell's own
confirmation log line goes missing. Also keeps an explicit non-zero-exit/
empty-output branch so a genuine did-fast failure is a visible
"✗ ... (did-fast exit N, no output)" log line instead of silently nothing.
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

def test_processed_ids_write_comes_before_the_did_fast_call_itself():
    """The strongest ordering guarantee: recorded as processed BEFORE
    did-fast is even invoked, not just before its downstream pipeline. The
    2026-08-02 second incident proved "right after did-fast returns" still
    wasn't early enough — the interruption struck in/around the result=$(...)
    capture itself."""
    body = _worker_body()
    line_parse = body.index('task_id="${line%%$\'\\t\'*}"')
    processed_ids_write = body.index('echo "${task_id:-$task_clean}" >> "$DTD_PROCESSED_IDS"')
    did_fast_call = body.index('result=$(python3 "$DID_FAST" --task-id')
    undo_pipe = body.index('python3 "$UNDO_FAST" --journal-done')
    assert line_parse < processed_ids_write < did_fast_call < undo_pipe, (
        "the processed-id must be recorded immediately after the FIFO line "
        "is parsed, BEFORE did-fast is even called — recording it only "
        "after did-fast returns (the first, insufficient fix) still let a "
        "second incident make a delivered, fully-successful completion "
        "look 'never processed'")


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


def test_processed_id_recorded_while_did_fast_is_still_running(tmp_path):
    """Direct proof of the strongest guarantee: the id is marked processed
    BEFORE did-fast is even called, so it's already recorded while did-fast
    is still in flight -- not just "eventually, once did-fast finishes".
    Uses a did-fast stub that sleeps, then checks processed.ids mid-sleep."""
    did_fast_body = (
        "#!/usr/bin/env python3\n"
        "import time\n"
        "time.sleep(2)\n"
        "print('{\"results\": [{\"name\": \"i447\", \"step\": \"0n\", "
        "\"todoist\": {\"closed\": true}}], \"agent_needed\": []}')\n"
    )
    undo_fast_body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    did_fast = _stub(tmp_path, "did_fast_stub.py", did_fast_body)
    undo_fast = _stub(tmp_path, "undo_fast_stub.py", undo_fast_body)

    fifo = tmp_path / "fifo"
    hdr, log = tmp_path / "hdr", tmp_path / "log"
    pushed, processed = tmp_path / "pushed", tmp_path / "processed"
    processed_ids = tmp_path / "processed.ids"
    journal, stop = tmp_path / "journal", tmp_path / "stop"

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
exec 3>"$DTD_FIFO"
printf 'realTaskId123\\ti447\\n' >&3
"""
    script_path = tmp_path / "harness.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)
    proc = subprocess.Popen(["zsh", str(script_path)])
    try:
        time.sleep(0.8)  # did-fast stub is mid-sleep(2) right now
        assert "realTaskId123" in (processed_ids.read_text() if processed_ids.exists() else ""), (
            "the id must already be recorded as processed WHILE did-fast is "
            "still running, proving the write happens before the call, not "
            "merely 'eventually' once it returns")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


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
