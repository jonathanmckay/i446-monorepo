"""Regression tests for build-order-daemon.py.

Bug: neon_add_score_to_p logged success (Y_ADD) even when Excel was not open,
because osascript returned a cached/stale result. The write silently failed,
leaving the -1₦ cell empty.

Fix: Added a read-back verification step that calls neon_read_y after writing
and logs VERIFY_FAILED if the cell is empty/zero.
"""
import json
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


def test_marker_earned_trusts_goal_marker_on_presence():
    """2026-08-11 (JM): "-1g should always give me the points and audit
    should not revoke them." Completing the 😈 -1g card is itself the
    attestation, same as -1t/-1l below — it no longer needs a separate
    goal-presence check to keep its points. (Previously this asserted the
    OPPOSITE: that a live goal-presence check of False stripped 🎯 even
    after a manual completion — that was the bug reported live: "I did all
    -1n habits in 辰 and 巳 but janus only showing 10 each" instead of 13,
    because no goal TEXT existed for either block even though the ritual
    CARD was completed for both.)"""
    mod = _load_daemon()
    line = "- 巳 🎯 😈"
    assert mod._marker_earned("🎯", line, {"🎯": False}) is True
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


def test_score_block_scores_goal_marker_regardless_of_live(tmp_path, monkeypatch):
    """2026-08-11: 🎯 scores on header presence alone now, same as ⏱️/✅ —
    a stamped 🎯 with no goal text still counts (see
    test_marker_earned_trusts_goal_marker_on_presence for the full story)."""
    mod = _load_daemon()
    build = tmp_path / "build.md"
    # 😈 (fired stamp) is written in Phase 3, after scoring, so the header has
    # only the sub-habit markers at score time.
    build.write_text(
        "## -1₲\n\n"
        "- 巳 🎯\n"
        "    - [ ] \n"          # empty goal — no longer matters for scoring
        "- 午 ✅\n"
        "    - [ ] real goal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BUILD_ORDER", build)
    # Live results for 巳: no goals, no toggl, no todoist → 🎯 still scores 3
    assert mod.score_block_from_emojis("巳", live={"🎯": False, "⏱️": False, "✅": False}) == 3
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


def test_strip_unearned_markers_is_now_a_no_op_for_every_marker(tmp_path, monkeypatch):
    """2026-08-11: DAEMON_OWNED_MARKERS is now empty (🎯's strip audit was
    removed per JM — see test_marker_earned_trusts_goal_marker_on_presence),
    so _strip_unearned_markers never removes anything regardless of what
    `live` says. The function stays wired in (not deleted) in case a future
    marker needs this kind of audit again."""
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

    # Even a live result saying nothing was earned strips nothing now.
    mod._strip_unearned_markers("辰", {"🎯": False, "⏱️": False, "✅": False})
    assert build.read_text(encoding="utf-8") == original


# ── Audited redesign (2026-07-30): ⏱️ is provisional-credit-then-audited,
# same as 🎯 -- ✅ tried the same treatment the same day and was reverted
# ────────────────────────────────────────────────────────────────────────────
# History: -1t/-1l started "auto"-only (completing the card did nothing).
# 2026-07-13 made completion earn them unconditionally ("OR" with the
# auto-check, never stripped) so a manual completion could never be erased by
# a failing auto-check. But that meant a false claim (closing -1t without
# actually hitting the Toggl-coverage threshold) was never caught either.
# 2026-07-30 (user-confirmed correction) made ⏱️/✅ both match 🎯: audited,
# strippable if `live` disagrees. Same day, after one round of use, ✅ alone
# was reverted back to trusted-on-presence — _todoist_l_satisfied proved too
# narrow a proxy for "-1l" (a first-person attestation), unlike ⏱️'s
# Toggl-minute-coverage check, which stayed audited.

def test_marker_earned_trusts_toggl_todoist_and_goal_on_presence():
    """2026-08-01 (JM): "If I manually mark -1l or -1t there shouldn't be an
    audit." ⏱️ joined ✅ as trusted-on-presence; the live checks are
    auto-award only. 2026-08-11: 🎯 joined them too (JM: "-1g should always
    give me the points and audit should not revoke them") — all three
    manual-completion markers are now trusted the same way."""
    mod = _load_daemon()
    # ⏱️/✅/🎯 all trusted on presence regardless of live — a manual claim is final.
    assert mod._marker_earned("⏱️", "- 巳 ⏱️", {"⏱️": False}) is True
    assert mod._marker_earned("⏱️", "- 巳 ⏱️", {"⏱️": True}) is True
    assert mod._marker_earned("✅", "- 巳 ✅", {"✅": False}) is True
    assert mod._marker_earned("✅", "- 巳 ✅", {"✅": True}) is True
    assert mod._marker_earned("🎯", "- 巳 🎯", {"🎯": False}) is True
    assert mod._marker_earned("🎯", "- 巳 🎯", {"🎯": True}) is True
    # Absent marker never earns regardless of live.
    assert mod._marker_earned("✅", "- 巳", {"✅": True}) is False
    assert mod._marker_earned("⏱️", "- 巳", {"⏱️": True}) is False
    assert mod._marker_earned("🎯", "- 巳", {"🎯": True}) is False
    # 🔒 still overrides everything — a deliberate user lock is never stripped.
    assert mod._marker_earned("🎯", "- 巳 🎯 🔒", {"🎯": False}) is True


