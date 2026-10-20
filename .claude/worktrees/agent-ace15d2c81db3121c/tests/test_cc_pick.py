"""Tests for tools/cs/cc-pick (the Termius session picker)."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'i446-monorepo' / 'tools' / 'cs' / 'cc-pick'


@pytest.fixture(scope='module')
def cp():
    loader = importlib.machinery.SourceFileLoader('cc_pick', str(SCRIPT))
    spec = importlib.util.spec_from_loader('cc_pick', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_new_session_launches_bare_cc(cp):
    assert cp.launch_args(cp.NEW, False, '') == []


def test_live_session_is_forked_with_marked_title(cp):
    """A session open in a cmux pane (or another client) must never be resumed in place."""
    args = cp.launch_args('a419abb3-7a25-449e-9c71-c3a9f9e3988d', True, 'Taxes PDF review')
    assert args[:2] == ['--resume', 'a419abb3-7a25-449e-9c71-c3a9f9e3988d']
    assert '--fork-session' in args and args[-1] == 'Taxes PDF review ⑂'


def test_idle_session_is_resumed_in_place(cp):
    assert cp.launch_args('abc', False, 'anything') == ['--resume', 'abc']


def test_remote_hosts_env_override(cp, monkeypatch):
    monkeypatch.setenv('CC_PICK_HOSTS', 'straylight,ix')
    assert cp.remote_hosts() == ['straylight', 'ix']
    monkeypatch.setenv('CC_PICK_HOSTS', '')
    assert cp.remote_hosts() == []


def test_remote_hosts_default_depends_on_hostname(cp, monkeypatch):
    monkeypatch.delenv('CC_PICK_HOSTS', raising=False)
    monkeypatch.setattr(cp.socket, 'gethostname', lambda: 'Straylight-Refit.local')
    assert cp.remote_hosts() == []
    monkeypatch.setattr(cp.socket, 'gethostname', lambda: 'ix.local')
    assert cp.remote_hosts() == ['straylight']


def test_list_is_tsv_with_five_columns(cp, capsys):
    cp.main(['--list'])
    lines = [l for l in capsys.readouterr().out.splitlines() if l]
    assert lines, 'expected at least one indexed session'
    for l in lines:
        cols = l.split('\t')
        assert len(cols) == 5 and cols[2] in ('live', 'idle') and cols[3].endswith('.jsonl')


# --- phone picker: 4 rows max, Haiku topic labels (JM 2026-10-18) -----------

def _row(i, live=False, host=None):
    return (f'row{i}', f'sid{i}', live, f'/p/{i}.jsonl', f'title {i}', host)


def test_picker_shows_at_most_four_sessions(cp):
    remote = [_row(1, True, 'straylight')]
    local = [_row(i) for i in range(2, 12)]
    rows = cp.cap_rows(remote, local)
    assert len(rows) == 4
    assert [r[1] for r in rows] == ['sid1', 'sid2', 'sid3', 'sid4'], 'remote panes first, then local in order'
    assert cp.MAX_ROWS == 4


def test_display_title_uses_cached_haiku_label_only(cp, monkeypatch):
    """A pick must never wait on an API call: only the precomputed cache is read."""
    class SS:
        CAP = 28
        calls = []

        @staticmethod
        def cached_short(sid, title):
            SS.calls.append((sid, title))
            return 'Neon -1n reconcile' if sid == 'hit' else None

        @staticmethod
        def ensure_short(*a, **k):
            raise AssertionError('must not compute at pick time')

    monkeypatch.setattr(cp, '_SS', SS)
    assert cp.display_title('hit', 'fix the -1n habits for last cycle that did not count') == 'Neon -1n reconcile'
    long = 'x' * 60
    assert cp.display_title('miss', long) == 'x' * 28, 'fallback is the raw title at the same width'


def test_local_rows_labels_use_display_title(cp, monkeypatch):
    class FakeCS:
        @staticmethod
        def load_index():
            return {'/p/a.jsonl': {'id': 'a', 'custom': None, 'ai': 'A very long ai title that overflows a phone column', 'prompt': '', 'mtime': 1}}

        @staticmethod
        def cmux_numbers():
            return {}

        @staticmethod
        def title_of(r):
            return r['ai']

        @staticmethod
        def mark_of(r):
            return '😈'

        @staticmethod
        def age(ts):
            return '1d'

    monkeypatch.setattr(cp, 'live_ids', lambda: set())
    monkeypatch.setattr(cp, 'display_title', lambda sid, title: 'ai title overflow')
    rows = cp.local_rows(FakeCS)
    assert rows[0][0].endswith('😈 ai title overflow')
    assert rows[0][4] == FakeCS.title_of(None if False else {'ai': 'A very long ai title that overflows a phone column'}), 'full title still travels with the row for the fork name'


def test_session_title_hook_precomputes_label():
    hook = (Path.home() / 'i446-monorepo' / 'scripts' / 'session-title.sh').read_text()
    assert 'session_short.py' in hook and '&)' in hook, 'hook must precompute the label, detached'
