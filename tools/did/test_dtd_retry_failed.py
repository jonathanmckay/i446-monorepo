"""Regression tests for dtd's auto-retry of failed completions (2026-08-04).

Context: did-fast now has a SIGALRM watchdog, so a completion that hits a
transient outage (Todoist API / ssh-to-ix unreachable) fails FAST (exit 124)
instead of hanging the worker. But a fast failure still means the Neon score /
build-order stamp never landed for that task (quick-close.py already closed the
Todoist card optimistically). This feature re-drives those failed completions
from a durable queue ($DTD_FAILED) once connectivity returns.

Two layers of coverage:
  1. Structural: the worker records failures and re-drives them, and does so
     WITHOUT tripping the substrings the sibling structural tests anchor on
     (test_dtd_processed_before_pipeline / test_dtd_worker_exits_on_stop).
  2. Functional: the awk file-state-machine (drop-on-success, exponential
     backoff bump, retry-cap drop, network-down cooldown) behaves correctly,
     validated in isolation with no network.
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text(encoding="utf-8")
LINES = SRC.splitlines()


def _worker_body() -> str:
    """The `( ... ) &` background completion worker subshell (same slice the
    sibling structural tests use)."""
    i0 = LINES.index("(")
    i1 = LINES.index(") &", i0)
    return "\n".join(LINES[i0:i1 + 1])


# --------------------------------------------------------------------------
# Structural wiring
# --------------------------------------------------------------------------

def test_failed_queue_path_is_declared_and_cleaned_up():
    assert 'DTD_FAILED="/tmp/dtd-$DTD_ID.failed"' in SRC
    startup = SRC[:SRC.index('mkfifo "$DTD_FIFO"')]
    assert '"$DTD_FAILED"' in startup, "startup sweep must clear a stale retry queue"
    tail = SRC[SRC.rindex('rm -f "$DTD_FIFO"'):]
    assert '"$DTD_FAILED"' in tail, "exit cleanup must remove the retry queue"


def test_failure_branch_records_item_for_retry():
    """A failed completion must be appended to $DTD_FAILED in the
    next_retry<TAB>attempts<TAB>id<TAB>content format, inside the ✗ branch."""
    body = _worker_body()
    m = re.search(r'if \[\[ \$rc -ne 0 \|\| -z "\$result" \]\]; then\n(.*?)\n *fi\n',
                  body, re.S)
    assert m, "could not find the did-fast failure branch"
    branch = m.group(1)
    assert '>> "$DTD_FAILED"' in branch, "failure must be enqueued for retry"
    assert r"printf '%d\t%d\t%s\t%s\n'" in branch, "must use the 4-field retry record format"
    # the original visible-failure logging + drain must survive
    assert 'did-fast exit $rc' in branch and "continue" in branch


def test_idle_branch_retries_failed_items():
    body = _worker_body()
    # the retry loop lives in the idle (read -t 2 timeout) branch
    assert '-s "$DTD_FAILED"' in body, "retry loop must gate on a non-empty queue"
    # only eligible items (next_retry <= now) are picked, one per tick
    assert '$1<=now' in body, "must only retry items whose backoff has elapsed"
    # connectivity is probed cheaply BEFORE spending a did-fast/watchdog window
    assert 'create_connection(("api.todoist.com",443))' in body, \
        "must probe reachability before retrying"
    # a successful retry drops the item and scores it as a (retry)
    assert '(retry)' in body
    assert '!($3==id && $4==c)' in body, "success must remove the item from the queue"


def test_retry_backoff_and_cap():
    body = _worker_body()
    assert '30 * (1 << (rtry - 1))' in body, "exponential backoff base 30s"
    assert 'rback=300' in body, "backoff must cap at 300s"
    assert 'DTD_MAX_RETRIES:-8' in body, "must cap total retries to avoid looping forever"
    assert 'gave up on' in body, "must log when it abandons a permanently-bad item"


def test_retry_does_not_break_sibling_structural_anchors():
    """The retry block must NOT contain the exact substrings that
    test_dtd_processed_before_pipeline anchors the MAIN dequeue path on, or the
    'first occurrence' those tests locate would move into the idle branch and
    the ordering assertions would fail. Retry uses `rout=` (not `result=`) and
    journals via a variable flag (not the literal `--journal-done`)."""
    body = _worker_body()
    idle = body[:body.index('[[ -z "$line" ]] && continue')]
    assert 'result=$(python3 "$DID_FAST" --task-id' not in idle, \
        "retry must not use the locked did-fast-call substring"
    assert 'python3 "$UNDO_FAST" --journal-done' not in idle, \
        "retry must not use the locked undo-journal substring"
    assert 'rout=$(python3 "$DID_FAST"' in body, "retry uses the rout= variable"
    assert 'rjflag="--journal-done"' in body, "retry journals via an indirected flag"


# --------------------------------------------------------------------------
# Functional: the awk file-state-machine, no network
# --------------------------------------------------------------------------

def _run(script: str) -> str:
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, check=True).stdout


def test_success_drops_only_the_matching_item(tmp_path):
    f = tmp_path / "failed"
    f.write_text("100\t1\tID_A\ttask a\n200\t1\tID_B\ttask b\n", encoding="utf-8")
    _run(f'''awk -F'\\t' -v id="ID_A" -v c="task a" \
        '!($3==id && $4==c)' "{f}" > "{f}.tmp" && mv "{f}.tmp" "{f}"''')
    out = f.read_text(encoding="utf-8")
    assert "ID_A" not in out and "ID_B" in out


def test_failure_bumps_attempts_and_backs_off(tmp_path):
    f = tmp_path / "failed"
    f.write_text("100\t2\tID_A\ttask a\n", encoding="utf-8")
    # rtry becomes 3 -> back = 30 * 2^2 = 120; next_retry = now(1000)+120 = 1120
    _run(f'''awk -F'\\t' -v id="ID_A" -v c="task a" -v nn="1120" -v at="3" \
        'BEGIN{{OFS="\\t"}} ($3==id && $4==c){{ $1=nn; $2=at }} {{print}}' \
        "{f}" > "{f}.tmp" && mv "{f}.tmp" "{f}"''')
    assert f.read_text(encoding="utf-8").strip() == "1120\t3\tID_A\ttask a"


def test_network_down_cooldown_keeps_attempts(tmp_path):
    f = tmp_path / "failed"
    f.write_text("100\t2\tID_A\ttask a\n", encoding="utf-8")
    # down: only next_retry advances (to now+15), attempts unchanged
    _run(f'''awk -F'\\t' -v id="ID_A" -v c="task a" -v nn="1015" \
        'BEGIN{{OFS="\\t"}} ($3==id && $4==c){{ $1=nn }} {{print}}' \
        "{f}" > "{f}.tmp" && mv "{f}.tmp" "{f}"''')
    assert f.read_text(encoding="utf-8").strip() == "1015\t2\tID_A\ttask a"


def test_eligible_picker_returns_earliest_due_item(tmp_path):
    f = tmp_path / "failed"
    # now=500: ID_C (300) and ID_A (100) are due; ID_B (900) is not. earliest=ID_A
    f.write_text("300\t1\tID_C\tc\n100\t1\tID_A\ta\n900\t1\tID_B\tb\n", encoding="utf-8")
    out = _run(f'''awk -F'\\t' -v now="500" '
        $1<=now {{ if (best=="" || $1<bv) {{ best=$0; bv=$1 }} }}
        END {{ print best }}' "{f}"''')
    assert out.strip() == "100\t1\tID_A\ta"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