def test_daemon_owned_markers_is_empty():
    """Strip set is empty (2026-08-11): 🎯 was the last marker in it, and its
    strip audit was removed per JM. Kept as an explicit empty set rather
    than deleted so _strip_unearned_markers stays wired in for future use."""
    mod = _load_daemon()
    assert mod.DAEMON_OWNED_MARKERS == set()


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


def _fake_todoist_write(created):
    """Records created/mutated payloads and returns a (status, body) reply
    that echoes back the same labels sent in -- i.e. a successful, non-flaky
    write, so tests aren't exercising the label-repair path by accident."""
    def _write(path, payload, token, method="POST"):
        created.append(payload)
        body = json.dumps({"id": "created", "labels": (payload or {}).get("labels", [])}).encode()
        return 200, body
    return _write


def test_create_block_rituals_skips_annotated_duplicate(monkeypatch):
    """Functional: an already-open '-1g' card annotated with (15) [15] must
    stop create_block_rituals from creating a second one."""
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals",
                        lambda token: [{"id": "1", "content": "😈 -1g (15) [15]"}])
    created = []
    monkeypatch.setattr(mod, "_todoist_write", _fake_todoist_write(created))
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


# ── Ritual fetch failure must fail closed, not open ─────────────────────────
# Bug (2026-07-30): -1t and -1l (and the other three -1neon cards) were
# double created. _todoist_open_rituals() caught any fetch exception (e.g. an
# HTTP 503) and returned [], indistinguishable from "genuinely nothing open".
# create_block_rituals() then created a full second set of cards on top of
# whatever was already open. Confirmed in /tmp/neon-lock-and-mark.log on Ix at
# 2026-07-30T04:00:06-0700: "rituals: fetch open ERROR HTTP Error 503: Service
# Unavailable" immediately followed by five "rituals: + ..." creation lines.
# Fix: _todoist_open_rituals() now returns None on failure (vs. [] for a
# genuinely empty result), and both create_block_rituals/delete_block_rituals
# abort instead of proceeding as if nothing were open.

def test_todoist_open_rituals_returns_none_on_fetch_error(monkeypatch):
    mod = _load_daemon()

    def raise_error(*a, **k):
        raise OSError("HTTP Error 503: Service Unavailable")

    monkeypatch.setattr(mod.urllib.request, "urlopen", raise_error)
    assert mod._todoist_open_rituals("tok") is None


def test_create_block_rituals_skips_when_fetch_fails(monkeypatch):
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals", lambda token: None)
    created = []
    monkeypatch.setattr(mod, "_todoist_write",
                        lambda path, payload, token, method="POST": created.append(payload))
    mod.create_block_rituals()
    assert created == [], "a failed fetch must not be treated as license to create a duplicate set"


def test_delete_block_rituals_skips_when_fetch_fails(monkeypatch):
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals", lambda token: None)
    calls = []
    monkeypatch.setattr(
        mod, "_todoist_write",
        lambda path, payload, token, method="POST": calls.append((path, method)))
    mod.delete_block_rituals(live={"⏱️": True})
    assert calls == [], "a failed fetch must not be treated as an empty leftover set"


def test_create_block_rituals_still_creates_on_genuinely_empty_fetch(monkeypatch):
    """Sanity check the fix distinguishes failure (None) from a genuinely
    empty result ([]) -- the latter must still allow creation as before."""
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals", lambda token: [])
    created = []
    monkeypatch.setattr(mod, "_todoist_write", _fake_todoist_write(created))
    mod.create_block_rituals()
    assert len(created) == 5


# ── Created ritual card must have its label verified, and repaired if the
# creation write dropped it (bug 2026-07-30: 3 of 5 cards created in the same
# batch came back from Todoist with labels=[] -- an apparent write flake on
# rapid-fire creates. An unlabeled card is invisible to the label-filtered
# open-rituals fetch, so it's never retired and just orphans in the inbox,
# with the dedup check also blind to it -- each subsequent fire creates
# another one on top since it can't see the orphan as "already open" either).

