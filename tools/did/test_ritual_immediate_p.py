#!/usr/bin/env python3
"""Regression: block-ritual completion (did-fast run_ritual) must NOT write P.

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

These pin the reverted contract: completion stamps the emoji only; P is owned
solely by the daemon's validating reconcile (which re-derives ⏱️/✅ from Toggl/
Todoist each fire, so it self-heals). compute_p stays a pure daemon-side scorer.
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


# ── did-fast: run_ritual credits P by INCREMENT, never recompute ─────────────
# 2026-07-12 redesign: completing a ritual credits its points immediately (the
# user shouldn't wait for the block boundary). It must do so by INCREMENTING
# (`=…+N`), never by recomputing from the header emojis — the recompute path was
# the 2026-07-11 clobber (a header sum missed retrospective ⏱️/✅ and SET P low).

def test_run_ritual_increments_p_immediately():
    src = _func_src(DIDFAST, "run_ritual")
    assert 'cell ("P" &' in src, "run_ritual must write column P (immediate credit)"
    assert "+{pts}" in src, "must append THIS ritual's own points (increment)"


def test_run_ritual_does_not_recompute_p():
    """The increment must not resurrect the clobber: no compute-p shell, no
    P_RESULT parse, no re-summing of header emojis."""
    src = _func_src(DIDFAST, "run_ritual")
    assert "compute-p" not in src, "run_ritual must NOT shell the daemon's compute-p"
    assert "P_RESULT" not in src, "run_ritual must NOT parse a recomputed P total"


def test_run_ritual_documents_reconcile_is_checksum():
    """Self-documenting so the increment isn't mistaken for the authority: the
    daemon reconcile remains the checksum that corrects provisional credit."""
    src = _func_src(DIDFAST, "run_ritual")
    assert "daemon" in src.lower() and "checksum" in src.lower()
