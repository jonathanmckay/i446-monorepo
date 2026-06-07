"""Regression tests for build-order-daemon.py.

Bug: neon_add_12_to_y logged success (Y_ADD) even when Excel was not open,
because osascript returned a cached/stale result. The write silently failed,
leaving the -1₦ cell empty.

Fix: Added a read-back verification step that calls neon_read_y after writing
and logs VERIFY_FAILED if the cell is empty/zero.
"""
from pathlib import Path

DAEMON = Path(__file__).parent / "build-order-daemon.py"


def test_add_12_has_readback_verification():
    """neon_add_12_to_y must verify the write by reading back the cell value."""
    src = DAEMON.read_text(encoding="utf-8")
    # Find the function
    idx = src.index("def neon_add_12_to_y")
    # Find the next function definition
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "neon_read_y" in func_body, (
        "neon_add_12_to_y must call neon_read_y to verify the write landed"
    )
    assert "VERIFY_FAILED" in func_body, (
        "neon_add_12_to_y must return VERIFY_FAILED if read-back shows empty/zero"
    )


def test_add_12_logs_verify_result():
    """The verification must be logged so failures are visible in the daemon log."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def neon_add_12_to_y")
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "verified=" in func_body, (
        "neon_add_12_to_y must log the verified value on success"
    )


def test_osascript_error_returns_failed():
    """When osascript returns non-zero, neon_add_12_to_y must return FAILED."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def neon_add_12_to_y")
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "FAILED" in func_body, (
        "neon_add_12_to_y must return FAILED on osascript error"
    )
    assert "returncode" in func_body, (
        "neon_add_12_to_y must check osascript returncode"
    )


def test_archive_calls_enrich_before_snapshot():
    """
    Bug: archive daemon saved an un-enriched build order because
    build-order-enrich.py only ran on Straylight while the archive
    daemon ran on ix. Even after migrating both to ix, the archive
    function never called enrich before snapshotting.

    Fix: run_archive() calls build-order-enrich.py as Step 0a,
    before link-meetings (Step 0b) and the archive write (Step 1).
    """
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def run_archive")
    # Find Step 1 (the archive write)
    step1_idx = src.index("Step 1: write archive", idx)
    pre_archive = src[idx:step1_idx]
    assert "enrich" in pre_archive.lower(), (
        "run_archive must call build-order-enrich.py before writing the archive snapshot"
    )
    assert "enrich_script" in pre_archive or "build-order-enrich" in pre_archive, (
        "run_archive must reference build-order-enrich.py explicitly"
    )


def test_enrich_runs_before_link_meetings_in_archive():
    """Enrichment (time entries) must run before link-meetings (d357 links)
    so that meeting links can be inlined onto time entries."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def run_archive")
    enrich_pos = src.index("enrich", idx)
    link_pos = src.index("run_link_meetings", idx)
    assert enrich_pos < link_pos, (
        "Enrichment (Step 0a) must come before link-meetings (Step 0b) in run_archive"
    )



def test_neon_template_uses_named_workbook():
    """
    Bug: NEON_FIND_ROW_TEMPLATE used active workbook instead of the
    named workbook. If another Excel file was frontmost, the lock/mark
    operations would hit the wrong workbook or fail silently, causing
    block cells to never get frozen.

    Fix: Template must reference workbook by name.
    """
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("NEON_FIND_ROW_TEMPLATE")
    template_end = src.index("'''", idx + 30)
    template = src[idx:template_end]
    assert "active workbook" not in template, (
        "NEON_FIND_ROW_TEMPLATE must NOT use active workbook"
    )
    assert "Neon" in template, (
        "NEON_FIND_ROW_TEMPLATE must reference Neon workbook by name"
    )


# ── Stale-marker scoring guard ──────────────────────────────────────────────
# Bug: a block earned 3 pts for a 🎯 (goals-set) marker left over from a prior
# day even though no goals were set today, because score_block_from_emojis
# trusted any emoji on the header. Fix: scoring validates each daemon-owned
# marker against the run's live results via _marker_earned; ☀️/📧 (written by
# /inbound, no daemon validator) are still trusted on presence.
import importlib.util


def _load_daemon():
    spec = importlib.util.spec_from_file_location("build_order_daemon", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_marker_earned_rejects_stale_daemon_marker():
    mod = _load_daemon()
    line = "- 巳 🎯 ⏰"
    # Live says no goals today → stale 🎯 must not earn
    assert mod._marker_earned("🎯", line, {"🎯": False}) is False
    # Live confirms goals → earns
    assert mod._marker_earned("🎯", line, {"🎯": True}) is True


def test_marker_earned_trusts_inbound_markers():
    mod = _load_daemon()
    line = "- 巳 ☀️ 📧"
    # /inbound markers have no daemon validator; presence alone earns even when
    # live has no opinion on them
    assert mod._marker_earned("☀️", line, {"🎯": False}) is True
    assert mod._marker_earned("📧", line, {"🎯": False}) is True
    # Absent marker never earns
    assert mod._marker_earned("✅", line, {}) is False


def test_marker_earned_legacy_trust_when_live_none():
    mod = _load_daemon()
    line = "- 巳 🎯"
    # No live results → preserve legacy trust-the-header behavior
    assert mod._marker_earned("🎯", line, None) is True


def test_score_block_ignores_stale_goal_marker(tmp_path, monkeypatch):
    mod = _load_daemon()
    build = tmp_path / "build.md"
    # ⏰ (fired stamp) is written in Phase 3, after scoring, so the header has
    # only the sub-habit markers at score time.
    build.write_text(
        "## -1₲\n\n"
        "- 巳 🎯\n"
        "    - [ ] \n"          # empty goal: 🎯 is stale
        "- 午 ✅\n"
        "    - [ ] real goal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    # Live results for 巳: no goals, no toggl, no todoist → score 0 despite 🎯
    assert mod.score_block_from_emojis("巳", live={"🎯": False, "⏱️": False, "✅": False}) == 0
    # With live confirming goals, the 🎯 earns its 3
    assert mod.score_block_from_emojis("巳", live={"🎯": True, "⏱️": False, "✅": False}) == 3