def test_create_block_rituals_repairs_dropped_label(monkeypatch):
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals", lambda token: [])
    calls = []

    def flaky_write(path, payload, token, method="POST"):
        if path == "/tasks":
            calls.append(("create", path, payload))
            # Simulate Todoist dropping the label on write, regardless of
            # what was sent.
            return 200, json.dumps({"id": "created-1", "labels": []}).encode()
        calls.append(("repair", path, payload))
        return 200, json.dumps({"id": "created-1", "labels": payload["labels"]}).encode()

    monkeypatch.setattr(mod, "_todoist_write", flaky_write)
    mod.create_block_rituals()

    repairs = [c for c in calls if c[0] == "repair"]
    assert len(repairs) == 5, "every card that came back unlabeled must get a corrective PATCH"
    assert all(c[1] == "/tasks/created-1" and c[2] == {"labels": ["-1neon"]} for c in repairs)


def test_create_block_rituals_no_repair_when_label_persists(monkeypatch):
    mod = _load_daemon()
    monkeypatch.setattr(mod, "_todoist_token", lambda: "tok")
    monkeypatch.setattr(mod, "_todoist_open_rituals", lambda token: [])
    created = []
    monkeypatch.setattr(mod, "_todoist_write", _fake_todoist_write(created))
    mod.create_block_rituals()
    # _fake_todoist_write echoes the sent labels back, so every create is
    # already correctly labeled -- no repair call (a 6th write) should fire.
    assert len(created) == 5


def test_goal_marker_stays_current_block():
    # 🎯 (-1g) is a current-block ritual — validated on THIS block's goals, not
    # the previous block's coverage.
    src = DAEMON.read_text(encoding="utf-8")
    body = _func_body(src, "_live_for_block")
    assert "GOAL_MARKER: _block_has_goals(block_name)" in body


# ── compute_toggl_totals: stale open-timer clamp ────────────────────────────
# Bug: Toggl still returns the currently-open (still-running) entry even when
# it started before the queried day — an open entry has no stop time for the
# date-range filter to match against. compute_toggl_totals computed a running
# entry's minutes as `now - start` unconditionally, so a >1-day-old forgotten
# open timer dumped its ENTIRE elapsed time into the current day's column
# (regression 2026-08-11: a stale "fall asleep" timer read as AV=1493min in
# 0n — the whole morning showing as asleep even though the user was up and
# doing things before 6am).
#
# Fix: clamp a running entry's effective start to target_date's local
# (America/Los_Angeles) midnight before computing elapsed minutes.
import datetime as dt


def test_compute_toggl_totals_clamps_stale_running_entry(monkeypatch):
    mod = _load_daemon()
    target_date = dt.date(2026, 8, 11)

    # Started >1 day before target_date, still open (Toggl's negative-duration
    # convention for a running entry — the exact value is irrelevant, only
    # its sign and the `start` field matter).
    stale_start = dt.datetime(2026, 8, 10, 5, 7, tzinfo=dt.timezone.utc)

    class FrozenDatetime(mod.dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 8, 11, 13, 0, tzinfo=dt.timezone.utc)  # 06:00 PDT

    monkeypatch.setattr(mod.dt, "datetime", FrozenDatetime)
    monkeypatch.setattr(mod, "_toggl_get", lambda path: [
        {"duration": -1, "start": stale_start.isoformat().replace("+00:00", "Z"),
         "tags": ["-1"], "project_id": None},
    ])

    totals = mod.compute_toggl_totals(target_date)
    # PT midnight for 2026-08-11 is 07:00Z; frozen "now" is 13:00Z, so at most
    # 360 minutes can legitimately belong to today — far below the ~1667
    # minutes an unclamped (now - stale_start) would have produced.
    assert totals.get("AV") == 360


# ── #xk88 tag -> 0分 points, 1pt/min (JM 2026-08-13) ────────────────────────
# Different write model from the 0n minute totals above: 0分!X accumulates via
# a shared +N append (other point sources write there too), so re-syncing the
# same tagged minutes every 2h cycle would double-count. A per-day minute
# baseline (TOGGL_POINT_STATE_PATH) is diffed each cycle so only the DELTA
# since the last sync gets appended.

def test_compute_toggl_point_tag_minutes_sums_only_configured_tags(monkeypatch):
    mod = _load_daemon()
    target_date = dt.date(2026, 8, 13)
    monkeypatch.setattr(mod, "_toggl_get", lambda path: [
        {"duration": 600, "tags": ["xk88"], "project_id": 1},   # 10m, counts
        {"duration": 1200, "tags": ["xk88"], "project_id": 2},  # 20m, counts
        {"duration": 300, "tags": ["-1"], "project_id": 3},     # not a point tag
        {"duration": 300, "tags": [], "project_id": 4},         # no tags at all
    ])
    totals = mod.compute_toggl_point_tag_minutes(target_date)
    assert totals == {"xk88": 30}


