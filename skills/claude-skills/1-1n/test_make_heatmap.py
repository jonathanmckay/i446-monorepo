#!/usr/bin/env python3
"""Regression tests for make_heatmap.py."""
import os, sys, datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
import make_heatmap as mh


def test_prune_future_clears_blocks_that_havent_started():
    """Bug: today's row showed rituals (☀️/🎯) for blocks that hadn't happened
    yet, because the live build order pre-stamps future block headers. A block
    that hasn't started cannot have a completed ritual."""
    now = dt.datetime.now(mh.PT)
    today = now.date()

    grid = defaultdict(lambda: defaultdict(set))
    # Light every block today with a goal-set marker (what add_goals does off
    # the pre-written live build order).
    for _, b in mh.BLOCKS:
        grid[today][b].add('🎯')

    mh.prune_future(grid)

    for start_h, b in mh.BLOCKS:
        if start_h > now.hour:
            assert b not in grid[today] or not grid[today][b], (
                f'future block {b} (starts {start_h}:00, now {now.hour}:xx) '
                f'should be pruned')
        else:
            assert '🎯' in grid[today][b], (
                f'started block {b} (starts {start_h}:00) must be kept')


def test_prune_future_leaves_past_days_untouched():
    """Only the current day has future blocks; prior days are fully realized."""
    now = dt.datetime.now(mh.PT)
    yesterday = now.date() - dt.timedelta(days=1)

    grid = defaultdict(lambda: defaultdict(set))
    for _, b in mh.BLOCKS:
        grid[yesterday][b].add('🎯')

    mh.prune_future(grid)

    for _, b in mh.BLOCKS:
        assert '🎯' in grid[yesterday][b], f'past-day block {b} must survive'


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
