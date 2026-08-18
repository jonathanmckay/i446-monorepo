"""Day-nav key contract.

History: Ctrl+-/Ctrl+= transmit the PLAIN character in most terminals, so
2026-07-05 bound bare "-"/"=" (gated on an empty command line) as day-nav.
Superseded 2026-07-29 (user report: '"-" goes to the previous day rather
than typing the character'): the empty-line gate is exactly the state you
are in when you START typing a "-"-leading description — "-1l", "-1g",
"#-2" — so the first keystroke navigated instead of typing. Bare "["/"]"
were bound instead (aliasing Ctrl+←/→, which macOS grabs for Mission
Control spaces) — but had the exact same collision problem, just never
reported until it blocked typing "[10]"-style point annotations (user
request 2026-08-18). Bare characters must ALWAYS type; day nav is now
Ctrl+←/→ and Ctrl+//Ctrl+= only, reached via the CSI-u aliases (f22/f23)
plus "c-_" (Ctrl+/'s standard raw-byte name) since it's unverified which
encoding this terminal actually sends for Ctrl+/."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_daynav", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_daynav"] = mod
    spec.loader.exec_module(mod)
    return mod


def _bindings(mod, key):
    return [b for b in mod.kb.bindings if b.keys == (key,)]


class _FakeApp:
    def create_background_task(self, coro):
        coro.close()  # never awaited in tests; close to avoid warnings


class _FakeEvent:
    app = _FakeApp()


def test_bare_minus_and_equals_are_not_bound():
    """Typing "-1l" into an idle janus must TYPE, never navigate — the
    2026-07-29 regression. Any future rebind of the bare characters
    reintroduces it, empty-line filter or not."""
    mod = _load_tui()
    assert _bindings(mod, "-") == [], 'bare "-" must type, not scrub days'
    assert _bindings(mod, "=") == [], 'bare "=" must type, not scrub days'


def test_bare_brackets_are_not_bound():
    """2026-08-18 regression guard: bare "["/"]" used to drive day nav and
    had the exact same typing-collision problem "-"/"=" did (blocked typing
    "[10]"-style point annotations). Removed in favor of Ctrl+//Ctrl+= —
    any future rebind of the bare characters reintroduces the collision."""
    mod = _load_tui()
    assert _bindings(mod, "[") == [], 'bare "[" must type, not scrub days'
    assert _bindings(mod, "]") == [], 'bare "]" must type, not scrub days'


def test_day_nav_alternatives_stay_bound():
    mod = _load_tui()
    for key, handler in (("c-left", mod._day_back), ("f22", mod._day_back),
                         ("c-_", mod._day_back), ("f24", mod._day_back),
                         ("c-right", mod._day_forward), ("f23", mod._day_forward)):
        hits = _bindings(mod, key)
        assert hits and hits[0].handler is handler, f"{key!r} must drive day nav"


def test_csiu_ctrl_sequences_still_aliased():
    """Real Ctrl+-/Ctrl+=/Ctrl+/ arrive as CSI-u sequences (verified for
    -/= on 2026-07-24); the ANSI_SEQUENCES aliases are what make f22/f23/f24
    reachable at all."""
    mod = _load_tui()
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    from prompt_toolkit.keys import Keys
    assert ANSI_SEQUENCES.get("\x1b[61;5u") == Keys.F23
    assert ANSI_SEQUENCES.get("\x1b[45;5u") == Keys.F24
    assert ANSI_SEQUENCES.get("\x1b[47;5u") == Keys.F22


def test_forward_returns_to_today_and_caps():
    mod = _load_tui()
    mod.STATE.day_offset = -1
    mod._day_forward(_FakeEvent())
    assert mod.STATE.day_offset == 0
    mod._day_forward(_FakeEvent())
    assert mod.STATE.day_offset == 0, "must never scrub into the future"


def test_back_goes_back_a_day():
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod._day_back(_FakeEvent())
    assert mod.STATE.day_offset == -1


def test_footer_hint_names_the_ctrl_keys():
    mod = _load_tui()
    src = (HERE / "janus.py").read_text()
    assert "^_/^= day" in src and "[/] day" not in src, \
        "the footer hint must advertise the keys that actually navigate"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
