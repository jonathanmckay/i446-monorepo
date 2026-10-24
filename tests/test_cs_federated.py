"""cs searches every Mac (2026-10-24: /fork on ix could not see the straylight session it named
and matched its own '/fork …' first prompt instead)."""
import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'i446-monorepo' / 'tools' / 'cs' / 'cs.py'
ME = '3156da86-9dd1-4415-94c3-15696a00d976'
TARGET = '07ff7460-6376-4113-b3b8-2390e432909c'
LOCAL_COPY_MTIME = 1000.0


def rec(sid, custom='', ai='', prompt='', mtime=2000.0):
    return {'v': 7, 'mtime': mtime, 'id': sid, 'custom': custom, 'ai': ai, 'cwd': '/Users/mckay',
            'prompt': prompt, 'proj': '-Users-mckay', 'claims': [], 'last_user': 0.0}


@pytest.fixture
def cs(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader('cs_fed', str(SCRIPT))
    spec = importlib.util.spec_from_loader('cs_fed', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(mod, 'PEER_DOWN', tmp_path / 'peer-down')
    monkeypatch.setattr(mod, 'self_host', lambda: 'ix')
    monkeypatch.delenv('CS_PEERS', raising=False)
    return mod


def fake_ssh(answers, calls):
    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == 'ssh':
            host = cmd[5]
            if host not in answers:
                return subprocess.CompletedProcess(cmd, 255, stdout='', stderr='timeout')
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(answers[host]), stderr='')
        return subprocess.CompletedProcess(cmd, 0, stdout='', stderr='')
    return run


def test_peers_skip_self(cs, monkeypatch):
    assert cs.peers() == ['straylight']
    monkeypatch.setattr(cs, 'self_host', lambda: 'straylight-refit')
    assert cs.peers() == ['ix']
    monkeypatch.setenv('CS_PEERS', '')
    assert cs.peers() == []


def test_peer_title_beats_own_fork_prompt(cs, monkeypatch):
    idx = {'a': rec(ME, prompt="/fork WA Driver's license")}
    remote = [{**rec(TARGET, ai="WA driver's license replacement"), 'score': 2.3, 'num': 4}]
    monkeypatch.setattr(cs.subprocess, 'run', fake_ssh({'straylight': remote}, []))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', ME)
    hits = cs.federated(idx, "WA Driver's license")
    assert [h[3]['id'] for h in hits] == [TARGET]
    assert hits[0][3]['peer'] == 'straylight'
    assert cs.host_of(hits[0][3]) == '@sl'


def test_bare_fork_by_own_id_still_resolves(cs, monkeypatch):
    idx = {'a': rec(ME, prompt='/fork')}
    monkeypatch.setattr(cs.subprocess, 'run', fake_ssh({'straylight': []}, []))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', ME)
    assert cs.federated(idx, ME)[0][3]['id'] == ME


def test_newer_copy_wins_dedupe(cs, monkeypatch):
    idx = {'a': rec(TARGET, ai='WA license', mtime=LOCAL_COPY_MTIME)}
    remote = [{**rec(TARGET, ai='WA license', mtime=5000.0), 'score': 2.3, 'num': None}]
    monkeypatch.setattr(cs.subprocess, 'run', fake_ssh({'straylight': remote}, []))
    hits = cs.federated(idx, 'WA license')
    assert len(hits) == 1 and hits[0][3].get('peer') == 'straylight'


def test_local_mode_never_sshes(cs, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, 'run', fake_ssh({}, calls))
    cs.federated({'a': rec(TARGET, ai='WA license')}, 'WA license', remote=False)
    assert not [c for c in calls if c[0] == 'ssh']


def test_dead_peer_is_skipped_for_a_minute(cs, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cs.subprocess, 'run', fake_ssh({}, calls))
    cs.remote_hits('x'); cs.remote_hits('x')
    assert len([c for c in calls if c[0] == 'ssh']) == 1
    assert 'straylight did not answer' in capsys.readouterr().err


def test_fork_title_does_not_outrank_original(cs):
    orig = rec(TARGET, ai="WA driver's license replacement")
    fork = rec(ME, custom="WA driver's license replacement ⑂", mtime=9999.0)
    assert cs.fuzzy("WA driver's license", orig) > cs.fuzzy("WA driver's license", fork)
