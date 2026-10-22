"""cmux tabs whose Claude runs on ix get a pane number, so dtd can draw 'N😈' on their claims
(2026-10-22). Their only local process is ssh/mosh; cs matches them to ix's live tmux sessions."""
import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'i446-monorepo' / 'tools' / 'cs' / 'cs.py'
SID_A = 'fbd23d23-7bc9-4e09-b7cc-49afcf0ea60f'
SID_B = '7007a78e-cd78-4037-8c01-fcaec24ac678'


@pytest.fixture
def cs(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader('cs_remote', str(SCRIPT))
    spec = importlib.util.spec_from_loader('cs_remote', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    monkeypatch.setattr(mod, 'IX_MAP_CACHE', tmp_path / 'ix-tmux-map.json')
    return mod


def _fake(tree, ps, ixmap, calls):
    def run(cmd, **kw):
        calls.append(cmd[0])
        if cmd[:2] == ['cmux', 'tree']:
            out = json.dumps(tree)
        elif cmd[0] == 'ps':
            out = ps
        elif cmd[0] == 'ssh':
            out = json.dumps(ixmap)
        else:
            out = ''
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr='')
    return run


TREE = {'windows': [{'workspaces': [{'panes': [{'surfaces': [
    {'ref': 'surface:7', 'tty': 'ttys005'}, {'ref': 'surface:8', 'tty': 'ttys006'},
    {'ref': 'surface:9', 'tty': 'ttys009'}]}]}]}]}
IXMAP = [{'tmux': 'cc-062745', 'surface': '', 'sid': SID_A, 'title': 'Janus and JM Reads'},
         {'tmux': 'cc-120951', 'surface': 'S9', 'sid': SID_B, 'title': 'cc r session'}]


def test_matches_by_tmux_name_and_by_surface_tag(cs, monkeypatch):
    ps = ('ttys005  ssh -t ix tmux attach -d -t cc-062745\n'
          'ttys006  -zsh\n'
          'ttys009  mosh-client -# ix.local -- zsh -lc t=$(cs tmux --latest) && exec tmux attach -d -t "$t"\n')
    calls = []
    monkeypatch.setattr(cs.subprocess, 'run', _fake(TREE, ps, IXMAP, calls))
    got = cs._remote_surfaces({'S7': 'surface:7', 'S8': 'surface:8', 'S9': 'surface:9'})
    assert got == {'S7': (SID_A, 'Janus and JM Reads'), 'S9': (SID_B, 'cc r session')}


def test_no_ssh_tab_means_no_ix_round_trip(cs, monkeypatch):
    calls = []
    monkeypatch.setattr(cs.subprocess, 'run', _fake(TREE, 'ttys005  -zsh\n', IXMAP, calls))
    assert cs._remote_surfaces({'S7': 'surface:7'}) == {}
    assert 'ssh' not in calls


def test_tmux_map_follows_one_nested_attach(cs, monkeypatch):
    monkeypatch.setattr(cs, '_tmux_claudes', lambda: {SID_A: 'cc-062745'})
    panes = '100 cc-062745\n200 cc-2043\n'
    ps = (f'  100     1 zsh\n  110   100 claude --session-id {SID_A}\n'
          '  200     1 zsh\n  210   200 ssh -t ix tmux attach -d -t cc-062745\n')

    def run(cmd, **kw):
        out = {'list-sessions': 'cc-062745\t\ncc-2043\tS8\n', 'list-panes': panes}.get(cmd[1], ps) if cmd[0] == 'tmux' else ps
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr='')
    monkeypatch.setattr(cs.subprocess, 'run', run)
    m = {e['tmux']: e for e in cs.tmux_map({})}
    assert m['cc-2043']['sid'] == SID_A and m['cc-2043']['surface'] == 'S8'
