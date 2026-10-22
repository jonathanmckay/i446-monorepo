"""Regression tests for the `cc` launcher (~/vault/i447/i446/claude-tracked)."""
import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT = Path.home() / 'vault' / 'i447' / 'i446' / 'claude-tracked'
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


@pytest.fixture(scope='module')
def ct():
    loader = importlib.machinery.SourceFileLoader('claude_tracked', str(SCRIPT))
    spec = importlib.util.spec_from_loader('claude_tracked', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_bare_launch_gets_explicit_session_id(ct):
    """A bare `cc` must pass `--session-id <uuid>` so `cs retitle` can map the pane to its session
    and number the cmux tab ('N · title'); without it the tab stayed unnumbered (2026-10-15)."""
    args = ct.ensure_session_id([])
    assert args[:1] == ['--session-id'] and UUID_RE.match(args[1])


def test_prompt_launch_keeps_prompt_and_adds_session_id(ct):
    args = ct.ensure_session_id(['write hello.py'])
    assert args[0] == 'write hello.py' and args[1] == '--session-id' and UUID_RE.match(args[2])


@pytest.mark.parametrize('args', [
    ['--resume', 'bc650922-88ab-486e-af05-dbaf12fa6724'],
    ['-r', 'bc650922-88ab-486e-af05-dbaf12fa6724'],
    ['--resume=bc650922-88ab-486e-af05-dbaf12fa6724'],
    ['-c'], ['--continue'],
    ['--session-id', 'fd050826-4008-46e6-8c4d-bef0e99b8e2c'],
])
def test_session_selecting_flags_pass_through_untouched(ct, args):
    assert ct.ensure_session_id(args) == args


def test_main_applies_ensure_session_id_after_resume_args(ct):
    """Structural guard: main() must call ensure_session_id on the args resume_args returns."""
    src = SCRIPT.read_text()
    i = src.index('claude_args, resumed = resume_args(claude_args)')
    j = src.index('claude_args = ensure_session_id(claude_args)')
    assert i < j < src.index("['claude'] + claude_args")


def test_cc_ls_lists_sessions_instead_of_fuzzy_forking(ct):
    """`cc ls` must hand off to `cs ls` (newest first). Before, 'ls' fell into resume_args'
    fuzzy matcher as a subsequence of 'goals' and forked a month-old session (2026-10-17)."""
    argv = ct.list_args(['ls'])
    assert argv is not None and argv[0].endswith('/cs') and argv[1:] == ['ls']


def test_cc_ls_passes_query_and_flags_to_cs(ct):
    assert ct.list_args(['ls', '--all'])[1:] == ['ls', '--all']
    assert ct.list_args(['list', 'taxes'])[1:] == ['ls', 'taxes']


@pytest.mark.parametrize('args', [[], ['taxes pdf review'], ['--resume', 'abc'], ['lsof the thing']])
def test_non_list_args_are_not_intercepted(ct, args):
    assert ct.list_args(args) is None


def test_main_checks_list_args_before_opening_a_db_session(ct):
    """Structural guard: the ls hand-off must run before init_session, so listing never records a
    phantom 'active' row in llm-sessions.db."""
    src = SCRIPT.read_text()
    main_src = src[src.index('def main():'):]
    assert main_src.index('list_args(') < main_src.index('init_session()')


def test_live_attach_probe_targets_latest_or_title(ct):
    """Phone → laptop handoff (2026-10-22): `cc r` / `cc <title>` attach to the tmux already
    holding that live session on ix instead of opening a new tmux that forks it."""
    r = ct.live_attach_probe(['r'])
    assert 'cs tmux --latest' in r and 'exec tmux attach -d -t "$t"' in r
    t = ct.live_attach_probe(['janus', "jm's"])
    assert "cs tmux --title --no-pick janus 'jm'\"'\"'s'" in t
    assert ct.live_attach_probe([]) == '' and ct.live_attach_probe(['--resume', 'x']) == ''


def test_exec_on_ix_probes_before_opening_a_new_tmux(ct, monkeypatch, capsys):
    monkeypatch.setenv('CC_DRY', '1')
    monkeypatch.setenv('CC_NO_MOSH', '1')
    ct.exec_on_ix(['r'])
    remote = capsys.readouterr().out.split('would run:', 1)[1]
    assert remote.index('cs tmux --latest') < remote.index('tmux new -d -s')


def test_ix_tmux_is_tagged_with_the_cmux_surface(ct, monkeypatch, capsys):
    """dtd draws 'N😈' from the pane number cs gives each cmux tab; a tab cc sent to ix only runs
    mosh/ssh locally, so cc tags the tmux session with the tab's surface id for cs to match
    (2026-10-22). The tag lands before the attach blocks, on both the live and the new path."""
    monkeypatch.setenv('CC_DRY', '1')
    monkeypatch.setenv('CC_NO_MOSH', '1')
    monkeypatch.setenv('CMUX_SURFACE_ID', 'DBF01CA2-00AD-4A13-9380-495A16FB0419')
    ct.exec_on_ix(['r'])
    remote = capsys.readouterr().out.split('would run:', 1)[1]
    tag = '@cmux_surface DBF01CA2-00AD-4A13-9380-495A16FB0419'
    assert remote.count(tag) == 2
    assert remote.index('-t "$t" ' + tag) < remote.index('exec tmux attach -d -t "$t"')
    assert remote.rindex(tag) < remote.rindex('exec tmux attach -d -t')


def test_exact_match_tmux_targets_are_quoted_for_zsh(ct, monkeypatch, capsys):
    """zsh expands a bare =word to a command path ('zsh: cc-x not found'), so tmux's exact-match
    '=name' target must be quoted in the remote shell string."""
    import os
    monkeypatch.delenv('CC_DRY', raising=False)
    monkeypatch.setenv('CC_NO_MOSH', '1')
    got = []
    monkeypatch.setattr(os, 'execvp', lambda f, argv: got.append(argv))
    ct.exec_on_ix(['taxes'])
    remote = got[0][-1]
    assert "has-session -t '=cc-taxes'" in remote and "attach -d -t '=cc-taxes'" in remote
    assert ' -t =cc' not in remote


def test_no_tag_outside_cmux(ct, monkeypatch):
    monkeypatch.delenv('CMUX_SURFACE_ID', raising=False)
    assert ct.surface_tag('x') == ''
