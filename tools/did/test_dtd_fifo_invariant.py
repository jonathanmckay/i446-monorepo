#!/usr/bin/env python3
"""Regression/feature (2026-07-31, redesigned 2026-08-01, RE-architected
2026-08-03): "have completed all -1n in a block and it's still only showing
10分 not 13 -- can dtd catch and explain this itself?"

Root cause (confirmed via /tmp/dtd-*.pushed.log vs .processed counts, twice,
for real -1l completions on 2026-07-31): the `done` keybinding's FIFO push
(done.sh: `printf ... > "$FIFO"`) runs inside a short-lived, KILLABLE fzf
`execute` child. A rapid-fire alt-enter burst (sub-1s between presses) tears
that child down mid-write, so the line NEVER reaches the worker's `read` --
pushed=3, processed=2, always the LAST item in the cluster. quick-close.py
still closes the Todoist card (a separate detached call, not gated on the
worker), so the card vanishes from dtd looking done while its header stamp
and -1₦ credit silently never happen -- the exact "completed but points
short" symptom.

Every fix from 2026-07-30 through 2026-08-02 only DETECTED the loss (audit
log, then count-diff, then a real set-difference alert). Detection alone
still forced the work to be redone by hand, and the bug kept recurring. This
version RECOVERS instead of merely alerting:

done.sh appends every requested completion to $DTD_PUSHED.log (atomic
O_APPEND, "ts\\tdone\\tid\\tcontent") BEFORE the racy FIFO push, so that log
-- not the ephemeral FIFO -- is the durable source of truth for "this work
was requested." The worker opens its OWN persistent read-write handle on the
FIFO (`exec 4<>"$DTD_FIFO"`); on every idle 2s `read` timeout it diffs the
durable log against $DTD_PROCESSED_IDS and RE-INJECTS any lost id back onto
the FIFO through that in-process fd, so the loss self-heals through the exact
same processing path within ~2s. did-fast's Todoist close + Neon write are
idempotent, and the id is marked processed the instant it is dequeued, so a
recovered item is attempted exactly once. One item is re-injected per tick to
keep the self-write far under the pipe buffer (no capacity deadlock draining
the worker's own FIFO); the next tick recovers the next.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()
LINES = SRC.splitlines()


def _worker_body() -> str:
    i0 = LINES.index("(")
    i1 = LINES.index(") &", i0)
    return "\n".join(LINES[i0:i1 + 1])


def _timeout_block() -> str:
    m = re.search(
        r"    if ! IFS= read -r -t 2 line; then\n(.*?\n    fi\n)",
        SRC, re.S)
    assert m, "could not find the read-timeout branch in dtd.sh"
    return m.group(0)


# ── Structural ────────────────────────────────────────────────────────────

def test_worker_read_has_a_timeout_not_a_blocking_read():
    """The worker must poll periodically (so an idle-but-lost push is
    reconciled within seconds), not block forever waiting for the next line."""
    assert "if ! IFS= read -r -t 2 line; then" in SRC


def test_fd3_still_keeps_the_fifo_writer_count_above_zero():
    """A failed read must always mean 'timeout', never real EOF -- that
    guarantee comes from fd 3 staying open as an extra writer for the whole
    session, so shutdown stays driven by $DTD_STOP, not EOF."""
    assert 'exec 3>"$DTD_FIFO"' in SRC


def test_worker_owns_a_persistent_reinject_handle_on_the_fifo():
    """Recovery re-injects through a read-write fd the WORKER owns
    (in-process, never a killable fzf `execute` child) -- opened <> so the
    open can't block waiting for a reader (the worker is itself the reader)."""
    body = _worker_body()
    assert 'exec 4<>"$DTD_FIFO"' in body, (
        "the worker must hold its own persistent FIFO writer to re-inject "
        "lost items reliably")


def test_reconcile_computes_a_real_set_difference_against_the_durable_log():
    block = _timeout_block()
    assert 'idsfile="$DTD_PROCESSED_IDS"' in block
    assert '"$DTD_PUSHED.log"' in block
    assert "seen[id] = 1" in block and "!($3 in seen)" in block
    # The old count-diff/tail guess must be fully gone.
    assert "tail -n" not in block


