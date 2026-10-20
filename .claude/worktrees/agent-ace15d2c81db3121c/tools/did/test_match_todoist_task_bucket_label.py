#!/usr/bin/env python3
"""Regression (2026-07-30): "I did 1 s897 yesterday, it's recorded in neon,
but it showed up in today's dtd list."

Completing the one-off `/-1g` goal card "1 s897 {5}" (project 0g, label
`#-1g`) also happens to name-match the live 1n+ habit header "1 s897". Step
0.2's 1n+ routing asked match_todoist_task to find the matching *1neon* card
to close, searching only `neon_1n_tasks` (tasks labeled `1neon`). The goal
card's id wasn't in that bucket, so the preferred_id fallback fetched it by
id directly from Todoist and returned it UNCONDITIONALLY -- even though it
carries no `1neon` label at all. did-fast believed it had found and closed
the real weekly 1neon card and never searched further; the actual recurring
"1 s897 (25) [30]" card sat open/overdue, still showing up in dtd.

Fix: match_todoist_task takes `require_labels` -- the id-fetch fallback only
counts as a match if the fetched task actually carries one of those labels.
Otherwise it falls through to the normal name-based search over the bucket
that was actually passed in.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "did_fast_bucket_label", _HERE / "did-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_bucket_label"] = mod
    spec.loader.exec_module(mod)
    return mod


GOAL_CARD = {
    "id": "6h98wXRc8c7PW5xR",
    "content": "1 s897 {5}",
    "labels": ["#-1g", "s897"],
}
REAL_1NEON_CARD = {
    "id": "6h8G3HFj6R9Q6HH6",
    "content": "1 s897 (25) [30]",
    "labels": ["1neon", "s897"],
}


def test_out_of_bucket_preferred_id_is_rejected(monkeypatch):
    df = _load()
    monkeypatch.setattr(df, "_fetch_task_by_id", lambda tid: GOAL_CARD)
    # Bucket is empty (as it would be: the goal card isn't a 1neon task), and
    # the fetched-by-id task doesn't carry the required label either -- must
    # NOT be accepted as a match.
    result = df.match_todoist_task("1 s897", [], preferred_id=GOAL_CARD["id"],
                                   require_labels={"1neon"})
    assert result is None, (
        "a preferred_id that resolves to a task outside the required-label "
        "bucket must not be accepted as a match"
    )


def test_falls_through_to_name_match_in_correct_bucket(monkeypatch):
    """The real fix: rejecting the out-of-bucket id must fall through to the
    normal name search, which finds the ACTUAL 1neon card in the bucket."""
    df = _load()
    monkeypatch.setattr(df, "_fetch_task_by_id", lambda tid: GOAL_CARD)
    result = df.match_todoist_task("1 s897", [REAL_1NEON_CARD],
                                   preferred_id=GOAL_CARD["id"],
                                   require_labels={"1neon"})
    assert result is not None and result["id"] == REAL_1NEON_CARD["id"], (
        "must fall through to find the real 1neon card by name once the "
        "out-of-bucket preferred_id is rejected"
    )


def test_in_bucket_preferred_id_still_accepted(monkeypatch):
    """Regression safety (2026-07-24 stale-cache fix must keep working): a
    preferred_id that genuinely resolves to a task carrying the required
    label (just missing from the passed-in bucket list, e.g. a stale cache)
    is still trusted."""
    df = _load()
    monkeypatch.setattr(df, "_fetch_task_by_id", lambda tid: REAL_1NEON_CARD)
    result = df.match_todoist_task("1 s897", [],
                                   preferred_id=REAL_1NEON_CARD["id"],
                                   require_labels={"1neon"})
    assert result is not None and result["id"] == REAL_1NEON_CARD["id"]


def test_no_require_labels_keeps_old_unconditional_behavior(monkeypatch):
    """Step 0.3's broad `all_tasks` search passes no require_labels -- must
    keep accepting any fetched-by-id task, unrestricted (its original,
    intentionally-generic behavior)."""
    df = _load()
    monkeypatch.setattr(df, "_fetch_task_by_id", lambda tid: GOAL_CARD)
    result = df.match_todoist_task("1 s897", [], preferred_id=GOAL_CARD["id"])
    assert result is not None and result["id"] == GOAL_CARD["id"]


def test_1n_step_passes_1neon_require_label():
    src = (_HERE / "did-fast.py").read_text()
    idx = src.index("neon_1n_tasks = tq.get(\"1neon\"")
    snippet = src[idx:idx + 400]
    assert 'require_labels={"1neon"}' in snippet, (
        "Step 0.2's 1n+ Todoist match must restrict the id-fetch fallback "
        "to tasks actually labeled 1neon"
    )


def test_0n_step_passes_0neon_require_labels():
    src = (_HERE / "did-fast.py").read_text()
    idx = src.index("neon_tasks = tq.get(\"0neon\"")
    snippet = src[idx:idx + 400]
    assert 'require_labels={"0neon", "夜neon"}' in snippet, (
        "Step 0.1's 0n Todoist match must restrict the id-fetch fallback "
        "to tasks actually labeled 0neon/夜neon"
    )
