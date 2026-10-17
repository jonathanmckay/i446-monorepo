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
