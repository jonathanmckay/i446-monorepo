#!/usr/bin/env python3
"""Regression (2026-10-02): dtd completions were slow because every ritual /
d359-met close ran a BLOCKING refresh_task_queue() (~4s) inside did-fast,
on dtd's strictly serial FIFO worker. Measured from the neon ledger: a ritual
landed 5-9s after the keypress, and the next queued close 19s.

Fix: did-fast skips that inline refresh when DIDFAST_DEFER_REFRESH=1
(_defer_refresh()); dtd's worker exports the flag, notes that a refresh is
owed after a ritual / d359 result, and fires ONE backgrounded
--refresh-cache when the FIFO goes idle (its 2s read timeout is the
debounce). Every other caller keeps the inline refresh, and the explicit
--refresh-cache CLI is never gated.
"""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"
SRC = DID_FAST.read_text()
DTD = (HERE / "dtd.sh").read_text()


def _load_did_fast():
    spec = importlib.util.spec_from_file_location("did_fast_defer_test", DID_FAST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass field resolution needs this before exec
    spec.loader.exec_module(mod)
    return mod


# --- did-fast.py -----------------------------------------------------------

def test_defer_flag_reads_env(monkeypatch):
    m = _load_did_fast()
    monkeypatch.delenv("DIDFAST_DEFER_REFRESH", raising=False)
    assert m._defer_refresh() is False
    monkeypatch.setenv("DIDFAST_DEFER_REFRESH", "1")
    assert m._defer_refresh() is True
    monkeypatch.setenv("DIDFAST_DEFER_REFRESH", "0")
    assert m._defer_refresh() is False


def test_both_in_completion_refreshes_are_gated():
    assert "if ritual_entries and not _defer_refresh():" in SRC
    assert "if d359_met_entries and not _defer_refresh():" in SRC
    # no ungated variant may creep back in
    assert not re.search(r"^\s+if ritual_entries:\s*$", SRC, re.M)
    assert not re.search(r"^\s+if d359_met_entries:\s*$", SRC, re.M)


def test_explicit_refresh_cli_is_never_gated():
    """`did-fast.py --refresh-cache` is the worker's (and /0g's) explicit
    'refresh now'; gating it on the same env var would make the deferred
    refresh a no-op inside the worker that exports the flag."""
    i = SRC.index('if sys.argv[1] == "--refresh-cache":')
    branch = SRC[i:i + 400]
    assert "refresh_task_queue(block=True)" in branch
    assert "_defer_refresh" not in branch


# --- dtd.sh worker -----------------------------------------------------------

def _worker_block() -> str:
    i = DTD.index('  exec 4<>"$DTD_FIFO"')
    j = DTD.index("WORKER_PID=$!", i) if "WORKER_PID=$!" in DTD[i:] else len(DTD)
    return DTD[i:j]


def test_worker_exports_defer_flag_before_loop():
    w = _worker_block()
    assert "export DIDFAST_DEFER_REFRESH=1" in w
    assert w.index("export DIDFAST_DEFER_REFRESH=1") < w.index("while true; do")


def test_worker_marks_refresh_owed_on_ritual_or_d359_result():
    w = _worker_block()
    m = re.search(r"jq -e '\.results\[\]\? \| select\(\.step == \"ritual\" or \.d359 != null\)'", w)
    assert m, "worker must detect ritual / d359 results to know a refresh is owed"
    assert "_pending_refresh=1" in w[m.start():m.start() + 300]


def test_worker_fires_one_backgrounded_refresh_on_idle():
    w = _worker_block()
    i = w.index("if (( ${#_qlines} <= _qcur )); then")
    idle = w[i:i + 400]
    assert '[[ -n "$_pending_refresh" ]]' in idle, "refresh must be gated on the owed flag"
    assert '_pending_refresh=""' in idle, "flag must be cleared so it fires once per burst"
    assert re.search(r'\( python3 "\$DID_FAST" --refresh-cache [^\n]*\) &', idle), (
        "the idle refresh must be backgrounded: a blocking one would stall the "
        "worker exactly like the inline refresh it replaces")


def test_idle_refresh_runs_before_shutdown_break():
    """cleanup sets $DTD_STOP and the idle branch breaks on it; an owed
    refresh must fire before that break or a session that ends right after a
    ritual close never refreshes the cache."""
    w = _worker_block()
    i = w.index("if (( ${#_qlines} <= _qcur )); then")
    assert w.index('[[ -n "$_pending_refresh" ]]', i) < w.index('[[ -f "$DTD_STOP" ]] && break', i)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
