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
