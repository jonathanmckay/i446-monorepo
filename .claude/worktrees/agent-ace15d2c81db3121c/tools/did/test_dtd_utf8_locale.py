"""Bug 2026-10-13: dtd's edit screen (vared) showed a typed 中 as
"\\xe4<0088><0086>". dtd runs on ix in a tmux server with no LANG, so zsh
was byte-oriented. dtd.sh now exports a UTF-8 locale before anything else."""
import subprocess
from pathlib import Path

SRC = (Path(__file__).parent / "dtd.sh").read_text()


def _guard() -> str:
    start = SRC.index("# UTF-8 locale guard")
    end = SRC.index("# END UTF-8 locale guard")
    return SRC[start:end]


def _run(env: dict) -> str:
    script = _guard() + '\nx=中文; print -r -- "${#x} $LANG"\n'
    return subprocess.run(["/bin/zsh", "-fc", script], env=env,
                          capture_output=True, text=True).stdout.strip()


def test_guard_makes_zsh_multibyte_without_lang():
    out = _run({"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"})
    assert out.split()[0] == "2", f"中文 must be 2 chars, got {out!r}"


def test_guard_keeps_an_existing_utf8_locale():
    out = _run({"HOME": str(Path.home()), "PATH": "/usr/bin:/bin",
                "LANG": "zh_CN.UTF-8"})
    assert out == "2 zh_CN.UTF-8"


def test_guard_runs_before_the_edit_script_is_generated():
    assert SRC.index("# UTF-8 locale guard") < SRC.index('cat > "$DTD_EDIT"')
