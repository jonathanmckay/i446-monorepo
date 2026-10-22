"""cs --latest ranks by JM's last typed prompt, and cs tmux finds the tmux holding a live session
(phone → laptop handoff, 2026-10-22)."""
import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'i446-monorepo' / 'tools' / 'cs' / 'cs.py'


@pytest.fixture
def cs():
    loader = importlib.machinery.SourceFileLoader('cs_latest', str(SCRIPT))
    spec = importlib.util.spec_from_loader('cs_latest', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _u(ts, content, **kw):
    return json.dumps({'type': 'user', 'timestamp': ts, 'message': {'role': 'user', 'content': content}, **kw},
                      separators=(',', ':'))   # compact, as Claude Code writes it


def test_last_user_skips_agent_and_injected_records(cs, tmp_path):
    f = tmp_path / 's.jsonl'
    f.write_text('\n'.join([
        _u('2026-10-22T17:00:00.000Z', 'make the janus changes'),
        json.dumps({'type': 'assistant', 'timestamp': '2026-10-22T17:30:00.000Z', 'message': {}}),
        _u('2026-10-22T17:31:00.000Z', [{'type': 'tool_result', 'content': 'ok'}]),
        _u('2026-10-22T17:32:00.000Z', '<task-notification><task-id>x</task-id></task-notification>'),
        _u('2026-10-22T17:33:00.000Z', [{'type': 'text', 'text': 'Base directory for this skill'}], isMeta=True),
        _u('2026-10-22T17:34:00.000Z', 'sub', isSidechain=True),
        _u('2026-10-22T17:35:00.000Z', '<local-command-stdout>done</local-command-stdout>'),
    ]) + '\n')
    assert cs.last_user_ts(f) == pytest.approx(1792688400.0)   # 17:00Z, not any later record


def test_slash_commands_and_bang_lines_count_as_input(cs, tmp_path):
    f = tmp_path / 's.jsonl'
    f.write_text(_u('2026-10-22T17:00:00.000Z', 'hi') + '\n'
                 + _u('2026-10-22T17:10:00.000Z', '<command-message>did</command-message>') + '\n')
    assert cs.last_user_ts(f) == pytest.approx(1792688400.0 + 600)


def test_latest_prefers_last_typed_over_last_written(cs, monkeypatch, capsys):
    idx = {'a': {'id': 'A' * 36, 'mtime': 2000.0, 'last_user': 100.0, 'custom': 'agent busy', 'ai': '', 'prompt': ''},
           'b': {'id': 'B' * 36, 'mtime': 1500.0, 'last_user': 900.0, 'custom': 'typed last', 'ai': '', 'prompt': ''},
           'c': {'id': 'C' * 36, 'mtime': 3000.0, 'last_user': 0.0, 'custom': 'no prompts', 'ai': '', 'prompt': ''}}
    monkeypatch.setattr(cs, 'load_index', lambda full=False: idx)
    cs.main(['id', '--latest'])
    assert capsys.readouterr().out.strip() == 'B' * 36


def test_tmux_session_of_walks_up_to_the_pane(cs, monkeypatch):
    sid = 'fbd23d23-7bc9-4e09-b7cc-49afcf0ea60f'
    ps = (f"  100     1 tmux\n  200   100 zsh -lic exec claude-tracked\n"
          f"  300   200 python3 claude-tracked\n  400   300 claude --session-id {sid}\n"
          f"  500     1 claude --session-id {'d' * 8}-0000-0000-0000-000000000000\n")

    def fake_run(cmd, **kw):
        out = ps if cmd[0] == 'ps' else '200 cc-062745\n999 other\n'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr='')
    monkeypatch.setattr(cs.subprocess, 'run', fake_run)
    assert cs.tmux_session_of(sid) == 'cc-062745'
    assert cs.tmux_session_of('e' * 8 + '-0000-0000-0000-000000000000') is None


def test_not_live_skips_the_session_open_here(cs, monkeypatch, capsys):
    """cc r (2026-10-22): the session JM is typing into is always the latest and already open on
    this screen; --not-live takes the most recent one that is not."""
    idx = {'a': {'id': 'A' * 36, 'mtime': 2000.0, 'last_user': 900.0, 'custom': 'open here', 'ai': '', 'prompt': ''},
           'b': {'id': 'B' * 36, 'mtime': 1500.0, 'last_user': 500.0, 'custom': 'left on phone', 'ai': '', 'prompt': ''}}
    monkeypatch.setattr(cs, 'load_index', lambda full=False: idx)
    monkeypatch.setattr(cs, '_live_sids', lambda: {'A' * 36})
    cs.main(['id', '--latest', '--not-live'])
    assert capsys.readouterr().out.strip() == 'B' * 36
    cs.main(['id', '--latest'])
    assert capsys.readouterr().out.strip() == 'A' * 36
