#!/usr/bin/env python3
"""Regression (2026-08-10): the dtd FIFO recovery replayed yesterday's
completions against today.

A dtd session left open across midnight (worker + its durable
$DTD_PUSHED.log both from the previous evening) hit a reconcile tick the
next morning with processed-ids missing entries. The recovery loop had no
concept of WHEN a push was made — pushed.log timestamps were bare HH:MM:SS —
so it replayed the entire previous evening's batch through did-fast, which
only knows how to target the CURRENT day: it closed the new day's recurring
cards (due dates advanced, cards vanished from dtd — "why isn't 1st hci
appearing today?"), marked them in the new day's completed-today, and
re-credited points into the new day's rows (a phantom relax +40).

Fix: pushes are date-stamped (%Y-%m-%dT%H:%M:%S) and the reconcile replays
ONLY same-day pushes. A previous-day loss is alerted once, calmly, in the
log and never replayed (date-less legacy lines count as stale too).
"""
import datetime
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "dtd.sh").read_text()

# Reuse the proven harness from the FIFO-invariant tests (runs the REAL
# worker body extracted from dtd.sh).
sys.path.insert(0, str(HERE))
from test_dtd_fifo_invariant import _run_recovery, _timeout_block  # noqa: E402

TODAY = datetime.date.today().isoformat()
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


# ── Structural ────────────────────────────────────────────────────────────

def test_push_audit_lines_carry_a_full_date():
    """done.sh's durable push line must timestamp with %Y-%m-%d, not bare
    HH:MM:SS — without the date the reconcile cannot tell yesterday's push
    from today's."""
    assert re.search(
        r"printf '%s\\tdone\\t%s\\t%s\\n' \"\\\$\(date \+%Y-%m-%dT%H:%M:%S\)\"", SRC), (
        "pushed.log timestamps must include the date")
    assert 'date +%H:%M:%S' not in SRC.split("PUSHED.log")[0].rsplit("printf", 1)[-1]


def test_reconcile_gates_replay_on_todays_date():
    block = _timeout_block()
    assert 'rec_today=$(date +%Y-%m-%d)' in block
    assert 'today "T"' in block.replace("'", '"') or 'today "T"' in block, (
        "the awk must classify pushes by whether the timestamp starts with "
        "today's date")
    assert '"stale"' in block and '"live"' in block


def test_stale_loss_is_alerted_once_and_never_reinjected():
    block = _timeout_block()
    # stale branch: alert + continue BEFORE the re-inject printf
    stale_pos = block.index('if [[ "$rkind" == "stale" ]]')
    inject_pos = block.index(">&4")
    assert stale_pos < inject_pos, "stale check must guard the re-inject"
    assert "stale_alerted[$rid]=1" in block, "one alert per id, not per tick"
    assert "NOT replayed" in block
    body_start = SRC.index('exec 4<>"$DTD_FIFO"')
    assert "typeset -A reinjected stale_alerted" in SRC[body_start - 200:body_start + 200]


# ── Functional: real worker body ──────────────────────────────────────────

def test_yesterdays_lost_push_is_not_replayed(tmp_path):
    """THE bug: a push from yesterday missing from processed-ids must NOT be
    re-run by the reconcile — did-fast would land it on today."""
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=[f"{YESTERDAY}T20:55:04\tdone\tSTALEID\t1st hci\n"],
        preprocessed_ids=[])
    assert "STALEID" not in called, (
        "a previous-day push must never be replayed against today")
    assert "STALEID" not in ids_txt.split()


def test_stale_loss_is_logged_calmly(tmp_path):
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=[f"{YESTERDAY}T20:55:04\tdone\tSTALEID\t1st hci\n"],
        preprocessed_ids=[])
    log = (tmp_path / "log").read_text() if (tmp_path / "log").exists() else ""
    assert "NOT replayed" in log and "1st hci" in log, (
        "the stale loss must be surfaced in the session log")
    assert log.count("NOT replayed") == 1, "alert once per id, not every 2s tick"


def test_todays_lost_push_still_recovers(tmp_path):
    """The same-day gate must not break the recovery it exists to protect:
    a TODAY push missing from processed-ids is still replayed."""
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=[f"{TODAY}T10:00:00\tdone\tLIVEID\t😈 -1l\n"],
        preprocessed_ids=[])
    assert "LIVEID" in called, "same-day recovery must keep working"
    assert "LIVEID" in ids_txt.split()


def test_dateless_legacy_line_counts_as_stale(tmp_path):
    """Old-format pushed.log lines (bare HH:MM:SS) have unknowable age —
    they must be treated as stale, never replayed."""
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=["10:00:00\tdone\tLEGACYID\told line\n"],
        preprocessed_ids=[])
    assert "LEGACYID" not in called


def test_mixed_stale_and_live_only_live_recovers(tmp_path):
    ids_txt, called = _run_recovery(
        tmp_path,
        pushed_log_lines=[
            f"{YESTERDAY}T20:55:04\tdone\tSTALEID\tyesterday thing\n",
            f"{TODAY}T10:00:00\tdone\tLIVEID\ttoday thing\n",
        ],
        preprocessed_ids=[])
    assert "LIVEID" in called
    assert "STALEID" not in called


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
