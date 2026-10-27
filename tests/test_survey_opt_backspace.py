"""Opt+Backspace must delete a word in the survey forms, not exit them.

2026-10-27: the terminal sends Opt+Backspace as ESC DEL. The forms bound Esc
with eager=True (2026-10-16, to skip the 1s chord wait), so the ESC fired
save-and-exit before prompt_toolkit could match its built-in
`escape backspace` -> backward-kill-word. Fix: non-eager Esc plus a short
app.timeoutlen, which keeps lone Esc snappy and lets the chord match.
"""
import re
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import TextArea

ROOT = Path(__file__).resolve().parent.parent
FORMS = [
    "tools/0s/0s.py",
    "tools/1s/1s-survey.py",
    "tools/xk887/xk887-survey.py",
    "tools/moralaudit/moralaudit.py",
    "tools/f693/1f693.py",
    "tools/-1g/-1g-form.py",
]


def _run(keys, eager):
    area = TextArea()
    kb = KeyBindings()
    kb.add("escape", eager=eager)(lambda e: e.app.exit(result="submit"))
    with create_pipe_input() as inp:
        app = Application(layout=Layout(area), key_bindings=kb,
                          input=inp, output=DummyOutput())
        app.timeoutlen = 0.05
        # trailing lone Esc closes the form once the keys under test have landed
        inp.send_text("hello world" + keys + "\x1b")
        result = app.run(handle_sigint=False)
    return area.text, result


def test_opt_backspace_deletes_word_with_non_eager_escape():
    text, result = _run("\x1b\x7f", eager=False)
    assert text == "hello "
    assert result == "submit"


def test_lone_escape_still_submits():
    text, result = _run("", eager=False)
    assert text == "hello world" and result == "submit"


def test_eager_escape_is_the_bug():
    text, _ = _run("\x1b\x7f", eager=True)
    assert text == "hello world"   # ESC swallowed the chord


def test_no_form_binds_eager_escape_and_all_set_short_timeoutlen():
    for rel in FORMS:
        src = (ROOT / rel).read_text()
        assert not re.search(r'kb\.add\("escape",\s*eager=True\)', src), rel
        assert "app.timeoutlen = " in src, rel
