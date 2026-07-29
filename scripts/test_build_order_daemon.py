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
    """When the excel client reports a failed write, neon_add_score_to_p must
    return FAILED (it used to check osascript returncode directly)."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def neon_add_score_to_p")
    next_def = src.index("\ndef ", idx + 1)
    func_body = src[idx:next_def]
    assert "FAILED" in func_body, (
        "neon_add_score_to_p must return FAILED on a failed client write"
    )
    assert 'get("ok")' in func_body, (
        "neon_add_score_to_p must check the client response's ok flag"
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
    line = "- 巳 🎯 😈"
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
    # 😈 (fired stamp) is written in Phase 3, after scoring, so the header has
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


def test_strip_unearned_markers_removes_phantom_goal_but_guards_none(tmp_path, monkeypatch):
    """_strip_unearned_markers drops GOAL_MARKER (🎯) when the live data says it
    wasn't earned, never touches ☀️/📧, and is a no-op when live is None (an API
    failure must not destroy a genuinely-earned mark)."""
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

    # 🎯 not earned → strip 🎯 only; ✅ stays even though its own live check is
    # False too (2026-07-13 OR redesign: ⏱️/✅ are no longer daemon-strippable —
    # a manual completion may have earned them independently of the auto-check,
    # so a failing auto-check must never erase them). ☀️ has no validator either.
    mod._strip_unearned_markers("辰", {"🎯": False, "⏱️": False, "✅": False})
    line = next(l for l in build.read_text(encoding="utf-8").split("\n")
                if l.startswith("- 辰"))
    assert "🎯" not in line and "☀️" in line and "✅" in line

    # dry_run leaves the file untouched
    build.write_text(original, encoding="utf-8")
    mod._strip_unearned_markers("辰", {"🎯": False, "⏱️": False, "✅": False},
                                dry_run=True)
    assert build.read_text(encoding="utf-8") == original


# ── OR redesign: ⏱️/✅ are earned by manual completion OR the auto-check ─────
# Bug: -1t/-1l were "auto"-only — completing their card did nothing (no stamp,
# no P credit); the daemon's Toggl/Todoist auto-check was the sole path. Users
# who genuinely did the work but whose real tasks lacked [N]/{N} (or whose
# Toggl categorization missed the threshold) never earned the marker even
# though they manually confirmed it. Fix: header presence alone earns ⏱️/✅
# (like ☀️/📧 always have) — the daemon's auto-check still WRITES the marker
# when it independently passes, but a failing auto-check no longer strips a
# marker manual completion already wrote. Only 🎯 (GOAL_MARKER) keeps the old
# live-gated/strippable behavior.

def test_marker_earned_ors_manual_completion_with_failing_auto_check():
    mod = _load_daemon()
    # ✅ manually stamped, but the daemon's own auto-check for this block fails
    # (e.g. no [N]/{N}-pointed task completed in the window) — must still earn.
    assert mod._marker_earned("✅", "- 巳 ✅", {"✅": False}) is True
    assert mod._marker_earned("⏱️", "- 巳 ⏱️", {"⏱️": False}) is True
    # Absent marker never earns regardless of live.
    assert mod._marker_earned("✅", "- 巳", {"✅": True}) is False


def test_daemon_owned_markers_excludes_toggl_and_todoist():
    mod = _load_daemon()
    assert mod.DAEMON_OWNED_MARKERS == {mod.GOAL_MARKER}


def test_block_matchers_tolerate_inline_annotations(tmp_path, monkeypatch):
    """Regression (2026-06-12): enrich writes mid-line annotations on block
    headers (`- 辰 (25min)   (32min) 😈`, `(15分, 163min)`). The old matcher
    stripped only one trailing `(Nmin)`, so name comparison failed and blocks
    scored 0/13 even with every ritual earned — -1₦ points never reached Neon."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    build.write_text(
        "## -1₲\n\n"
        "- 卯 🎯 ⏱️ 😈\n"
        "    - [ ] wake up well\n"
        "- 辰 (25min)   (32min) 😈 ⏱️\n"
        "    - [ ] morning goal\n"
        "- 巳     (15分, 119min)  (15分, 163min) ☀️ ✅ 📧 🎯\n"
        "    - [x] grind list\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)

    # Name extraction is the first token, annotations ignored
    assert mod._block_line_name("- 辰 (25min)   (32min) 😈") == "辰"
    assert mod._block_line_name("- 巳     (15分, 119min)  (15分, 163min) ☀️") == "巳"

    # Scoring matches annotated headers (was 0 before the fix)
    assert mod.score_block_from_emojis("辰", live={"⏱️": True}) == 3
    assert mod.score_block_from_emojis(
        "巳", live={"🎯": True, "✅": True}) == 1 + 3 + 3 + 3  # ☀️+🎯+✅+📧

    # Goal lookup and marker write also match annotated headers
    assert mod._block_has_goals("辰") is True
    assert mod._write_block_marker("辰", "🎯") is True
    assert "- 辰 (25min)   (32min) 😈 ⏱️ 🎯" in build.read_text(encoding="utf-8")


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
    assert captured["formula"] == "=3+6"


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
    assert seen[0] == seen[1] == ("=6", 6)          # identical, no accumulation


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


