"""Regression tests: -1t/-1l (auto rituals) earn points on manual completion,
and 0分!P always reads as one term per block.

Bug 1 (pre-2026-07-13): completing the \U0001f608 -1t / \U0001f608 -1l card
was visibility-only — run_ritual's "mode == auto" branch closed the Todoist
card and returned immediately, with NO emoji stamp and NO 0分!P credit. The
daemon's automatic Toggl/Todoist validation (_todoist_l_satisfied etc.) was
the ONLY path to earning ⏱️/✅, so a user who genuinely did the work but whose
real tasks lacked a [N]/{N} marker (or whose Toggl categorization missed the
coverage threshold) could complete the card every 2h block and never see the
points land.

Fix: manual completion is now an independent, equally-valid path (OR'd with
the daemon's auto-check — see build-order-daemon.py's _marker_earned and
DAEMON_OWNED_MARKERS) — completing ⏱️/✅ stamps the emoji and credits P
immediately, same as ☀️/\U0001f3af/\U0001f4e7. It targets the CURRENT block,
matching the daemon's own convention: evaluate_and_mark_block's ⏱️/✅ CHECK
looks at the previous block's window (you can't know a block's Toggl/task
coverage is complete until it's over), but the resulting STAMP always lands
on the block being scored, never on the block that was checked. (An earlier
cut of this fix conflated those two things and stamped the previous block
instead — wrong, and confirmed wrong live: a user who did all 5 rituals
while in 午 correctly expected 午 itself to read 13, not 巳.)

Bug 2 (2026-07-13, same day): the first cut of this fix kept P append-only —
a positional "merge into the last term" for manual (current-block) rituals,
and always-open-a-new-term for auto (previous-block) rituals, to avoid ever
recomputing from a build-order.md copy that can lag Ix's over Syncthing (the
2026-07-11 clobber — see test_ritual_immediate_p.py). That's provably safe
but was observed live to leave P showing multiple terms for one block (e.g.
`=0+6+3+10+7+3` — five terms for four blocks, 巳 stuck at 10 instead of 13)
whenever an auto credit landed after the current block already had its own
later term — exactly the case the position-based append can't merge.

Fix: prefer a full recompute (`neon_blocks.score_day`, one term per
currently-stamped block, chronological order) using the header text this
very call just wrote to — so it can't be missing OUR OWN stamp. Guard against
the 2026-07-11 failure mode with a single check: only use the recomputed
formula if its total is >= the CURRENTLY LIVE P total (read fresh from
Excel); otherwise fall back to a plain append, which can still grow P but
never decreases it. The recompute is an improvement (better grouping, same or
higher total) in the common case; the rare cross-machine race (a daemon
marker landed on Ix moments ago, not yet synced to us) fails the guard and
degrades to append-only instead of clobbering.

This repo's convention (see test_did_ritual_card_routing.py) is structural
source-inspection over mocking for did-fast.py, since exercising run_ritual
end-to-end needs live Todoist + Excel/SSH access.
"""
from __future__ import annotations

import datetime as dtm
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"
TZ = ZoneInfo("America/Los_Angeles")

sys.path.insert(0, str(HERE.parent.parent / "lib"))
import neon_blocks as nb  # noqa: E402


def _run_ritual_body() -> str:
    src = DID_FAST.read_text(encoding="utf-8")
    i_start = src.index("def run_ritual(")
    i_end = src.index("\ndef ", i_start + 10)
    return src[i_start:i_end]


def test_no_early_return_before_stamping_for_auto_mode():
    # The old branch returned out immediately for mode=="auto", before the
    # emoji-stamp step ever ran. That early return must be gone: auto rituals
    # now fall through to the same stamp+credit path as manual ones.
    body = _run_ritual_body()
    assert 'out["note"] = "auto ritual — daemon stamps and scores at block close"' \
        not in body
    i_mode_check = body.index('r.get("mode")')
    i_stamp = body.index("stamp_emoji(")
    assert i_mode_check < i_stamp, (
        "the mode==auto check must run BEFORE stamping (to pick the target "
        "block), not as an early return that skips it")


def test_all_rituals_including_auto_target_the_current_block():
    body = _run_ritual_body()
    assert "is_auto = " in body
    assert "nb.current_block(datetime.now().hour)" in body, (
        "ALL rituals — including ⏱️/✅ — must stamp the CURRENT block, "
        "matching the daemon's own convention (its retrospective CHECK looks "
        "at the previous block, but the STAMP lands on the block being "
        "scored, never on the block that was checked)")
    # No previous-block index math should remain.
    assert "cur_idx - 1" not in body
    assert "BRANCHES[" not in body


def test_p_credit_prefers_recompute_guarded_by_live_total():
    body = _run_ritual_body()
    assert "nb.score_day(new_text)" in body, (
        "must compute the grouped, one-term-per-block formula from the "
        "just-written header text")
    assert "computed_total >= live_total" in body, (
        "must only use the recomputed formula when it's not a regression "
        "vs. the currently live P total — this is what prevents the "
        "2026-07-11 clobber (a recompute from a stale copy undercounting)")
    assert "live_total = float(v or 0)" in body, (
        "must read the CURRENT live P value fresh before deciding")


def test_p_credit_falls_back_to_append_never_decreases():
    body = _run_ritual_body()
    i_else = body.index("else:\n                # Fall back to a safe append")
    fallback = body[i_else:i_else + 650]
    assert "terms.append(str(pts))" in fallback, (
        "the guard-triggered fallback must still credit this ritual's own "
        "points via append, not silently drop them")


def test_score_day_groups_all_stamped_blocks_into_one_term_each():
    # Directly demonstrates the fix for the observed bug: a block (巳) with
    # ALL 5 rituals stamped must score as ONE term (13), not fragment across
    # multiple terms the way append-only accumulation could leave it.
    #
    # `now` is pinned to 11:00 (午, start hour 10, has begun) — score_day
    # excludes not-yet-started blocks (see test_neon_blocks_future_block_gate.py),
    # so this must stay >= 午's own start hour or the assertion below would be
    # time-of-day flaky depending on when the suite runs.
    now = dtm.datetime.now(TZ).replace(hour=11, minute=0, second=0, microsecond=0)
    text = (
        "## -1₲\n\n"
        "- 卯 ⏱️ ✅\n"
        "    - [ ] a\n"
        "- 辰 ⏱️\n"
        "    - [ ] b\n"
        "- 巳 ☀️ \U0001f3af \U0001f4e7 ⏱️ ✅\n"
        "    - [ ] c\n"
        "- 午 ☀️ \U0001f3af \U0001f4e7\n"
        "    - [ ] d\n"
    )
    parts, total, formula = nb.score_day(text, now=now)
    assert dict(parts) == {"卯": 6, "辰": 3, "巳": 13, "午": 7}
    assert total == 29
    assert formula == "=6+3+13+7", (
        "exactly one term per block, no stray leading '0' — otherwise a day "
        "with N scored blocks shows N+1 terms (the literal '0' miscounted as "
        "a phantom block; observed live 2026-07-14: 6 terms for 5 blocks)")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
