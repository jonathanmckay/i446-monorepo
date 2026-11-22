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
    # exec_on_ix runs the transport as a child since 2026-11-14; patching os.execvp let a real ssh out
    monkeypatch.setattr(ct, 'run_transport', lambda cmd: got.append(cmd) or 0)
    monkeypatch.setattr(ct, 'after_ix_exit', lambda name, host: True)
    monkeypatch.setattr(ct, 'cmux_resume_bind', lambda name: None)
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


# --- /fin closes the tab, always (2026-11-22) -------------------------------------------------
# JM: "I want /fin to close the entire CMUX tab, all it does is start me in a new claude session."
# after_ix_exit used to close the surface only when the workspace held another Claude tab and
# otherwise relaunched a fresh thread in it; with one Claude tab per workspace that was every /fin.

def _fin_exit(ct, monkeypatch, marker=True, close_rc=0, surface='SURF-1', cmux='/x/cmux', tmp_log=None, shell_sent=False):
    """Run after_ix_exit with the ix marker faked; returns (result, cmux argvs, relaunches)."""
    import types
    if surface:
        monkeypatch.setenv('CMUX_SURFACE_ID', surface)
    else:
        monkeypatch.delenv('CMUX_SURFACE_ID', raising=False)
    monkeypatch.setattr(ct, 'fin_closed_on_ix', lambda name, host: marker)
    monkeypatch.setattr(ct, 'cmux_resume_clear', lambda name: None)
    monkeypatch.setattr(ct, 'cmux_bin', lambda: cmux, raising=False)
    monkeypatch.setattr(ct, 'end_tab_shell', lambda: {'sent': shell_sent}, raising=False)
    monkeypatch.setattr(ct, 'FIN_TAB_LOG', tmp_log or Path('/nonexistent-dir/fin.log'), raising=False)
    monkeypatch.setattr(ct, 'other_claude_tabs', lambda: [], raising=False)   # the lone-tab case
    relaunched, calls = [], []
    envs = _fin_exit.envs = []
    monkeypatch.setattr(ct, 'exec_on_ix', lambda *a, **k: relaunched.append(a))

    def fake_run(argv, **k):
        calls.append(list(argv))
        envs.append(k.get('env') or {})
        return types.SimpleNamespace(returncode=close_rc, stdout='', stderr='no such surface')
    monkeypatch.setattr(ct.subprocess, 'run', fake_run)
    return ct.after_ix_exit('cc-081834', 'ix'), calls, relaunched


def test_fin_closes_the_lone_claude_tab_instead_of_starting_a_fresh_thread(ct, monkeypatch):
    done, calls, relaunched = _fin_exit(ct, monkeypatch)
    assert done is True
    assert relaunched == []                                   # no new claude session in the tab
    assert calls[-1] == ['/x/cmux', 'close-surface', '--surface', 'SURF-1']
    assert [c for c in calls if c[1] == 'close-surface' and '--help' not in c] == calls[-1:]


def test_fin_close_failure_says_so_and_still_starts_no_thread(ct, monkeypatch, capsys):
    done, calls, relaunched = _fin_exit(ct, monkeypatch, close_rc=1)
    assert relaunched == [] and len([c for c in calls if c[1:2] == ['close-surface'] and '--help' not in c]) == 1
    assert 'close it by hand' in capsys.readouterr().out
    assert done is False            # callers end the way a plain exit does


def test_fin_outside_cmux_never_calls_cmux(ct, monkeypatch):
    """No surface id (Terminal.app, phone): a flagless close-surface would take the focused tab."""
    done, calls, relaunched = _fin_exit(ct, monkeypatch, surface='')
    assert done is False and calls == [] and relaunched == []


def test_ordinary_ix_exit_closes_nothing(ct, monkeypatch):
    done, calls, relaunched = _fin_exit(ct, monkeypatch, marker=False)
    assert done is False and calls == [] and relaunched == []


