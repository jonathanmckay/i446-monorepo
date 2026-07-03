#!/usr/bin/env python3
"""Regression: block-ritual points (-1₦, 0分!P) allocate as each ritual is done,
not only at the 2h block boundary.

Change (2026-07-03): previously run_ritual (did-fast) closed the card + stamped
the emoji but deliberately did NOT write P — the daemon's boundary
reconcile_p_for_day was the sole P writer. Now run_ritual credits P immediately
by recomputing from the just-stamped LOCAL build-order.md via the daemon's pure
`compute-p` (trusts stamps → no Toggl/Todoist calls) and SETting col P on Ix.

These pin the wiring: the daemon exposes `compute-p`, compute_p_formula bounds to
fired blocks plus the current one, and run_ritual writes P (and dropped the old
"credited at block turnover" no-op note).
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAEMON = ROOT / "scripts" / "build-order-daemon.py"
DIDFAST = Path(__file__).resolve().parent / "did-fast.py"


def _func_src(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(path.read_text(), node)
    raise AssertionError(f"{name} not found in {path.name}")


# ── daemon: on-demand pure scorer ────────────────────────────────────────────

def test_daemon_has_compute_p_mode():
    src = DAEMON.read_text()
    assert '"compute-p"' in src, "compute-p must be a daemon mode"
    assert "P_RESULT" in src, "compute-p must emit a parseable P_RESULT line"


def test_compute_p_bounds_to_fired_blocks_plus_current():
    src = _func_src(DAEMON, "compute_p_formula")
    # Same fired-block bound as reconcile_p_for_day — never sums future blocks.
    assert "if h <= upto_hour" in src
    # The in-progress block is scored trusting its stamps (no early ⏱️/✅ eval).
    assert "current_block" in src and "live=None" in src


def test_compute_p_is_pure_no_excel_or_api():
    """compute_p_formula must not write Excel or re-validate (that's the daemon's
    boundary job); it only reads stamps so the on-demand path stays instant."""
    src = _func_src(DAEMON, "compute_p_formula")
    assert "neon_set_p" not in src         # no Excel write
    assert "_live_for_block" not in src     # no Toggl/Todoist re-validation


def test_boundary_reconcile_still_validates():
    """The daemon's authoritative boundary reconcile must remain the validating
    self-heal — unchanged (still uses _live_for_block + strips stale markers)."""
    src = _func_src(DAEMON, "reconcile_p_for_day")
    assert "_live_for_block" in src
    assert "_strip_unearned_markers" in src


# ── did-fast: run_ritual now writes P ────────────────────────────────────────

def test_run_ritual_writes_p_immediately():
    src = _func_src(DIDFAST, "run_ritual")
    assert "compute-p" in src, "run_ritual must recompute P via the daemon's compute-p"
    assert "ix_run(" in src, "run_ritual must SET P on Ix via ix_run"
    assert 'cell ("P" &' in src, "run_ritual must write column P for today's row"


def test_run_ritual_dropped_stale_no_op_note():
    src = _func_src(DIDFAST, "run_ritual")
    assert "credited at block turnover by daemon reconcile" not in src, (
        "the old P-not-written note must be gone")


def test_run_ritual_p_write_never_fails_the_ritual():
    """A P-write error must be swallowed — closing the card + stamping is the
    critical path; points are best-effort/self-healing."""
    src = _func_src(DIDFAST, "run_ritual")
    assert "p_reconcile_error" in src and "except Exception" in src