# ── 2026-07-12 redesign: -1t/-1l measure the PREVIOUS block ──────────────────
# Block X's ⏱️/✅ reward having RECORDED block X-1 (e.g. 戌's ⏱️ ⇔ 酉 fully
# recorded), so both validators use the [fire-4, fire-2] window, not the block's
# own [fire-2, fire]. 🎯 stays current-block (goals are set for X itself).

def _func_body(src: str, name: str) -> str:
    idx = src.index(f"def {name}")
    nxt = src.index("\ndef ", idx + 1)
    return src[idx:nxt]


def test_auto_rituals_measure_previous_block_window():
    src = DAEMON.read_text(encoding="utf-8")
    for fn in ("evaluate_and_mark_block", "_live_for_block"):
        body = _func_body(src, fn)
        assert "_prev_block_window(hour, target_date)" in body, (
            f"{fn} must derive the previous-block window via _prev_block_window "
            f"(centralizes the 卯/sleep-gap wraparound exception)")
        assert "_toggl_covers_block(prev_date, prev" in body, (
            f"{fn} must pass the previous-block's own date to _toggl_covers_block")
        assert "_todoist_l_satisfied(prev_date, prev" in body, (
            f"{fn} must pass the previous-block's own date to _todoist_l_satisfied")


def test_prev_block_window_is_the_prior_days_hai_for_mao():
    # Regression (2026-07-19): 卯 fires at 06, and the generic hour-4/hour-2
    # arithmetic landed on [02,04] — inside the unscored overnight sleep gap
    # (22:00-04:00), always trivially "covered" by one sleep Toggl entry, so
    # ⏱️/✅ for 卯 never signaled anything real (credited while asleep, having
    # done nothing). 卯's real previous block is 亥 (20:00-22:00) of the PRIOR
    # calendar day.
    import datetime as dt
    mod = _load_daemon()
    today = dt.date(2026, 7, 19)
    start, end, date = mod._prev_block_window(6, today)
    assert (start, end, date) == (20, 22, dt.date(2026, 7, 18)), (
        "卯's previous-block window must be 亥 (20-22) of the PRIOR day, "
        f"got start={start} end={end} date={date}"
    )


def test_prev_block_window_is_unchanged_for_every_other_block():
    # Every other fire hour must keep the plain same-day [hour-4, hour-2]
    # window — only 卯 (hour=6) is the wraparound special case.
    import datetime as dt
    mod = _load_daemon()
    today = dt.date(2026, 7, 19)
    for fh in (8, 10, 12, 14, 16, 18, 20, 22):
        start, end, date = mod._prev_block_window(fh, today)
        assert (start, end, date) == (fh - 4, fh - 2, today), (
            f"fire hour {fh} must use the plain same-day window"
        )


