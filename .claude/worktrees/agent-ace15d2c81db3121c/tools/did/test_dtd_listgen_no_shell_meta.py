#!/usr/bin/env python3
"""Regression: the list-gen python is embedded in `python3 -c "<payload>"`, a
DOUBLE-quoted zsh string — so the payload must contain no shell metacharacters
that zsh would interpret.

Bug (2026-07-13): a comment I added read `# … the name-based `removed` hide …`
with BACKTICKS. Inside the double-quoted -c string, zsh treats backticks as
command substitution, so it ran `removed` as a command on EVERY reload
(`command not found: removed`). That stderr line printed into fzf's managed
screen and corrupted the display into ghost/duplicated rows.

The payload must have no backticks and no `$(` command substitution (and no bare
`$var` outside the intended positional args), or they leak into the shell.
"""
from pathlib import Path

DTD = (Path(__file__).resolve().parent / "dtd.sh").read_text()


def _payload() -> str:
    lines = DTD.splitlines()
    i0 = next(i for i, l in enumerate(lines)
              if l.strip() == "cat > \"$DTD_LIST\" << 'LISTEOF'")
    ps = next(i for i in range(i0, len(lines)) if lines[i].strip().startswith('python3 -c "'))
    pe = next(i for i in range(ps + 1, len(lines)) if lines[i].startswith('" "$1"'))
    # payload = the lines strictly BETWEEN `python3 -c "` and the closing `" "$1"…`
    return "\n".join(lines[ps + 1:pe])


def test_payload_has_no_backticks():
    body = _payload()
    assert "`" not in body, (
        "backticks in the python3 -c payload are command substitution in zsh — "
        "they run as shell commands on every reload and corrupt fzf's screen")


def test_payload_has_no_command_substitution():
    body = _payload()
    assert "$(" not in body, "$(...) in the -c payload is shell command substitution"


def test_payload_has_no_unescaped_double_quotes():
    # A stray " would terminate the -c string early and leak python into the shell.
    body = _payload()
    assert '"' not in body, "a double-quote would close the -c string early"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
