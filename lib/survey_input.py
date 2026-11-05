"""Typed answers for the terminal review surveys. prompt_toolkit gives them
the editing keys plain input() lacks: opt+arrow moves by word (all the escape
sequences terminals send for it), and ctrl-a / ctrl-e / ctrl-w work."""
from __future__ import annotations

import sys


def read_line(prompt: str) -> str:
    """One line. `prompt` may carry ANSI colors. "" on EOF or ctrl-c."""
    try:
        if not sys.stdin.isatty():
            return input(prompt).strip()
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import ANSI
        return PromptSession().prompt(ANSI(prompt)).strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def read_multiline() -> str:
    """One item per line, editable across lines. Enter on an empty line or
    ctrl-s saves; "" on ctrl-c."""
    if not sys.stdin.isatty():
        lines = []
        while True:
            ln = read_line("> ")
            if not ln:
                return "\n".join(lines)
            lines.append(ln)
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    kb = KeyBindings()

    @kb.add("enter")
    def _(e):
        buf = e.current_buffer
        if buf.document.current_line.strip():
            # Finish the line from anywhere in it: after opt+arrow an Enter at
            # the cursor would split it and the blank line would never come.
            buf.cursor_position += buf.document.get_end_of_line_position()
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    @kb.add("c-s")
    @kb.add("c-d")
    def _(e):
        e.current_buffer.validate_and_handle()

    try:
        text = PromptSession(key_bindings=kb, multiline=True).prompt(
            "> ", prompt_continuation=lambda *_a: "> ")
    except (KeyboardInterrupt, EOFError):
        return ""
    return "\n".join(ln for ln in text.splitlines() if ln.strip())