def test_reconcile_reinjects_the_lost_item_it_does_not_merely_alert():
    """The whole point of the re-architecture: a lost id is pushed back onto
    the FIFO (through fd 4) to be actually processed, not just named in an
    alert. Prior versions only wrote a header/log line and moved on."""
    block = _timeout_block()
    assert re.search(r"printf '%s\\t%s\\n' \"\$rid\" \"\$rcontent\" >&4", block), (
        "reconcile must re-inject the lost 'id<TAB>content' line onto the "
        "FIFO via the worker's own fd 4")


def test_reconcile_recovers_at_most_one_item_per_tick():
    """One re-inject per idle tick keeps the self-write far under the pipe
    buffer -- re-injecting an unbounded batch onto the FIFO the worker is
    itself draining could fill the buffer and deadlock on its own write."""
    block = _timeout_block()
    # the inner loop breaks after a single successful re-inject
    assert re.search(r'recovered="\$rcontent"\n\s*break', block), (
        "must break after re-injecting one item, deferring the rest to the "
        "next tick")


def test_reconcile_dedupes_so_it_does_not_reinject_the_same_id_twice():
    """A guard map prevents pushing the same id again in the brief window
    between re-inject and the main loop marking it processed."""
    body = _worker_body()
    assert "typeset -A reinjected" in body
    block = _timeout_block()
    assert '[[ -n "${reinjected[$rid]}" ]] && continue' in block
    # the guard is only set AFTER a successful printf, so a failed write
    # doesn't permanently suppress a retry.
    printf_pos = block.index(">&4")
    guard_pos = block.index("reinjected[$rid]=1")
    assert printf_pos < guard_pos, (
        "must mark the id re-injected only after the write succeeds")


def test_recovery_does_not_drop_an_item_recovered_at_shutdown():
    """If a completion is recovered on the same tick $DTD_STOP appears, the
    worker must drain it before honoring the stop -- otherwise the last
    recovered item is lost at exit."""
    block = _timeout_block()
    stop_pos = block.index('[[ -f "$DTD_STOP" ]] && break')
    recovered_continue = block.index('# Drain the reinjected item')
    assert recovered_continue < stop_pos, (
        "the recovered-then-continue path must come before the stop/break, "
        "so a last-second recovery drains instead of being dropped")


def test_reconcile_sets_degraded_terminal_color():
    """Matches the existing orange = 'tool-use failure (non-fatal)'
    convention rather than inventing a new signal."""
    block = _timeout_block()
    assert "term-color.sh\" orange" in block


def test_detect_only_invariant_language_is_gone():
    """Guard against regressing to a detection-only design: the old
    'never processed' alert and its last_alerted dedup are replaced by
    active recovery."""
    block = _timeout_block()
    assert "last_alerted" not in block
    assert "never processed" not in block


# ── Functional: run the REAL worker body and prove it recovers a lost item ─