def _marker_rc(ct, tmp_path, name, surface):
    import subprocess
    return subprocess.run(['zsh', '-c', ct.fin_marker_script(name, surface, marker_dir=str(tmp_path))],
                          capture_output=True, timeout=10).returncode


def test_fin_marker_found_by_surface_when_the_tab_attached_under_another_tmux_name(ct, tmp_path):
    """`cc r` names its transport cc-HHMMSS but attaches to the live session, so the marker /fin
    wrote is under that session's name; the surface id on line 2 is what ties it to this tab.
    Runs the real shell snippet (zsh, as on ix) against tmux names that do not exist."""
    (tmp_path / 'cc-zz-ended-thread').write_text('sid-1\nsurface=SURF-1\n')
    (tmp_path / 'cc-zz-other-tab').write_text('sid-2\nsurface=SURF-2\n')
    assert _marker_rc(ct, tmp_path, 'cc-zz-transport-name', 'SURF-1') == 0
    assert not (tmp_path / 'cc-zz-ended-thread').exists()          # consumed
    assert (tmp_path / 'cc-zz-other-tab').exists()                 # another tab's marker untouched
    assert _marker_rc(ct, tmp_path, 'cc-zz-transport-name', 'SURF-1') == 1   # nothing left for us
    assert _marker_rc(ct, tmp_path, 'cc-zz-other-tab', '') == 0              # by name, old one-line form too


def test_fin_marker_is_left_alone_while_its_tmux_session_is_alive(ct, tmp_path):
    import subprocess
    name = 'cc-zz-test-fin-alive'
    if subprocess.run(['tmux', 'new', '-d', '-s', name, 'sleep 30'], capture_output=True).returncode != 0:
        pytest.skip('no tmux server available')
    try:
        (tmp_path / name).write_text('sid\nsurface=SURF-9\n')
        assert _marker_rc(ct, tmp_path, name, 'SURF-9') == 1
        assert (tmp_path / name).exists()
    finally:
        subprocess.run(['tmux', 'kill-session', '-t', f'={name}'], capture_output=True)


def test_fin_closed_here_reads_the_session_id_from_a_two_line_marker(ct, monkeypatch, tmp_path):
    monkeypatch.setattr(ct, 'FIN_CLOSE_DIR', tmp_path)
    monkeypatch.setattr(ct, 'tmux_session_name', lambda: 'cc-x')
    (tmp_path / 'cc-x').write_text('sid-1\nsurface=SURF-1\n')
    assert ct.fin_closed_here('sid-1', now=(tmp_path / 'cc-x').stat().st_mtime + 10 * ct.FIN_CLOSE_GRACE_S)


def test_fin_tab_close_leaves_evidence_where_ix_can_read_it(ct, monkeypatch, tmp_path):
    """2026-11-22: "it closed the claude session, but not the cmux tab" and ix had nothing to say
    why. The close attempt, its rc and stderr, and the layout before it go to a synced log."""
    import json
    log = tmp_path / 'fin.log'
    _fin_exit(ct, monkeypatch, close_rc=1, tmp_log=log)
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [r['step'] for r in rows] == ['start', 'layout', 'close', 'end-shell']
    assert rows[0]['CMUX_SURFACE_ID'] == 'SURF-1'
    assert rows[2]['rc'] == 1 and rows[2]['err'] == 'no such surface'


