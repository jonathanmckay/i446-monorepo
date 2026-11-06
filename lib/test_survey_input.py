"""opt+arrow word movement in the survey answers (JM 2026-11-05)."""
import sys
from pathlib import Path

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

sys.path.insert(0, str(Path(__file__).resolve().parent))
import survey_input as si  # noqa: E402


@pytest.fixture()
def typed(monkeypatch):
    monkeypatch.setattr(si.sys.stdin, "isatty", lambda: True, raising=False)

    def run(fn, text):
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            pipe.send_text(text)
            return fn()
    return run


@pytest.mark.parametrize("opt_left", ["\x1b[1;3D", "\x1bb", "\x1b\x1b[D"])
def test_opt_left_moves_back_one_word(typed, opt_left):
    assert typed(lambda: si.read_line("> "), f"one two{opt_left}X\r") == "one Xtwo"


def test_multiline_saves_on_an_empty_line(typed):
    assert typed(si.read_multiline, "first\rsecond\r\r") == "first\nsecond"


def test_multiline_edits_by_word_too(typed):
    assert typed(si.read_multiline, "one two\x1b[1;3DX\r\r") == "one Xtwo"
