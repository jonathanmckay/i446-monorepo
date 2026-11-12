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


def _handoff(ct, monkeypatch, ix_out, local_ts=None, rc=0):
    import os, subprocess as sp
    monkeypatch.setenv('CC_DRY', '1')
    monkeypatch.delenv('CC_HOST', raising=False)
    monkeypatch.setattr(ct.sys, 'argv', ['cc'])
    monkeypatch.setattr('socket.gethostname', lambda: 'straylight')   # ix_handoff never hops when run ON ix
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == 'ssh':
            return sp.CompletedProcess(cmd, rc, stdout=ix_out, stderr='')
        out = f'L\tlocal one\tidle\t{local_ts}\n' if local_ts is not None else ''
        return sp.CompletedProcess(cmd, 0 if local_ts is not None else 1, stdout=out, stderr='')
    monkeypatch.setattr(ct.subprocess, 'run', run)
    return calls


IX_LIVE = 'fbd23d23\tJanus and JM Reads\tlive\t2000\ncc-062745\n'


def test_off_lan_cc_r_attaches_to_newer_live_ix_session(ct, monkeypatch, capsys):
    """2026-10-22: away from home cc r only looked at this Mac and resumed a Straylight session
    instead of the phone session live on ix."""
    calls = _handoff(ct, monkeypatch, IX_LIVE, local_ts=1000)
    assert ct.ix_handoff(['r']) is True
    out = capsys.readouterr().out
    assert "would run: ['ssh', '-t', 'ix', '--'" in out and 'exec tmux attach -d -t cc-062745' in out
    assert any(c[0] == 'ssh' and '--latest' in c[-1] for c in calls)


def test_off_lan_cc_r_stays_local_when_this_mac_is_newer(ct, monkeypatch):
    _handoff(ct, monkeypatch, IX_LIVE, local_ts=3000)
    assert ct.ix_handoff(['r']) is False


def test_off_lan_falls_through_when_ix_has_no_live_tmux(ct, monkeypatch):
    _handoff(ct, monkeypatch, 'fbd23d23\tJanus\tidle\t2000\n', local_ts=1000, rc=1)
    assert ct.ix_handoff(['r']) is False


def test_off_lan_title_attaches_without_time_check(ct, monkeypatch, capsys):
    calls = _handoff(ct, monkeypatch, IX_LIVE)
    assert ct.ix_handoff(['janus', 'jm']) is True
    assert any(c[0] == 'ssh' and '--title --no-pick janus jm' in c[-1] for c in calls)


def test_handoff_skipped_for_flags_and_forced_local(ct, monkeypatch):
    _handoff(ct, monkeypatch, IX_LIVE, local_ts=0)
    assert ct.ix_handoff(['--resume', 'x']) is False and ct.ix_handoff([]) is False
    monkeypatch.setenv('CC_HOST', 'local')
    assert ct.ix_handoff(['r']) is False


def test_cc_r_never_resumes_a_session_open_here(ct):
    import inspect
    assert "'--latest', '--not-live'" in inspect.getsource(ct.resume_args)


# ── cmux resume bindings / `cc --reattach` (2026-11-12, "CMUX on restart") ──────────────────

def test_reattach_target_parses_both_forms(ct):
    assert ct.reattach_target(['--reattach', 'cc-taxes']) == 'cc-taxes'
    assert ct.reattach_target(['--reattach=cc-091533']) == 'cc-091533'
    assert ct.reattach_target(['taxes', 'pdf']) is None
    assert ct.reattach_target(['--reattach']) is None


def test_reattach_shell_only_attaches(ct):
    """The bound command must be the attach-only path, never `cc <title>`: with ix unreachable at
    restart the title (or a dtd agent's whole prompt) would replay as a fresh conversation."""
    sh = ct.reattach_shell("cc-fix the thing")
    assert sh.endswith("--reattach 'cc-fix the thing'")
    assert sh.startswith(str(ct.CC_PATH))
    assert 'fix the thing' not in sh.replace("'cc-fix the thing'", '')


def test_cmux_resume_noop_outside_surface(ct, monkeypatch):
    monkeypatch.delenv('CMUX_SURFACE_ID', raising=False)
    calls = []
    monkeypatch.setattr(ct.subprocess, 'run', lambda *a, **k: calls.append(a))
    ct.cmux_resume_bind('cc-x')
    ct.cmux_resume_clear('cc-x')
    assert calls == []


def test_cmux_resume_bind_calls_helper_inside_surface(ct, monkeypatch):
    monkeypatch.setenv('CMUX_SURFACE_ID', '00000000-0000-0000-0000-000000000000')
    monkeypatch.setattr(ct, 'RESUME_HELPER', Path(__file__))   # exists() must be true
    calls = []
    monkeypatch.setattr(ct.subprocess, 'run', lambda argv, **k: calls.append(argv))
    ct.cmux_resume_bind('cc-taxes')
    assert calls and calls[0][2:4] == ['set', 'cc-taxes'] and calls[0][4] == ct.reattach_shell('cc-taxes')
    ct.cmux_resume_clear('')
    assert len(calls) == 1          # empty checkpoint: nothing to clear


def test_local_tmux_inner_exports_checkpoint(ct, monkeypatch, capsys):
    """release_tab clears the binding by CC_RESUME_CHECKPOINT, so the inner cc must receive it."""
    monkeypatch.setenv('CC_DRY', '1')
    monkeypatch.delenv('CMUX_SURFACE_ID', raising=False)
    monkeypatch.setattr(ct, 'live_attach_probe', lambda args: '')
    ct.exec_in_local_tmux(['taxes', 'pdf'])
    out = capsys.readouterr().out
    assert 'CC_RESUME_CHECKPOINT=cc-taxes-pdf' in out