def test_fin_tab_close_authenticates_to_the_cmux_socket(ct, monkeypatch, tmp_path):
    """2026-11-22, from the tab itself: "cc: could not close this tab (Error: auth_required:
    Authentication required. Send auth <password> first.)". The socket is password-protected and
    the close went out without CMUX_SOCKET_PASSWORD."""
    pw = tmp_path / 'socket-control-password'
    pw.write_text('s3cret\n')
    monkeypatch.setattr(ct, 'CMUX_PASSWORD_FILE', pw, raising=False)
    monkeypatch.delenv('CMUX_SOCKET_PASSWORD', raising=False)
    done, calls, _ = _fin_exit(ct, monkeypatch)
    assert done is True and calls[-1][1] == 'close-surface'
    assert _fin_exit.envs[-1].get('CMUX_SOCKET_PASSWORD') == 's3cret'
    monkeypatch.setenv('CMUX_SOCKET_PASSWORD', 'from-env')          # an explicit one wins
    _fin_exit(ct, monkeypatch)
    assert _fin_exit.envs[-1]['CMUX_SOCKET_PASSWORD'] == 'from-env'
    monkeypatch.delenv('CMUX_SOCKET_PASSWORD')
    monkeypatch.setattr(ct, 'CMUX_PASSWORD_FILE', tmp_path / 'absent')
    monkeypatch.setattr(ct, 'CMUX_CONFIG', tmp_path / 'no-config.json')
    _fin_exit(ct, monkeypatch)                                      # no file: call still goes out
    assert 'CMUX_SOCKET_PASSWORD' not in _fin_exit.envs[-1]


@pytest.mark.parametrize('text', [
    '{"automation": {"socketControlMode": "password", "socketPassword": "c0nfig"}}',
    '{\n  // jsonc\n  "automation": {"socketControlMode": "password", "socketPassword": "c0nfig",},\n}',
])
def test_cmux_password_falls_back_to_cmux_config(ct, monkeypatch, tmp_path, text):
    """2026-11-22, second run: the log said password=false. Straylight has no
    socket-control-password file; password mode keeps it in cmux.json (automation.socketPassword)."""
    cfg = tmp_path / 'cmux.json'
    cfg.write_text(text)
    monkeypatch.setattr(ct, 'CMUX_CONFIG', cfg)
    monkeypatch.setattr(ct, 'CMUX_PASSWORD_FILE', tmp_path / 'absent')
    monkeypatch.delenv('CMUX_SOCKET_PASSWORD', raising=False)
    assert ct.cmux_password() == ('c0nfig', 'config')
    assert ct.cmux_env()['CMUX_SOCKET_PASSWORD'] == 'c0nfig'
    facts = ct.cmux_auth_facts('/usr/bin/true')
    assert facts['mode'] == 'password' and facts['config_has_password'] is True
    assert 'c0nfig' not in repr(facts)                      # the log never carries the password


def test_fin_ends_the_tab_shell_when_the_cmux_socket_refuses(ct, monkeypatch, capsys):
    """2026-11-22, third run: Straylight's socket is in password mode with no password the CLI can
    find, so close-surface can never succeed there. The tab still has to close."""
    done, calls, relaunched = _fin_exit(ct, monkeypatch, close_rc=1, shell_sent=True)
    assert done is True and relaunched == []
    assert 'close it by hand' not in capsys.readouterr().out


def test_end_tab_shell_hangs_up_only_a_parent_shell_outside_tmux(ct, monkeypatch):
    import os, signal, types
    killed = []
    monkeypatch.setattr(os, 'getppid', lambda: 4242)
    monkeypatch.setattr(os, 'kill', lambda pid, sig: killed.append((pid, sig)))
    parent = {'comm': '-zsh'}
    monkeypatch.setattr(ct.subprocess, 'run',
                        lambda argv, **k: types.SimpleNamespace(stdout=parent['comm'] + '\n', returncode=0))
    monkeypatch.delenv('TMUX', raising=False)
    assert ct.end_tab_shell()['sent'] is True and killed == [(4242, signal.SIGHUP)]
    parent['comm'] = '/usr/bin/python3'
    assert ct.end_tab_shell()['sent'] is False                 # a script started us: leave it
    parent['comm'] = '/bin/zsh'
    monkeypatch.setenv('TMUX', '/tmp/tmux-501/default,1,0')
    assert ct.end_tab_shell()['sent'] is False                 # local tmux pane, not the tab's shell
    assert len(killed) == 1


# --- /fin behaves the same everywhere (2026-11-22, JM: "I'd like to keep consistent behavior") ---

