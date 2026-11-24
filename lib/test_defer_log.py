#!/usr/bin/env python3
"""defer_log.py: count real deferrals of one-off tasks across hosts."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import defer_log as dl  # noqa: E402

TODAY = date(2026, 11, 23)


def test_only_a_later_day_on_a_one_off_counts(tmp_path):
    assert dl.record("a", "x [10]", ["m5x2"], "2026-11-24", TODAY, tmp_path)
    assert not dl.record("a", "x [10]", ["m5x2"], "2026-11-23", TODAY, tmp_path)   # delay by 0
    assert not dl.record("h", "family 11.7", ["1neon"], "2026-11-24", TODAY, tmp_path)
    assert not dl.record("", "x", [], "2026-11-24", TODAY, tmp_path)
    assert dl.record("b", "y", [], "next monday", TODAY, tmp_path)                 # passed through
    assert dl.counts(tmp_path) == {"a": 1, "b": 1}


def test_undo_takes_one_back_and_hosts_are_summed(tmp_path):
    for _ in range(3):
        dl.record("a", "x", [], "2026-11-25", TODAY, tmp_path)
    dl.undo("a", tmp_path)
    (tmp_path / "defer-log-other-host.jsonl").write_text(
        '{"id": "a", "day": "2026-11-20", "target": "2026-11-21"}\nnot json\n')
    assert dl.counts(tmp_path) == {"a": 3}


def test_missing_folder_never_raises(tmp_path):
    assert dl.record("a", "x", [], "2026-11-25", TODAY, tmp_path / "nope") is False
    assert dl.counts(tmp_path / "nope") == {}
