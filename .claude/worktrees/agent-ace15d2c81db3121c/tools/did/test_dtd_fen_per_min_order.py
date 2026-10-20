#!/usr/bin/env python3
"""Within a priority tier, dtd ranks plain tasks by 分 per minute (2026-10-10).

[N] / (N), no (N) counts as 30 minutes, no [N] sorts last; equal ratios keep
the by-id order test_dtd_stable_render_order.py pins. Runs the real list
generator extracted from dtd.sh.
"""
import tempfile
from pathlib import Path

from test_dtd_stable_render_order import _ids_in_order, _run_listgen, _task


def _order(tasks):
    with tempfile.TemporaryDirectory() as d:
        out = _run_listgen(Path(d), {"updated": "t", "关键路径": [], "today": tasks})
    ids = {t["id"] for t in tasks}
    return [i for i in _ids_in_order(out) if i in ids]


def test_higher_fen_per_minute_first_within_a_tier():
    tasks = [_task("A", "slow low (60) [5]"),        # 0.08/min
             _task("B", "fast high (10) [20]"),      # 2/min
             _task("C", "no estimate [30]"),         # 30/30 = 1/min
             _task("D", "no value (5)")]             # 0
    assert _order(tasks) == ["B", "C", "A", "D"]


def test_priority_still_beats_ratio():
    tasks = [_task("A", "great ratio (5) [50]", priority=1),
             _task("B", "poor ratio (60) [5]", priority=4)]   # API priority 4 = p1
    assert _order(tasks) == ["B", "A"]


def test_equal_ratio_keeps_id_order():
    tasks = [_task("Z", "same (10) [10]"), _task("M", "same too (20) [20]")]
    assert _order(tasks) == ["M", "Z"]
