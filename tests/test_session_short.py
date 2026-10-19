"""tools/cs/session_short.py: cached Haiku topic labels for cc-pick on the phone."""
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'i446-monorepo' / 'tools' / 'cs' / 'session_short.py'


@pytest.fixture
def ss(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader('session_short_t', str(SCRIPT))
    spec = importlib.util.spec_from_loader('session_short_t', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(mod, 'SIDECAR', tmp_path / 'session-shortnames.json')
    return mod


def test_computes_once_and_caches_by_title_hash(ss):
    calls = []

    def fake(title):
        calls.append(title)
        return 'Suzzallo hours lookup'

    assert ss.ensure_short('s1', 'what are the hours of suzzallo library tomorrow?', compute=fake) == 'Suzzallo hours lookup'
    assert ss.ensure_short('s1', 'what are the hours of suzzallo library tomorrow?', compute=fake) == 'Suzzallo hours lookup'
    assert calls == ['what are the hours of suzzallo library tomorrow?'], 'second call served from cache'
    assert ss.cached_short('s1', 'what are the hours of suzzallo library tomorrow?') == 'Suzzallo hours lookup'
    # A retitled session (e.g. /rename) invalidates the cached label.
    assert ss.cached_short('s1', 'renamed') is None
    assert ss.ensure_short('s1', 'renamed', compute=lambda t: 'Renamed') == 'Renamed'
    assert calls == ['what are the hours of suzzallo library tomorrow?']


def test_failed_compute_caches_nothing(ss):
    assert ss.ensure_short('s2', 'anything', compute=lambda t: None) is None
    assert ss.cached_short('s2', 'anything') is None
    assert not ss.SIDECAR.exists()


def test_cap_is_phone_width(ss):
    assert ss.CAP <= 30


def test_trim_never_cuts_mid_word(ss):
    assert ss.trim('Troubleshoot cmux availability on ix', 28) == 'Troubleshoot cmux'
    assert ss.trim('short enough', 28) == 'short enough'
    assert ss.trim('x' * 40, 28) == 'x' * 28, 'one unbroken token: hard cut'
