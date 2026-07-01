"""Regression tests for build-order-daemon.py.

Bug: neon_add_score_to_p logged success (Y_ADD) even when Excel was not open,
because osascript returned a cached/stale result. The write silently failed,
leaving the -1₦ cell empty.

Fix: Added a read-back verification step that calls neon_read_y after writing
and logs VERIFY_FAILED if the cell is empty/zero.
"""
from pathlib import Path

DAEMON = Path(__file__).parent / "build-order-daemon.py"


def test_add_12_has_readback_verification():
    """neon_add_score_to_p must verify the write by reading back the cell value."""
    src = DAEMON.read_text(encoding="utf-8")
    # Find the function
    idx = src.index("def neon_add_score_to_p")
    # Find the next function definition
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "neon_read_y" in func_body, (
        "neon_add_score_to_p must call neon_read_y to verify the write landed"
    )
    assert "VERIFY_FAILED" in func_body, (
        "neon_add_score_to_p must return VERIFY_FAILED if read-back shows empty/zero"
    )


def test_add_12_logs_verify_result():
    """The verification must be logged so failures are visible in the daemon log."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def neon_add_score_to_p")
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "verified=" in func_body, (
        "neon_add_score_to_p must log the verified value on success"
    )


def test_osascript_error_returns_failed():
    """When osascript returns non-zero, neon_add_score_to_p must return FAILED."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def neon_add_score_to_p")
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "FAILED" in func_body, (
        "neon_add_score_to_p must return FAILED on osascript error"
    )
    assert "returncode" in func_body, (
        "neon_add_score_to_p must check osascript returncode"
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


def test_block_has_goals_rejects_whitespace_only_bullet(tmp_path, monkeypatch):
    """Regression: an empty goal bullet '- [ ] ' (trailing space) must NOT count
    as a goal. The old regex `\\s*.+` matched the trailing whitespace, so a
    goal-less block earned a phantom 🎯 at its fire."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    build.write_text(
        "## -1₲\n\n"
        "- 卯 ☀️\n"
        "    - [ ] \n"             # empty (trailing space) → no goal
        "- 辰 🎯\n"
        "    - [ ] real goal\n",   # has text → goal
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    assert mod._block_has_goals("卯") is False
    assert mod._block_has_goals("辰") is True


def test_strip_unearned_markers_removes_phantom_but_guards_none(tmp_path, monkeypatch):
    """_strip_unearned_markers drops a daemon-owned marker the live data says
    wasn't earned (e.g. a phantom ✅), never touches ☀️/📧, and is a no-op when
    live is None (an API failure must not destroy a genuinely-earned mark)."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    original = (
        "## -1₲\n\n"
        "- 辰 ✅ ☀️ 🎯\n"
        "    - [ ] real goal\n"
    )
    build.write_text(original, encoding="utf-8")
    monkeypatch.setattr(mod, "BUILD_ORDER", build)

    # live=None → no-op (guard against transient API failure)
    mod._strip_unearned_markers("辰", None)
    assert build.read_text(encoding="utf-8") == original

    # ✅ not earned, 🎯 earned → strip ✅ only; keep ☀️ (no validator) + 🎯
    mod._strip_unearned_markers("辰", {"🎯": True, "⏱️": False, "✅": False})
    line = next(l for l in build.read_text(encoding="utf-8").split("\n")
                if l.startswith("- 辰"))
    assert "✅" not in line and "☀️" in line and "🎯" in line

    # dry_run leaves the file untouched
    build.write_text(original, encoding="utf-8")
    mod._strip_unearned_markers("辰", {"🎯": True, "⏱️": False, "✅": False},
                                dry_run=True)
    assert build.read_text(encoding="utf-8") == original


