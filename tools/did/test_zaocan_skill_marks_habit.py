#!/usr/bin/env python3
"""Regression: /早餐 must mark the 早餐 0₦ habit done, not just log macros.

Bug (2026-08-04): /早餐 was a thin alias that only ran /ate's hcbi write —
it never called did-fast.py, so breakfast logged calories/protein but the
早餐 habit card never closed and dtd kept showing it as not-done.

/ate itself must NOT gain this call: it's the generic, branch-agnostic food
logger with no fixed habit column to close (only 早餐 has one). Wiring the
habit-close into /早餐 keeps /ate as the single source of truth for parsing
and writing, per its own header comment.
"""
import re
from pathlib import Path

SKILL = Path.home() / "i446-monorepo" / "skills" / "claude-skills" / "早餐" / "SKILL.md"
ATE_SKILL = Path.home() / "i446-monorepo" / "skills" / "claude-skills" / "ate" / "SKILL.md"

DID_FAST_ZAOCAN_RE = re.compile(r"did-fast\.py[^\n]*早餐")


def test_zaocan_skill_calls_did_fast():
    src = SKILL.read_text(encoding="utf-8")
    assert DID_FAST_ZAOCAN_RE.search(src), (
        "/早餐 SKILL.md has no did-fast.py \"早餐\" call — breakfast logging "
        "will not mark the 早餐 habit done in dtd"
    )


def test_ate_skill_stays_habit_agnostic():
    src = ATE_SKILL.read_text(encoding="utf-8")
    assert not DID_FAST_ZAOCAN_RE.search(src), (
        "/ate SKILL.md should not hardcode the 早餐 habit close — /ate is "
        "the generic, branch-agnostic logger; only /早餐 owns that habit"
    )


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