def test_toggl_point_sync_appends_full_amount_on_first_cycle(monkeypatch, tmp_path):
    mod = _load_daemon()
    monkeypatch.setattr(mod, "dt", dt)  # real clock; today's date only matters for keys
    monkeypatch.setattr(mod, "TOGGL_POINT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(mod, "compute_toggl_point_tag_minutes", lambda d: {"xk88": 12})
    calls = []
    monkeypatch.setattr(mod.neon_excel, "append",
                         lambda sheet, col, date, value, src: calls.append((sheet, col, value)))
    mod.run_toggl_point_sync()
    assert calls == [("0分", "X", "+12")]


def test_toggl_point_sync_appends_only_delta_on_later_cycle(monkeypatch, tmp_path):
    """Regression for the double-count trap: cycle 1 sees 12 tagged minutes
    (+12 appended); cycle 2 re-fetches the SAME entries plus 8 new minutes
    (20 total) -- it must append only +8, not +20."""
    mod = _load_daemon()
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(mod, "TOGGL_POINT_STATE_PATH", state_path)

    monkeypatch.setattr(mod, "compute_toggl_point_tag_minutes", lambda d: {"xk88": 12})
    calls = []
    monkeypatch.setattr(mod.neon_excel, "append",
                         lambda sheet, col, date, value, src: calls.append((sheet, col, value)))
    mod.run_toggl_point_sync()

    monkeypatch.setattr(mod, "compute_toggl_point_tag_minutes", lambda d: {"xk88": 20})
    mod.run_toggl_point_sync()

    assert calls == [("0分", "X", "+12"), ("0分", "X", "+8")]


def test_toggl_point_sync_skips_write_when_minutes_unchanged(monkeypatch, tmp_path):
    mod = _load_daemon()
    monkeypatch.setattr(mod, "TOGGL_POINT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(mod, "compute_toggl_point_tag_minutes", lambda d: {"xk88": 12})
    calls = []
    monkeypatch.setattr(mod.neon_excel, "append",
                         lambda sheet, col, date, value, src: calls.append((sheet, col, value)))
    mod.run_toggl_point_sync()
    mod.run_toggl_point_sync()  # same 12 minutes again -- delta is 0
    assert calls == [("0分", "X", "+12")]


def test_toggl_point_sync_dry_run_makes_no_writes_and_no_state(monkeypatch, tmp_path):
    mod = _load_daemon()
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(mod, "TOGGL_POINT_STATE_PATH", state_path)
    monkeypatch.setattr(mod, "compute_toggl_point_tag_minutes", lambda d: {"xk88": 12})
    calls = []
    monkeypatch.setattr(mod.neon_excel, "append",
                         lambda sheet, col, date, value, src: calls.append((sheet, col, value)))
    mod.run_toggl_point_sync(dry_run=True)
    assert calls == []
    assert not state_path.exists()


def test_toggl_point_sync_failed_append_does_not_update_baseline(monkeypatch, tmp_path):
    """A failed Excel write must not be recorded as synced, or the delta for
    those minutes is lost forever instead of retried next cycle."""
    mod = _load_daemon()
    monkeypatch.setattr(mod, "TOGGL_POINT_STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(mod, "compute_toggl_point_tag_minutes", lambda d: {"xk88": 12})

    def _boom(*a, **k):
        raise RuntimeError("daemon unreachable")
    monkeypatch.setattr(mod.neon_excel, "append", _boom)
    mod.run_toggl_point_sync()  # must not raise

    calls = []
    monkeypatch.setattr(mod.neon_excel, "append",
                         lambda sheet, col, date, value, src: calls.append((sheet, col, value)))
    mod.run_toggl_point_sync()
    assert calls == [("0分", "X", "+12")], "retry must resend the full 12, not 0"


def test_toggl_point_sync_wired_into_lock_and_mark_same_cadence():
    """run_toggl_point_sync must fire alongside run_toggl_sync inside
    run_lock_and_mark, not just exist as an unused standalone function."""
    src = DAEMON.read_text(encoding="utf-8")
    idx = src.index("def run_lock_and_mark")
    next_def = src.index("\ndef ", idx + 1)
    body = src[idx:next_def]
    assert "run_toggl_sync(dry_run=dry_run)" in body
    assert "run_toggl_point_sync(dry_run=dry_run)" in body
