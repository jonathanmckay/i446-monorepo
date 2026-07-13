"""Regression tests: -1t/-1l (auto rituals) earn points on manual completion.

Bug (pre-2026-07-13): completing the \U0001f608 -1t / \U0001f608 -1l card was
visibility-only — run_ritual's "mode == auto" branch closed the Todoist
card and returned immediately, with NO emoji stamp and NO 0分!P credit. The
daemon's automatic Toggl/Todoist validation (_todoist_l_satisfied etc.) was
the ONLY path to earning ⏱️/✅, so a user who genuinely did the
work but whose real tasks lacked a [N]/{N} marker (or whose Toggl
categorization missed the coverage threshold) could complete the card
every 2h block and never see the points land.

Fix: manual completion is now an independent, equally-valid path (OR'd with
the daemon's auto-check — see build-order-daemon.py's _marker_earned and
DAEMON_OWNED_MARKERS) — completing ⏱️/✅ stamps the emoji and credits P
immediately, same as ☀️/\U0001f3af/\U0001f4e7. Since ⏱️/✅ measure the
PREVIOUS block (not the block the card is completed in), the manual stamp
targets that same previous block, not the current one.

P-credit stays append-only (never a from-headers recompute): the 2026-07-11
clobber (see test_ritual_immediate_p.py) came from recomputing P off a
build-order.md copy that can lag Ix's over Syncthing, silently undercounting
and SETting P below what the daemon's own (self-contained, no cross-machine
read) reconcile had already correctly written. Since ⏱️/✅ target a block that
may not be P's last term (the current block can already have its own later
term), a positional "merge into last term" isn't attempted for them either —
they always open a fresh term, appended safely, and the next daemon reconcile
(≤2h, running on Ix against Ix's own file) re-groups it into the previous
block's own term.

This repo's convention (see test_did_ritual_card_routing.py) is structural
source-inspection over mocking for did-fast.py, since exercising run_ritual
end-to-end needs live Todoist + Excel/SSH access.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DID_FAST = HERE / "did-fast.py"


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


def test_auto_mode_targets_previous_block():
    body = _run_ritual_body()
    assert "is_auto = " in body
    assert "max(0, cur_idx - 1) if is_auto else cur_idx" in body, (
        "auto rituals (⏱️/✅) must stamp the PREVIOUS block, matching "
        "the daemon's own hour-4..hour-2 offset — not the current block")


def test_p_credit_is_append_only_never_a_full_recompute():
    # Must still be a pure increment (never `score_day`/a from-headers
    # recompute) — see test_ritual_immediate_p.py for why a recompute can
    # clobber P using a stale (Syncthing-lagged) local build-order.md copy.
    body = _run_ritual_body()
    assert "score_day(" not in body, (
        "P-credit must stay append-only; a from-headers recompute reintroduces "
        "the 2026-07-11 clobber via Ix/Straylight sync lag")
    assert "terms.append(str(pts))" in body, (
        "must append THIS ritual's own points onto whatever P currently holds")


def test_auto_mode_never_attempts_positional_merge():
    # Auto rituals target the PREVIOUS block, which is not necessarily P's
    # last term (the current block may already have a later term of its own)
    # — merging into "last term" for them would silently credit the wrong
    # block, so `same_block_merge` must be hardcoded False for is_auto.
    body = _run_ritual_body()
    i_is_auto = body.index("if is_auto:")
    segment = body[i_is_auto:i_is_auto + 200]
    assert "same_block_merge = False" in segment


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
