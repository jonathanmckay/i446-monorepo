"""Bug 2026-10-19: dtd launched to a silent, empty list ("… done  0 left",
0/0). Xcode.app had just been installed; xcode-select pointed at it with the
license unaccepted, so the /usr/bin/python3 shim failed on EVERY call with
"You have not agreed to the Xcode license agreements". dtd.sh redirects all
of those to /dev/null, so nothing surfaced. dtd.sh now preflights python3:
fall back to the Command Line Tools toolchain via DEVELOPER_DIR when that
works, else exit nonzero with the real error before painting anything."""
import os
import stat
import subprocess
from pathlib import Path

SRC = (Path(__file__).parent / "dtd.sh").read_text()
CLT = "/Library/Developer/CommandLineTools"
LICENSE_MSG = "You have not agreed to the Xcode license agreements."


def _guard() -> str:
    start = SRC.index("# python3 preflight guard")
    end = SRC.index("# END python3 preflight guard")
    return SRC[start:end]


def _fake_python(tmp_path: Path, *, works_under_clt: bool) -> Path:
    """A python3 shim that mimics the Xcode license failure. When
    works_under_clt, it succeeds only if DEVELOPER_DIR points at the CLT."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    shim = bindir / "python3"
    body = "#!/bin/sh\n"
    if works_under_clt:
        body += f'[ "$DEVELOPER_DIR" = "{CLT}" ] && exit 0\n'
    body += f'echo "{LICENSE_MSG}" >&2\nexit 69\n'
    shim.write_text(body)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return bindir


def _run(bindir: Path):
    script = _guard() + '\nprint -r -- "reached:${DEVELOPER_DIR:-unset}"\n'
    env = {"HOME": str(Path.home()), "PATH": f"{bindir}:/usr/bin:/bin"}
    return subprocess.run(["/bin/zsh", "-fc", script], env=env,
                          capture_output=True, text=True)


def test_guard_falls_back_to_clt_when_default_python_is_broken(tmp_path):
    r = _run(_fake_python(tmp_path, works_under_clt=True))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"reached:{CLT}"


def test_guard_exits_loudly_when_no_python_works(tmp_path):
    r = _run(_fake_python(tmp_path, works_under_clt=False))
    assert r.returncode != 0
    assert "reached:" not in r.stdout
    assert LICENSE_MSG in r.stderr, r.stderr
    assert "dtd: python3 is unusable" in r.stderr


def test_guard_is_a_noop_with_a_working_python():
    script = _guard() + '\nprint -r -- "reached:${DEVELOPER_DIR:-unset}"\n'
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin",
           "DEVELOPER_DIR": CLT}  # a known-good toolchain, whatever the host's xcode-select says
    r = subprocess.run(["/bin/zsh", "-fc", script], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"reached:{CLT}"


def test_guard_runs_before_the_first_python3_call():
    assert SRC.index("# python3 preflight guard") < SRC.index('python3 "$HOME/i446-monorepo/lib/daytime.py"')
