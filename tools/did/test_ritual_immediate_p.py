#!/usr/bin/env python3
"""Regression: did-fast's own 0分!P credit must never regress the live total.

History:
- Original design: run_ritual closed the card + stamped the emoji, and the
  daemon's boundary reconcile_p_for_day was the SOLE P writer.
- 2026-07-03: an "immediate P" feature had run_ritual recompute P from the
  just-stamped LOCAL build-order.md via the daemon's `compute-p` and SET col P
  on Ix, so points landed as each ritual was done.
- 2026-07-11: that feature was REVERTED — it was actively destructive. The
  retrospective auto markers ⏱️ (-1t) / ✅ (-1l) are written by the daemon onto
  Ix's build order and are absent from the Straylight copy a completion reads
  (and get stripped off Ix by Syncthing between fires), so compute-p's header
  sum excludes them and OVERWRITES the daemon's correct P. Observed: the 20:00
  reconcile set P=19; a 20:04 /-1g recomputed P=14, dropping 5 earned points.
- 2026-07-12: reinstated as a pure INCREMENT (`=…+N`), never a recompute —
  provably can't clobber, but positional append/merge can't tell "the
  previous block" (⏱️/✅'s target since the same day's redesign) apart from
  "the current block's own later term", so P could fragment into multiple
  terms for one block (observed: `=0+6+3+10+7+3`, 巳 stuck at 10 of 13).
- 2026-07-13 (same day): recompute is back, but GUARDED — did-fast still
  calls `neon_blocks.score_day` (one term per stamped block), but only uses
  the result if its total is >= the freshly-read LIVE P total; otherwise it
  falls back to the 2026-07-12 append. This keeps the never-decreases
  invariant the 2026-07-11 incident needed, while actually fixing the
  fragmentation the pure-increment approach couldn't.

compute_p_formula (the daemon's OWN pure scorer, shelled via `compute-p`)
stays untouched by all of this — did-fast never calls it; the guard above is
did-fast's own in-process score_day() + a live-value comparison.
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


# ── daemon: pure scorer still exists (used only daemon-side, never by completion)

def test_daemon_still_has_compute_p_mode():
    src = DAEMON.read_text()
    assert '"compute-p"' in src, "compute-p remains a daemon mode"
    assert "P_RESULT" in src, "compute-p still emits a parseable P_RESULT line"


def test_compute_p_is_pure_no_excel_or_api():
    """compute_p_formula must not write Excel or re-validate — it only reads
    stamps, so it stays instant and side-effect-free."""
    src = _func_src(DAEMON, "compute_p_formula")
    assert "neon_set_p" not in src          # no Excel write
    assert "_live_for_block" not in src      # no Toggl/Todoist re-validation


def test_boundary_reconcile_is_the_sole_validating_p_writer():
    """The daemon's boundary reconcile must remain the validating self-heal —
    it re-derives ⏱️/✅ from live Toggl/Todoist and strips stale markers."""
    src = _func_src(DAEMON, "reconcile_p_for_day")
    assert "_live_for_block" in src
    assert "_strip_unearned_markers" in src


# ── did-fast: run_ritual credits P immediately, guarded against regression ──
# 2026-07-13: run_ritual prefers a recompute (one term per block) but only
# applies it when its total is >= the live P value read fresh from Excel —
# see test_did_ritual_manual_or_auto.py for the full regression coverage of
# the guard itself and the fragmentation bug it fixes.

def test_run_ritual_credits_p_immediately():
    src = _func_src(DIDFAST, "run_ritual")
    assert 'cell ("P" &' in src, "run_ritual must write column P (immediate credit)"
    assert "computed_total >= live_total" in src, (
        "must guard the recompute against regressing the live P total")


def test_run_ritual_never_shells_the_daemons_compute_p_cli():
    """did-fast's own recompute (neon_blocks.score_day, in-process) must not
    be confused with shelling the daemon's `compute-p` subprocess mode — that
    was never part of either the 2026-07-12 or 2026-07-13 design."""
    src = _func_src(DIDFAST, "run_ritual")
    assert '"compute-p"' not in src, "run_ritual must NOT shell the daemon's compute-p"
    assert "P_RESULT" not in src, "run_ritual must NOT parse a daemon compute-p result"


def test_run_ritual_documents_reconcile_is_checksum():
    """Self-documenting so the guarded recompute isn't mistaken for replacing
    the daemon: its boundary reconcile remains the periodic validating
    checksum (re-deriving ⏱️/✅ from live Toggl/Todoist), independent of
    did-fast's own best-effort immediate credit."""
    src = _func_src(DIDFAST, "run_ritual")
    assert "daemon" in src.lower() and "checksum" in src.lower()
