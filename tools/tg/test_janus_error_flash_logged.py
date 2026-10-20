"""Bug 2026-10-20: a Toggl 402 flashed as
"RuntimeError: Toggl API POST /workspaces/2092616/time…" and vanished in 4s,
unreadable and unreported.

`_FLASH_FAIL` matched `\\berror`, and there is no word boundary inside
"RuntimeError", so an exception flash was not classified as a failure at all:
no "outcome error" event, no error styling, no hold. The footer is still ONE
line on purpose -- the full text belongs in the event log, not on screen.
"""
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "janus.py").read_text()

TOGGL_402 = ("RuntimeError: Toggl API POST /workspace/2092616/search/time_entries"
             " -> 402 (hourly quota for the ws bucket is exhausted;"
             " resets in 2169s; request not sent)")


def _load():
    spec = importlib.util.spec_from_file_location("janus_err_flash", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


J = _load()


def test_exception_names_are_classified_as_failures():
    for msg in (TOGGL_402, "ValueError: bad", "KeyError: 'x'", "toggl current err: boom",
                "write failed", "invalid code"):
        assert J._FLASH_FAIL.search(msg), msg
    for msg in ("stopped + logged → neon ✓", "+15m → hcb", "tracking started ✓"):
        assert not J._FLASH_FAIL.search(msg), msg


def test_a_failure_is_held_on_screen_and_a_success_is_not(monkeypatch):
    monkeypatch.setattr(J.JE, "emit", lambda *a, **k: None)
    t0 = time.monotonic()
    J.flash(TOGGL_402)                       # caller passed the 4s default
    assert J.STATE.flash_until - t0 >= J.ERROR_FLASH_SECS - 1
    J.flash("stopped + logged ✓", 4.0)
    assert J.STATE.flash_until - t0 < J.ERROR_FLASH_SECS - 1


def test_a_long_explicit_duration_is_never_shortened(monkeypatch):
    monkeypatch.setattr(J.JE, "emit", lambda *a, **k: None)
    t0 = time.monotonic()
    J.flash("rate limited, backing off", 90.0)
    assert J.STATE.flash_until - t0 >= 89


def test_the_whole_error_reaches_the_event_log(monkeypatch):
    seen = []
    monkeypatch.setattr(J.JE, "emit", lambda *a, **k: seen.append(a))
    J.flash(TOGGL_402)
    assert seen and seen[0][:2] == ("outcome", "error"), seen
    logged = seen[0][3]
    assert "402" in logged and "2169s" in logged, "the diagnosis must survive, not just the verb"
    assert len(logged) <= 300, "janus_events truncates extra at 300 chars"


def test_the_footer_stays_one_line():
    # Explicit 2026-10-20 decision: errors go to the log, the box does not grow.
    assert "height=1" in SRC[SRC.index("footer_window = Window("):][:200]
    body = SRC[SRC.index("def render_footer()"):SRC.index("def _current_block_lines(")]
    assert body.count("return [") == 2 and "textwrap" not in body