def test_local_thread_keeps_the_launcher_alive_and_closes_its_tab_after_fin(ct, monkeypatch, capsys):
    """exec_in_local_tmux used to exec tmux, so nothing was left to close the tab of a /fin'd
    `cc --local` thread."""
    import os
    monkeypatch.delenv('CC_DRY', raising=False)
    monkeypatch.delenv('CMUX_SURFACE_ID', raising=False)
    monkeypatch.setattr(ct, 'live_attach_probe', lambda args: '')
    monkeypatch.setattr(ct, 'cmux_resume_bind', lambda name: None)
    monkeypatch.setattr(os, 'execvp', lambda *a: pytest.fail('launcher replaced itself'))
    ran, closed = [], []
    monkeypatch.setattr(ct, 'run_transport', lambda cmd: ran.append(cmd) or 0)
    monkeypatch.setattr(ct, 'fin_closed_locally', lambda name: True)
    monkeypatch.setattr(ct, 'close_this_tab', lambda name: closed.append(name) or True)
    ct.exec_in_local_tmux(['taxes', 'pdf'])
    assert len(ran) == 1 and closed == ['cc-taxes-pdf']
    monkeypatch.setattr(ct, 'fin_closed_locally', lambda name: False)      # plain exit: no close
    with pytest.raises(SystemExit):
        ct.exec_in_local_tmux(['taxes', 'pdf'])
    assert closed == ['cc-taxes-pdf']


def test_off_lan_handoff_keeps_the_launcher_alive_for_the_tab_close(ct, monkeypatch, capsys):
    import os
    monkeypatch.setattr(os, 'execvp', lambda *a: pytest.fail('launcher replaced itself'))
    monkeypatch.setattr(ct, 'cmux_resume_bind', lambda name: None)
    ran, after = [], []
    monkeypatch.setattr(ct, 'run_transport', lambda cmd: ran.append(cmd) or 0)
    monkeypatch.setattr(ct, 'after_ix_exit', lambda name, host: after.append((name, host)) or True)
    _handoff(ct, monkeypatch, IX_LIVE)
    monkeypatch.delenv('CC_DRY')
    assert ct.ix_handoff(['janus']) is True
    assert len(ran) == 1 and after == [('cc-062745', ct.IX_TS_HOST)]


def test_stale_marker_for_this_surface_is_purged_before_the_tab_attaches(ct, tmp_path, monkeypatch):
    """The phone took a session this tab had tagged and ran /fin there: nobody consumed the
    marker, and by surface id it would have closed this tab on its next plain detach."""
    import subprocess
    (tmp_path / 'cc-zz-dead-session').write_text('sid\nsurface=SURF-1\n')
    (tmp_path / 'cc-zz-other-surface').write_text('sid\nsurface=SURF-2\n')
    subprocess.run(['zsh', '-c', ct.fin_marker_purge('SURF-1', marker_dir=str(tmp_path))], timeout=10)
    assert not (tmp_path / 'cc-zz-dead-session').exists()
    assert (tmp_path / 'cc-zz-other-surface').exists()
    monkeypatch.setenv('CMUX_SURFACE_ID', 'SURF-1')
    assert 'surface=SURF-1' in ct.surface_tag('cc-x')            # every attach script carries it
    monkeypatch.delenv('CMUX_SURFACE_ID')
    assert ct.surface_tag('cc-x') == '' and ct.fin_marker_purge('') == ''


def test_only_a_launcher_at_the_top_of_its_tmux_pane_owns_the_session(ct, monkeypatch):
    import types
    parent = {'comm': 'tmux'}
    monkeypatch.setattr(ct.subprocess, 'run',
                        lambda argv, **k: types.SimpleNamespace(stdout=parent['comm'] + '\n', returncode=0))
    monkeypatch.setenv('TMUX', '/tmp/tmux-501/default,1,0')
    assert ct.tmux_top() is True
    parent['comm'] = '-zsh'                                       # cc typed at a shell inside tmux
    assert ct.tmux_top() is False
    parent['comm'] = 'tmux'
    monkeypatch.delenv('TMUX')
    assert ct.tmux_top() is False
