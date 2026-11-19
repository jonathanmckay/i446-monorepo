"""Regression 2026-11-19: "when I run /did from Janus it blocks the UI".

janus never blocked its own event loop. Its children ate the keystrokes:
did-fast was spawned with the terminal as stdin, and off Ix did-fast (and
janus's own in-process Excel reads) reach the daemon through `ssh ix curl`,
which reads its stdin for as long as it runs. Typed keys went to ssh and were
dropped (pty probe: 0 of 11 bytes survived an `ssh ix sleep 3` with inherited
stdin, 11 of 11 with stdin=DEVNULL).

Rule: every subprocess launch in the files a janus job runs through names its
stdin: `stdin=` (DEVNULL) or `input=` (a pipe). Covers the form
`asyncio.to_thread(subprocess.run, argv, ...)` too, where the kwargs sit on
the wrapping call.
"""
import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FILES = [
    "tools/tg/janus.py",
    "tools/tg/tg-fast.py",
    "tools/did/did-fast.py",
    "lib/neon/excel.py",
]
LAUNCHERS = {"run", "Popen", "check_output", "check_call", "call"}


def _subprocess_aliases(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    names.add(a.asname or "subprocess")
    return names


def unguarded_launches(src: str) -> list[int]:
    """Line numbers of subprocess launches that inherit the parent's stdin."""
    tree = ast.parse(src)
    aliases = _subprocess_aliases(tree)

    def is_launcher(n: ast.AST) -> bool:
        return (isinstance(n, ast.Attribute) and n.attr in LAUNCHERS
                and isinstance(n.value, ast.Name) and n.value.id in aliases)

    bad = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        direct = is_launcher(call.func)
        wrapped = any(is_launcher(a) for a in call.args)  # to_thread(subprocess.run, ...)
        if not (direct or wrapped):
            continue
        kws = {k.arg for k in call.keywords}
        if None in kws:  # **kwargs: cannot tell statically
            continue
        if not ({"stdin", "input"} & kws):
            bad.append(call.lineno)
    return sorted(bad)


def test_detector_catches_the_three_shapes():
    src = (
        "import subprocess\nimport subprocess as _sp\nimport asyncio\n"
        "subprocess.run(['ssh', 'ix', 'true'], capture_output=True)\n"      # 4 bad
        "_sp.Popen(['x'])\n"                                                # 5 bad
        "asyncio.to_thread(subprocess.run, ['x'], capture_output=True)\n"   # 6 bad
        "subprocess.run(['x'], stdin=subprocess.DEVNULL)\n"
        "subprocess.run(['x'], input='y')\n"
        "asyncio.to_thread(subprocess.run, ['x'], stdin=subprocess.DEVNULL)\n"
    )
    assert unguarded_launches(src) == [4, 5, 6]


@pytest.mark.parametrize("rel", FILES)
def test_no_child_inherits_the_terminal(rel):
    bad = unguarded_launches((REPO / rel).read_text())
    assert not bad, (
        f"{rel}: subprocess launch without stdin=/input= at line(s) {bad}. "
        "A child that inherits janus's tty (ssh above all) eats typed keys."
    )