def _stub(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return str(p)


def _run_recovery(tmp_path, pushed_log_lines, preprocessed_ids, deliver_via_fifo=()):
    """Start the REAL worker body (extracted verbatim from dtd.sh) with a
    durable $DTD_PUSHED.log seeded with `pushed_log_lines` and
    $DTD_PROCESSED_IDS pre-seeded with `preprocessed_ids`. Optionally deliver
    some lines through the FIFO normally. Nothing is pushed for the "lost"
    ids -- they exist ONLY in the durable log, exactly like a completion
    whose FIFO push was killed mid-write. Returns processed.ids contents and
    the list of task-ids did-fast was actually invoked for."""
    calls = tmp_path / "didfast.calls"
    did_fast = _stub(
        tmp_path, "did_fast_stub.py",
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "argv = sys.argv[1:]\n"
        "tid = argv[1] if len(argv) >= 2 and argv[0] == '--task-id' else argv[-1]\n"
        f"open({str(calls)!r}, 'a').write(tid + '\\n')\n"
        "print(json.dumps({'results':[{'name':'t','step':'x',"
        "'todoist':{'closed':True}}]}))\n")
    undo_fast = _stub(
        tmp_path, "undo_fast_stub.py",
        "#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n")

    # term-color stub so the recovery path's orange signal has no real effect
    term_dir = tmp_path / "fakehome" / "i446-monorepo" / "scripts"
    term_dir.mkdir(parents=True, exist_ok=True)
    (term_dir / "term-color.sh").write_text("#!/bin/bash\nexit 0\n")
    (term_dir / "term-color.sh").chmod(0o755)

    fifo = tmp_path / "fifo"
    hdr = tmp_path / "hdr"
    log = tmp_path / "log"
    pushed = tmp_path / "pushed"
    processed = tmp_path / "processed"
    processed_ids = tmp_path / "processed.ids"
    journal = tmp_path / "journal"
    stop = tmp_path / "stop"

    # Seed the durable log and the processed-ids file from Python so the TAB
    # separators are REAL tabs (a zsh here-string of repr() text would embed
    # literal backslash-t and break awk's -F'\t').
    (Path(str(pushed) + ".log")).write_text("".join(pushed_log_lines))
    processed_ids.write_text("".join(f"{i}\n" for i in preprocessed_ids))
    deliver = "".join(f'printf %s\\\\n {d!r} >&3\n' for d in deliver_via_fifo)

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
HOME={tmp_path / 'fakehome'}
mkfifo "$DTD_FIFO"
: > "$DTD_PROCESSED"; : > "$DTD_LOG"; : > "$DTD_LOG.err"

{body}
WORKER_PID=$!

exec 3>"$DTD_FIFO"
{deliver}sleep 3.5
touch "$DTD_STOP"
exec 3>&-

for _i in $(seq 1 60); do
  kill -0 $WORKER_PID 2>/dev/null || break
  sleep 0.1
done
"""
    script_path = tmp_path / "harness.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)
    subprocess.run(["zsh", str(script_path)], capture_output=True, text=True, timeout=25)
    ids_txt = processed_ids.read_text() if processed_ids.exists() else ""
    calls_txt = calls.read_text() if calls.exists() else ""
    return ids_txt, [c for c in calls_txt.splitlines() if c]


def test_lost_completion_present_only_in_the_durable_log_is_actually_processed(tmp_path):
    """THE bug: a completion done.sh logged but whose FIFO push was lost
    (never delivered) must be recovered and actually run did-fast -- not just
    flagged. Detection-only versions left this permanently unprocessed."""
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=["10:00:00\tdone\tLOSTID\t😈 -1l\n"],
        preprocessed_ids=[])
    assert "LOSTID" in ids_txt.split(), "the lost id must end up marked processed"
    assert "LOSTID" in called, "did-fast must actually be invoked for the recovered id"


def test_recovered_item_is_processed_exactly_once(tmp_path):
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=["10:00:00\tdone\tLOSTID\t😈 -1l\n"],
        preprocessed_ids=[])
    assert ids_txt.split().count("LOSTID") == 1, "no double-marking"
    assert called.count("LOSTID") == 1, "did-fast must run once, not in a loop"


def test_already_processed_pushed_item_is_not_reprocessed(tmp_path):
    """A completion that IS in the durable log AND already recorded in
    processed-ids (normal delivery) must never be re-run by reconcile."""
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=["10:00:00\tdone\tDONEID\talready done\n"],
        preprocessed_ids=["DONEID"])
    assert "DONEID" not in called, "reconcile must not re-run an already-processed item"


def test_recovers_the_lost_one_while_leaving_the_delivered_one_alone(tmp_path):
    """Mixed case: A delivered normally, B lost. B is recovered; A is not
    touched by reconcile (it flowed through the FIFO on its own)."""
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=["10:00:00\tdone\tA\tdelivered\n",
                          "10:00:01\tdone\tB\tlost one\n"],
        preprocessed_ids=["A"],  # A already processed via normal delivery
        deliver_via_fifo=())
    assert "B" in ids_txt.split() and "B" in called, "the lost item must be recovered"
    assert called.count("A") == 0, "the already-delivered item must not be re-run"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