# ── Ritual card dedup/earned must tolerate annotated content ───────────────
# Bug (2026-07-29): "seeing a lot of extra -1n" + uniform bogus (15)[15] on
# every ritual card (real ritual cards never carry [N] -- their points come
# from 0分!P via the block header). The instant a ritual card picked up ANY
# trailing annotation, create_block_rituals' dedup check (`tag in open_bare`,
# exact string match) stopped recognizing it as already open and created a
# duplicate every 2h fire, while delete_block_rituals' earned check (`bare in
# auto_emoji`) stopped recognizing an EARNED auto card, silently deleting it
# (no credit) instead of closing it. lib/neon_blocks.ritual_card_tag() already
# handles this correctly (whole-token comparison) for dtd's completion path;
# _ritual_bare_tag() is the same logic, now shared by both daemon functions.

def test_ritual_bare_tag_tolerates_trailing_annotations():
    mod = _load_daemon()
    tags = ["سمش", "-1g", "-1ibx", "-1t", "-1l"]
    assert mod._ritual_bare_tag("😈 -1g (15) [15]", "😈", tags) == "-1g"
    assert mod._ritual_bare_tag("😈 -1g", "😈", tags) == "-1g"
    assert mod._ritual_bare_tag("😈 -1t (30)", "😈", tags) == "-1t"


def test_ritual_bare_tag_none_for_unrelated_task():
    # Unlike neon_blocks.ritual_card_tag() (which gatekeeps arbitrary dtd task
    # names and fails closed with no marker), _ritual_bare_tag() is only ever
    # called on tasks already filtered by the -1neon LABEL
    # (_todoist_open_rituals), so it doesn't need that guard -- it's purely
    # tag-matching, not "is this a ritual card at all" classification.
    mod = _load_daemon()
    tags = ["سمش", "-1g", "-1ibx", "-1t", "-1l"]
    assert mod._ritual_bare_tag("😈 unrelated task (15) [15]", "😈", tags) is None


def test_create_block_rituals_skips_annotated_duplicate(monkeypatch):
    """Functional: an already-open '-1g' card annotated with (15) [15] must
    stop create_block_rituals from creating a second one."""
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals",
                        lambda token: [{"id": "1", "content": "😈 -1g (15) [15]"}])
    created = []
    monkeypatch.setattr(mod, "_todoist_write",
                        lambda path, payload, token, method="POST": created.append(payload))
    mod.create_block_rituals()
    created_tags = [p["content"].replace("😈", "").strip() for p in created]
    assert "-1g" not in created_tags, "annotated '-1g' must be recognized as already open"
    # The other 4 rituals (not open at all) still get created.
    assert {"سمش", "-1ibx", "-1t", "-1l"} <= set(created_tags)


def test_delete_block_rituals_closes_annotated_earned_auto_card(monkeypatch):
    """Functional: an EARNED auto card ('-1t') annotated with (15) [15] must
    still be CLOSED (credited), not deleted, at block turnover."""
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals",
                        lambda token: [{"id": "1", "content": "😈 -1t (15) [15]"}])
    calls = []
    monkeypatch.setattr(
        mod, "_todoist_write",
        lambda path, payload, token, method="POST": calls.append((path, method)))
    mod.delete_block_rituals(live={"⏱️": True})
    assert calls == [("/tasks/1/close", "POST")], (
        "annotated but earned auto card must be closed, not deleted")


def test_goal_marker_stays_current_block():
    # 🎯 (-1g) is a current-block ritual — validated on THIS block's goals, not
    # the previous block's coverage.
    src = DAEMON.read_text(encoding="utf-8")
    body = _func_body(src, "_live_for_block")
    assert "GOAL_MARKER: _block_has_goals(block_name)" in body
