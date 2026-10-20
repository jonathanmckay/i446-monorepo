"""cs.cmux_numbers() must still number panes when cmux's socket is unreachable (ssh/cron)."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'i446-monorepo' / 'tools' / 'cs' / 'cs.py'


@pytest.fixture
def cs(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader('cs_mod', str(SCRIPT))
    spec = importlib.util.spec_from_loader('cs_mod', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(mod, 'PANES_CACHE', tmp_path / 'pane-numbers.json')
    return mod


def test_falls_back_to_cache_filtered_to_live_processes(cs, monkeypatch):
    cs.PANES_CACHE.write_text(json.dumps({'a' * 36: 3, 'b' * 36: 4, 'c' * 36: 5}))
    monkeypatch.setattr(cs, '_cmux', lambda method: {})           # socket unreachable
    monkeypatch.setattr(cs, '_live_sids', lambda: {'a' * 36, 'c' * 36})
    monkeypatch.setattr(cs.shutil, 'which', lambda name: '/usr/local/bin/cmux')
    assert cs.cmux_numbers() == {'a' * 36: 3, 'c' * 36: 5}


def test_no_fallback_when_cmux_answers_with_no_panes(cs, monkeypatch):
    """An empty but successful answer (workspace with no Claude panes) must not resurrect the cache."""
    cs.PANES_CACHE.write_text(json.dumps({'a' * 36: 3}))
    monkeypatch.setattr(cs, '_cmux', lambda method: {'panes': [], 'workspace_id': 'w'})
    monkeypatch.setattr(cs, '_live_sids', lambda: {'a' * 36})
    monkeypatch.setattr(cs.shutil, 'which', lambda name: '/usr/local/bin/cmux')
    assert cs.cmux_numbers() == {}


def test_missing_cache_is_empty(cs, monkeypatch):
    monkeypatch.setattr(cs, '_cmux', lambda method: {})
    monkeypatch.setattr(cs.shutil, 'which', lambda name: '/usr/local/bin/cmux')
    assert cs.cmux_numbers() == {}
