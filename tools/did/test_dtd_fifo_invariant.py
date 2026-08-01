#!/usr/bin/env python3
"""Regression/feature (2026-07-31, redesigned 2026-08-01):
"have completed all -1n in a block and it's still only showing 10分 not
13 -- can dtd catch and explain this itself?"

Root cause (confirmed via /tmp/dtd-*.pushed.log vs .processed counts, twice,
for real -1l completions on 2026-07-31): a rapid-fire alt-enter burst
(sub-1s between presses) can push a line onto $DTD_FIFO that the worker's
`read` never sees -- pushed=3, processed=2, always the LAST item in the
cluster. quick-close.py still closes the Todoist card (a separate
fire-and-forget call, not gated on the worker), so the card vanishes from
dtd looking done while its header stamp and -1₦ credit silently never
happen -- the exact "completed but points short" symptom reported.

First cut (2026-07-31) compared $DTD_PUSHED/$DTD_PROCESSED line COUNTS and
guessed the lost item(s) via `tail -n missing $DTD_PUSHED.log`. Caught live
in production (2026-08-01): that guess is wrong as soon as more than one
item is ever lost in the same session, or $DTD_PUSHED/$DTD_PROCESSED pick up
unrelated increments from ctrl-d defer's own async per-item workers (which
legitimately sit "pushed > processed" for seconds mid-network-round-trip --
not a loss). It kept re-citing already-✓'d completions (e.g. "-1ibx",
"i447") as lost while never once correctly naming the genuinely stuck ones.

Redesign: $DTD_PUSHED.log (written only by done.sh, "ts\\tdone\\tid\\tcontent")
and $DTD_PROCESSED_IDS (written only by the main worker loop, one task_id
per line) are used to compute a REAL set difference -- which specific
pushed ids have no matching processed-id line -- instead of guessing from
counts. Neither file is touched by defer, so defer's async in-flight state
can no longer produce a false positive.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()


def _invariant_block() -> str:
    m = re.search(
        r"    if ! IFS= read -r -t 2 line; then\n(.*?\n    fi\n)",
        SRC, re.S)
    assert m, "could not find the read-timeout invariant block in dtd.sh"
    return m.group(0)


# ── Structural ────────────────────────────────────────────────────────────

def test_worker_read_has_a_timeout_not_a_blocking_read():
    """The worker must poll periodically (so an idle-but-lost push is
    noticed within seconds), not block forever waiting for the next line."""
    assert "if ! IFS= read -r -t 2 line; then" in SRC


def test_fd3_still_keeps_the_fifo_writer_count_above_zero():
    """A failed read must always mean 'timeout', never real EOF -- that
    guarantee comes from fd 3 staying open as an extra writer for the whole
    session. If this ever goes away, the timeout branch's EOF assumption
    (see its own comment) breaks silently."""
    assert 'exec 3>"$DTD_FIFO"' in SRC


def test_invariant_uses_a_dedicated_processed_ids_file_not_the_shared_counter():
    """$DTD_PUSHED/$DTD_PROCESSED are shared with ctrl-d defer's own async
    workers -- using them for this check produced a false positive on a
    merely in-flight defer. The dedicated files are written only by
    done.sh/the main worker loop."""
    assert 'DTD_PROCESSED_IDS="/tmp/dtd-$DTD_ID.processed.ids"' in SRC
    assert 'echo "${task_id:-$task_clean}" >> "$DTD_PROCESSED_IDS"' in SRC


def test_invariant_block_computes_a_real_set_difference():
    block = _invariant_block()
    assert 'idsfile="$DTD_PROCESSED_IDS"' in block
    assert '"$DTD_PUSHED.log"' in block
    assert "seen[id] = 1" in block and "!($3 in seen)" in block
    # The old, buggy tail-based guess must be fully gone, not just supplemented.
    assert "tail -n" not in block


def test_invariant_block_dedupes_repeat_alerts():
    """Without a dedup guard, an unresolved loss would re-fire the alert
    every ~2s forever instead of once. Dedup key is the actual lost-set
    string (not a raw count), so a genuinely NEW loss re-alerts even if the
    total count happens to coincide with a previous alert."""
    block = _invariant_block()
    assert '"$lost" != "$last_alerted"' in block
    assert 'last_alerted="$lost"' in block


def test_invariant_block_sets_degraded_terminal_color():
    """Matches the existing orange = 'tool use failure (non-fatal)'
    convention (e.g. /ate's Ix-unreachable path) rather than inventing a new
    signal."""
    block = _invariant_block()
    assert "term-color.sh\" orange" in block


# ── Functional: extract and actually execute the timeout-branch logic ─────

def _run_invariant_branch(tmp_path, pushed_log_lines, processed_ids):
    """Run the exact invariant-check snippet from dtd.sh as a standalone
    script against fabricated $DTD_PUSHED.log/$DTD_PROCESSED_IDS files,
    bypassing the FIFO/read entirely (that part is exercised by the
    structural tests above; this part exercises the set-difference + output)."""
    pushed_log = tmp_path / "pushed.log"
    processed_ids_file = tmp_path / "processed.ids"
    hdr = tmp_path / "hdr"
    log = tmp_path / "log"
    pushed_log.write_text("".join(pushed_log_lines))
    processed_ids_file.write_text("".join(f"{i}\n" for i in processed_ids))
    hdr.write_text("")
    log.write_text("")

    block = _invariant_block()
    # `body` ends in a bare `continue`, only valid inside a loop -- wrap it in
    # a single-pass `for` so `continue` behaves as "this iteration is done"
    # exactly like it does inside the real worker's `while true` loop.
    body = block.split("\n", 1)[1]
    body = body.rsplit("\n    fi\n", 1)[0]

    term_dir = tmp_path / "term_stub_dir" / "i446-monorepo" / "scripts"
    term_dir.mkdir(parents=True, exist_ok=True)
    stub = term_dir / "term-color.sh"
    stub.write_text("#!/bin/bash\necho \"color:$1\" >> " + str(tmp_path / "color.log") + "\n")
    stub.chmod(0o755)

    script = f"""#!/bin/zsh
DTD_PUSHED_LOG_FILE={pushed_log}
DTD_PROCESSED_IDS={processed_ids_file}
DTD_HDR={hdr}
DTD_LOG={log}
HOME={tmp_path / 'term_stub_dir'}
last_alerted=""
for __once in 1; do
{body.replace('"$DTD_PUSHED.log"', '"$DTD_PUSHED_LOG_FILE"')}
done
"""
    script_path = tmp_path / "invariant.sh"
    script_path.write_text(script)
    subprocess.run(["zsh", str(script_path)], check=True, capture_output=True, text=True)
    return hdr.read_text(), log.read_text()


def test_no_alert_when_everything_pushed_was_processed(tmp_path):
    hdr, log = _run_invariant_branch(
        tmp_path,
        pushed_log_lines=["13:46:25\tdone\t6h9fVpX5\t😈 -1ibx\n"],
        processed_ids=["6h9fVpX5"])
    assert hdr == "" and log == ""


def test_alert_fires_for_the_one_genuinely_unprocessed_item(tmp_path):
    hdr, log = _run_invariant_branch(
        tmp_path,
        pushed_log_lines=["13:46:25\tdone\t6h9fVpX5\t😈 -1ibx\n",
                          "13:46:26\tdone\t6h9fVph7\t😈 -1l\n"],
        processed_ids=["6h9fVpX5"])  # -1ibx processed, -1l is not
    assert "INVARIANT" in hdr
    assert "1 completion(s)" in hdr
    assert "😈 -1l" in hdr
    assert "😈 -1ibx" not in hdr, "must not blame an already-processed completion"


def test_alert_correctly_identifies_a_middle_item_lost_not_just_the_tail(tmp_path):
    """This is the exact live bug (2026-08-01): three pushed, the MIDDLE one
    lost, the two after it processed fine. A tail-based guess would wrongly
    blame the two most recent (already-successful) pushes instead."""
    hdr, log = _run_invariant_branch(
        tmp_path,
        pushed_log_lines=["10:00:00\tdone\tA\tfirst\n",
                          "10:00:01\tdone\tB\tsecond (the lost one)\n",
                          "10:00:02\tdone\tC\tthird\n"],
        processed_ids=["A", "C"])  # B never processed
    assert "1 completion(s)" in hdr
    assert "second (the lost one)" in hdr
    assert "first" not in hdr and "third" not in hdr


def test_repeat_ticks_with_the_same_unresolved_loss_dont_respam(tmp_path):
    hdr1, log1 = _run_invariant_branch(
        tmp_path, pushed_log_lines=["10:00:00\tdone\tA\tfoo\n"], processed_ids=[])
    assert "INVARIANT" in hdr1
    # A second tick with the SAME still-unprocessed set must not re-append
    # to the log (this is exercised at the shell-snippet level per-call, so
    # here we just confirm the single-call output is well-formed; the
    # cross-call dedup itself is covered by the structural
    # test_invariant_block_dedupes_repeat_alerts, since last_alerted only
    # persists across iterations of the SAME long-lived worker process).
    assert log1.strip() == hdr1.strip()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