def test_block_matchers_tolerate_inline_annotations(tmp_path, monkeypatch):
    """Regression (2026-06-12): enrich writes mid-line annotations on block
    headers (`- 辰 (25min)   (32min) ⏰`, `(15分, 163min)`). The old matcher
    stripped only one trailing `(Nmin)`, so name comparison failed and blocks
    scored 0/13 even with every ritual earned — -1₦ points never reached Neon."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    build.write_text(
        "## -1₲\n\n"
        "- 卯 🎯 ⏱️ ⏰\n"
        "    - [ ] wake up well\n"
        "- 辰 (25min)   (32min) ⏰ ⏱️\n"
        "    - [ ] morning goal\n"
        "- 巳     (15分, 119min)  (15分, 163min) ☀️ ✅ 📧 🎯\n"
        "    - [x] grind list\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)

    # Name extraction is the first token, annotations ignored
    assert mod._block_line_name("- 辰 (25min)   (32min) ⏰") == "辰"
    assert mod._block_line_name("- 巳     (15分, 119min)  (15分, 163min) ☀️") == "巳"

    # Scoring matches annotated headers (was 0 before the fix)
    assert mod.score_block_from_emojis("辰", live={"⏱️": True}) == 3
    assert mod.score_block_from_emojis(
        "巳", live={"🎯": True, "✅": True}) == 1 + 3 + 3 + 3  # ☀️+🎯+✅+📧

    # Goal lookup and marker write also match annotated headers
    assert mod._block_has_goals("辰") is True
    assert mod._write_block_marker("辰", "🎯") is True
    assert "- 辰 (25min)   (32min) ⏰ ⏱️ 🎯" in build.read_text(encoding="utf-8")


# ── Reconcile: P must self-heal late markers, not drift below the header ──────
# Bug: the daemon scored each block once at its boundary and APPENDED +score to
# column P. A marker landing on a block header after its boundary fired (a prayer
# ☀️ logged at 08:20 when 卯 fired at 06:00) was never rescored, so P (53) drifted
# permanently below the header-implied total (79). Fix: reconcile_p_for_day SETs P
# to the validated score of all fired blocks each fire — idempotent + self-healing.

def test_reconcile_sets_total_over_all_fired_blocks(tmp_path, monkeypatch):
    mod = _load_daemon()
    build = tmp_path / "build.md"
    build.write_text(
        "## -1₲\n\n"
        "- 卯 🎯\n"
        "    - [ ] wake\n"
        "- 辰 🎯 ⏱️\n"
        "    - [ ] morning\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    monkeypatch.setattr(mod, "_live_for_block", lambda b, h, d: None)  # trust headers
    captured = {}
    monkeypatch.setattr(mod, "neon_set_p",
                        lambda date, formula, total, dry_run=False: captured.update(formula=formula, total=total) or "OK")
    import datetime as dt
    # upto_hour=8 → fire hours {4,6,8}; 4 has no block, 6→卯(3), 8→辰(🎯+⏱️=6)
    mod.reconcile_p_for_day(dt.date(2026, 6, 14), 8)
    assert captured["total"] == 9, captured           # 3 + 6, not just one block
    assert captured["formula"] == "=0+3+6"


def test_reconcile_is_idempotent(tmp_path, monkeypatch):
    """Re-firing the same hour must NOT double-count (the old append did)."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    build.write_text("## -1₲\n\n- 卯 🎯 ⏱️\n    - [ ] wake\n", encoding="utf-8")
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    monkeypatch.setattr(mod, "_live_for_block", lambda b, h, d: None)
    seen = []
    monkeypatch.setattr(mod, "neon_set_p",
                        lambda date, formula, total, dry_run=False: seen.append((formula, total)) or "OK")
    import datetime as dt
    mod.reconcile_p_for_day(dt.date(2026, 6, 14), 6)
    mod.reconcile_p_for_day(dt.date(2026, 6, 14), 6)
    assert seen[0] == seen[1] == ("=0+6", 6)          # identical, no accumulation


def test_reconcile_picks_up_late_prayer(tmp_path, monkeypatch):
    """The core bug: a ☀️ added to an already-fired block must raise P on the
    next reconcile. The old per-block append could never revisit 卯."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    build.write_text(
        "## -1₲\n\n"
        "- 卯 🎯\n"             # fired at 06:00 with only 🎯 → 3
        "    - [ ] wake\n"
        "- 辰 🎯\n"
        "    - [ ] morning\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    monkeypatch.setattr(mod, "_live_for_block", lambda b, h, d: None)
    totals = []
    monkeypatch.setattr(mod, "neon_set_p",
                        lambda date, formula, total, dry_run=False: totals.append(total) or "OK")
    import datetime as dt
    mod.reconcile_p_for_day(dt.date(2026, 6, 14), 8)   # 卯=3, 辰=3 → 6
    # Prayer logged late, stamped on the already-fired 卯 header
    build.write_text(build.read_text().replace("- 卯 🎯\n", "- 卯 🎯 ☀️\n"), encoding="utf-8")
    mod.reconcile_p_for_day(dt.date(2026, 6, 14), 8)   # 卯=4 now → 7
    assert totals == [6, 7], totals                    # late ☀️ picked up (+1)


def test_reconcile_preserves_goal_marker_on_past_blocks(tmp_path, monkeypatch):
    """Regression (v52 code-review): if a user (or a next-day clear) removes a
    checkbox line from an already-fired block, the reconcile's strip-phase
    would revoke that block's 🎯 — silently erasing a legitimately-earned
    marker. Past blocks must never have 🎯 revoked; the current in-progress
    block is the only one where 🎯 freshness matches the file's goal freshness."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    # 卯 fired at 06:00 with 🎯 (goal existed at fire time). Later the goal
    # bullet was deleted (e.g. user tidied the file, or next-day clear).
    build.write_text(
        "## -1₲\n\n"
        "- 卯 🎯 ☀️\n"     # 🎯 earned at 06:00
        # NOTE: goal bullet removed
        "- 辰 🎯\n"
        "    - [ ] current goal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    # live for past 卯 returns 🎯=False (no goal in file anymore) but 🎯 was
    # legitimately earned earlier — must not be stripped.
    def fake_live(bn, fh, td):
        return {mod.GOAL_MARKER: mod._block_has_goals(bn),
                mod.TOGGL_MARKER: False, mod.TODOIST_MARKER: False}
    monkeypatch.setattr(mod, "_live_for_block", fake_live)
    monkeypatch.setattr(mod, "neon_set_p",
                        lambda d, f, t, dry_run=False: "OK")
    import datetime as dt
    # upto_hour=8 → 卯 is a past block (fired at 06); 辰 is the current one.
    mod.reconcile_p_for_day(dt.date(2026, 6, 14), 8)
    text = build.read_text(encoding="utf-8")
    # Past 卯: 🎯 preserved despite live saying not-earned
    line_mao = next(l for l in text.split("\n") if l.startswith("- 卯"))
    assert "🎯" in line_mao, line_mao
    # Current 辰: 🎯 also preserved (goal exists)
    line_chen = next(l for l in text.split("\n") if l.startswith("- 辰"))
    assert "🎯" in line_chen, line_chen
