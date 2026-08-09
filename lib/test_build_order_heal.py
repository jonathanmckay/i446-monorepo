"""Regression (user report 2026-07-29: "I did all -1n for 辰 but only 7 -1n
points got recorded" — then 巳 at 4/13 an hour later): build-order.md has
concurrent writers on two machines; Syncthing keeps the race's loser only as
a LOCAL .sync-conflict copy, so ritual stamps written on the losing side
vanish, and the daemon's reconcile then SETs 0分!P from the survivors —
converting a file race into a permanent points loss. heal() union-merges the
same-day conflict copies' stamps back into the canonical file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_order_heal as bh  # noqa: E402

CANON = """---
date: 2026-07-29
---
## -1₲
- 卯 (360min) 😈
- 辰 ☀️ ⏱️ 📧 (68min) 😈
    - [ ] goal a {5}
- 巳 ☀️ 🎯
- 午
"""

CONFLICT = """---
date: 2026-07-29
---
## -1₲
- 卯 (360min) 😈
- 辰 ☀️ 🎯 ✅
- 巳
- 午
"""


def test_merge_unions_missing_stamps_preserving_line_shape():
    merged, added = bh.merge_stamps(CANON, CONFLICT)
    assert sorted(added) == ["辰+✅", "辰+🎯"]
    line = next(l for l in merged.split("\n") if l.startswith("- 辰"))
    for e in ("☀️", "⏱️", "📧", "🎯", "✅"):
        assert e in line
    assert line.index("🎯") < line.index("("), "insert lands before the (Nmin) annotation"
    assert line.endswith("😈"), "trailing marker preserved"


def test_merge_never_removes_stamps():
    # Conflict copy has FEWER stamps on 巳 — canonical's must survive.
    merged, added = bh.merge_stamps(CANON, CONFLICT)
    line = next(l for l in merged.split("\n") if l.startswith("- 巳"))
    assert "☀️" in line and "🎯" in line
    assert not any(a.startswith("巳") for a in added)


def test_heal_merges_same_day_and_deletes_conflict(tmp_path):
    bo = tmp_path / "build-order.md"
    bo.write_text(CANON, encoding="utf-8")
    cf = tmp_path / "build-order.sync-conflict-20260729-073026-D6XJHOP.md"
    cf.write_text(CONFLICT, encoding="utf-8")
    res = bh.heal(bo)
    assert res["merged"] and not res["errors"]
    assert "🎯" in next(l for l in bo.read_text().split("\n") if l.startswith("- 辰"))
    assert not cf.exists(), "processed conflict copy is removed"


def test_heal_skips_other_days_conflicts(tmp_path):
    bo = tmp_path / "build-order.md"
    bo.write_text(CANON, encoding="utf-8")
    stale = CONFLICT.replace("date: 2026-07-29", "date: 2026-07-28")
    cf = tmp_path / "build-order.sync-conflict-20260728-080448-D6XJHOP.md"
    cf.write_text(stale, encoding="utf-8")
    res = bh.heal(bo)
    assert res["skipped"] == [cf.name]
    assert cf.exists(), "another day's conflict copy is left alone"
    assert "🎯" not in next(l for l in bo.read_text().split("\n") if l.startswith("- 辰")), \
        "yesterday's stamps must never bleed into today's blocks"


def test_writers_call_heal_before_acting():
    root = Path.home() / "i446-monorepo"
    ritual = (root / "tools/did/did-fast.py").read_text()
    daemon = (root / "scripts/build-order-daemon.py").read_text()
    assert "build_order_heal" in ritual, "run_ritual heals before stamping"
    i = daemon.index("def run_lock_and_mark")
    body = daemon[i:daemon.index("\ndef ", i + 10)]
    assert "build_order_heal" in body, "lock-and-mark heals before reconciling"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
