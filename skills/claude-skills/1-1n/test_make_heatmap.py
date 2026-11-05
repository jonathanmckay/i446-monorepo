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


# --- desk / mobile classifier (2026-11-02) ---
import ipaddress as _ip

def _cls(mh, **r):
    by_ip = {'100.72.38.25': 'mobile', '100.114.46.109': 'desk', '100.71.83.90': 'laptop',
             '192.168.1.75': 'laptop'}
    by_host = {'ix': 'desk', 'straylight-refit': 'laptop'}
    return mh.classify_row(r, by_ip, by_host, [_ip.ip_network('192.168.1.0/24')],
                           {'build-order-daemon'})


def test_classify_device_rules():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('mh', pathlib.Path(__file__).parent / 'make_heatmap.py')
    mh = importlib.util.module_from_spec(spec); spec.loader.exec_module(mh)
    D, M = mh.DESK, mh.MOBILE
    assert _cls(mh, host='ix', source='1p-app', via='janus-mobile') == M
    assert _cls(mh, host='ix', source='cli', via='build-order-daemon') is None
    assert _cls(mh, host='ix', source='cli', via='quick-close') == D          # pre-cutover row
    assert _cls(mh, host='ix', source='cli', via='did-fast', tty=False) is None
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=True, ssh_from='100.72.38.25') == M
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=True, ssh_from='192.168.1.75') == D
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=True, ssh_from='100.71.83.90') is None
    assert _cls(mh, host='straylight-refit', via='janus', tty=True, net='highgarden') == D
    assert _cls(mh, host='straylight-refit', via='janus', tty=True, net='other') is None
    assert _cls(mh, host='straylight-refit', via='janus') is None               # no net recorded


def test_ttyless_prompt_is_no_device_signal():
    # cron's hourly `claude -p` and Dream log prompts on ix with tty False
    # (scripts/ai-timing-log.sh, 2026-11-03); they must not paint 🖥️ desk.
    import make_heatmap as mh
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=False) is None
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=True) == mh.DESK
