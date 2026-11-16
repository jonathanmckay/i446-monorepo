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


def _mh():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('mh', pathlib.Path(__file__).parent / 'make_heatmap.py')
    mh = importlib.util.module_from_spec(spec); spec.loader.exec_module(mh)
    return mh


def test_headless_prompt_is_not_desk():
    """2026-11-16: the hourly d357-auto-file cron (`claude --print`) logged two
    prompts per hour and lit 🖥️ in all 63 blocks of Nov 9-15. A prompt row
    with tty False carries no device signal."""
    mh = _mh()
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=False) is None
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=False, ssh_from='192.168.1.75') is None
    assert _cls(mh, host='ix', metric='prompt', via='claude', tty=True) == mh.DESK


def test_legacy_headless_sessions_from_transcripts(tmp_path):
    """Rows logged before the hook fix all say tty=True; the transcript's
    entrypoint decides. Other hosts, post-fix rows and missing transcripts
    are left as logged."""
    import datetime as dt
    mh = _mh()
    proj = tmp_path / '-Users-mckay-vault'; proj.mkdir()
    (proj / 'cron-sid.jsonl').write_text('{"type":"session-owner"}\n{"type":"user","entrypoint":"sdk-cli"}\n')
    (proj / 'typed-sid.jsonl').write_text('{"type":"user","entrypoint":"cli"}\n')
    (proj / 'late-sid.jsonl').write_text('{"type":"user","entrypoint":"sdk-cli"}\n')
    (proj / 'laptop-sid.jsonl').write_text('{"type":"user","entrypoint":"sdk-cli"}\n')
    old = mh.PROMPT_TTY_FIXED - dt.timedelta(days=3)
    new = mh.PROMPT_TTY_FIXED + dt.timedelta(hours=1)
    row = lambda sid, host='ix': {'metric': 'prompt', 'via': 'claude', 'host': host, 'ext_id': sid, 'tty': True}
    rows = [(row('cron-sid'), old), (row('typed-sid'), old), (row('gone-sid'), old),
            (row('late-sid'), new), (row('laptop-sid', 'straylight-refit'), old),
            ({'metric': 'task', 'via': 'claude', 'host': 'ix', 'ext_id': 'cron-sid'}, old)]
    assert mh.legacy_headless_sessions(rows, 'ix', projects=str(tmp_path)) == {'cron-sid'}


def test_time_log_descriptions_prefix_match():
    """2026-11-16: `0l, food log` and `0t sleep backfill` are time-log entries;
    the old exact-set match dropped them."""
    mh = _mh()
    for d in ('-1l', '0l', '-1t', '0t', '0l, food log', '0t sleep backfill', '😈 -1l'):
        assert mh.GOALS_T_RE.match(d), d
    for d in ('0log', '0t2', 'ibx', 'do -1l later', '0l.5'):
        assert not mh.GOALS_T_RE.match(d), d


def test_toggl_window_uses_bare_dates(monkeypatch):
    """2026-11-16: a hard-coded `-07:00` offset slid every request back a day
    once DST ended (11-09..11-15 fetched 11-08..11-14)."""
    import datetime as dt, sys, types
    mh = _mh()
    seen = {}
    api = types.SimpleNamespace(get_entries=lambda s, e: seen.update(s=s, e=e) or [])
    pkg = types.ModuleType('toggl_server'); pkg.toggl_api = api
    cfg = types.ModuleType('toggl_server.config'); cfg.PROJECT_NAMES = {}
    monkeypatch.setitem(sys.modules, 'toggl_cli', types.ModuleType('toggl_cli'))
    monkeypatch.setitem(sys.modules, 'toggl_server', pkg)
    monkeypatch.setitem(sys.modules, 'toggl_server.toggl_api', api)
    monkeypatch.setitem(sys.modules, 'toggl_server.config', cfg)
    mh.toggl_entries(dt.date(2026, 11, 9), dt.date(2026, 11, 15))
    assert seen == {'s': '2026-11-09', 'e': '2026-11-16'}
