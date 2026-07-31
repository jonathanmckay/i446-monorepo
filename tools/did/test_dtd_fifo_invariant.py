#!/usr/bin/env python3
"""Regression/feature (2026-07-31): "have completed all -1n in a block and
it's still only showing 10分 not 13 -- can dtd catch and explain this
itself?"

Root cause (confirmed via /tmp/dtd-*.pushed.log vs .processed counts, twice,
for real -1l completions on 2026-07-31): a rapid-fire alt-enter burst
(sub-1s between presses) can push a line onto $DTD_FIFO that the worker's
`read` never sees -- pushed=3, processed=2, always the LAST item in the
cluster. quick-close.py still closes the Todoist card (a separate
fire-and-forget call, not gated on the worker), so the card vanishes from
dtd looking done while its header stamp and -1₦ credit silently never
happen -- the exact "completed but points short" symptom reported.

Fix: the worker polls with `read -t 2` instead of blocking forever; on every
timeout (idle tick, ~every 2s) it compares $DTD_PUSHED vs $DTD_PROCESSED
line counts. A mismatch means a push was lost -- surfaced immediately via
the header/log and scripts/term-color.sh orange (the existing degraded-state
convention), not silently discovered later as a point shortfall.
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


def test_invariant_block_compares_pushed_and_processed_counts():
    block = _invariant_block()
    assert 'wc -l < "$DTD_PUSHED"' in block
    assert 'wc -l < "$DTD_PROCESSED"' in block
    assert '"$pushed_now" -gt "$processed_now"' in block


def test_invariant_block_dedupes_repeat_alerts():
    """Without a dedup guard, an unresolved loss would re-fire the alert
    every ~2s forever instead of once."""
    block = _invariant_block()
    assert '"$pushed_now" != "$last_alerted"' in block
    assert 'last_alerted="$pushed_now"' in block


def test_invariant_block_sets_degraded_terminal_color():
    """Matches the existing orange = 'tool use failure (non-fatal)'
    convention (e.g. /ate's Ix-unreachable path) rather than inventing a new
    signal."""
    block = _invariant_block()
    assert "term-color.sh\" orange" in block


def test_invariant_block_identifies_which_completions_were_lost():
    block = _invariant_block()
    assert 'tail -n "$missing" "$DTD_PUSHED.log"' in block


# ── Functional: extract and actually execute the timeout-branch logic ─────

def _run_invariant_branch(tmp_path, pushed_lines, processed_lines, pushed_log_lines):
    """Run the exact invariant-check snippet from dtd.sh as a standalone
    script against fabricated $DTD_PUSHED/$DTD_PROCESSED/$DTD_PUSHED.log
    files, bypassing the FIFO/read entirely (that part is exercised by the
    structural tests above; this part exercises the arithmetic + output)."""
    pushed = tmp_path / "pushed"
    processed = tmp_path / "processed"
    pushed_log = tmp_path / "pushed.log"
    hdr = tmp_path / "hdr"
    log = tmp_path / "log"
    pushed.write_text("".join("x\n" for _ in range(pushed_lines)))
    processed.write_text("".join("x\n" for _ in range(processed_lines)))
    pushed_log.write_text("".join(pushed_log_lines))
    hdr.write_text("")
    log.write_text("")

    block = _invariant_block()
    # Strip the `if ! IFS= read ...; then` / trailing `fi` wrapper -- we're
    # unconditionally exercising the "read timed out" body, not the read
    # itself. `last_alerted` starts unset (fresh worker) unless a test seeds it.
    body = block.split("\n", 1)[1]
    body = body.rsplit("\n    fi\n", 1)[0]
    # `body` ends in a bare `continue`, only valid inside a loop -- wrap it in
    # a single-pass `for` so `continue` behaves as "this iteration is done"
    # exactly like it does inside the real worker's `while true` loop.
    script = f"""#!/bin/zsh
DTD_PUSHED={pushed}
DTD_PROCESSED={processed}
DTD_HDR={hdr}
DTD_LOG={log}
HOME={tmp_path}
last_alerted=0
for __once in 1; do
{body}
done
"""
    (tmp_path / "term_stub_dir" / "i446-monorepo" / "scripts").mkdir(parents=True, exist_ok=True)
    stub = tmp_path / "term_stub_dir" / "i446-monorepo" / "scripts" / "term-color.sh"
    stub.write_text("#!/bin/bash\necho \"color:$1\" >> " + str(tmp_path / "color.log") + "\n")
    stub.chmod(0o755)
    script = script.replace(f"HOME={tmp_path}", f"HOME={tmp_path / 'term_stub_dir'}")
    script_path = tmp_path / "invariant.sh"
    script_path.write_text(script)
    subprocess.run(["zsh", str(script_path)], check=True, capture_output=True, text=True)
    return hdr.read_text(), log.read_text()


def test_no_alert_when_pushed_equals_processed(tmp_path):
    hdr, log = _run_invariant_branch(tmp_path, pushed_lines=2, processed_lines=2,
                                     pushed_log_lines=[])
    assert hdr == "" and log == ""


def test_alert_fires_when_pushed_exceeds_processed(tmp_path):
    hdr, log = _run_invariant_branch(
        tmp_path, pushed_lines=3, processed_lines=2,
        pushed_log_lines=["13:46:25\tdone\t6h9fVpX5\t😈 -1ibx\n",
                          "13:46:26\tdone\t6h9fVph7\t😈 -1l\n"])
    assert "INVARIANT" in hdr
    assert "1 completion(s)" in hdr
    assert "😈 -1l" in hdr, "must name the specific lost completion, not just a count"
    assert hdr == log.strip() + "\n" or log.strip() == hdr.strip()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
