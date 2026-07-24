"""Regression (2026-07-24): "ctrl+= doesn't go forward one day".

cmux/Ghostty encode Ctrl+= and Ctrl+- as CSI-u ("fixterms") sequences —
ESC[61;5u and ESC[45;5u, captured with cat -v — not as the plain characters
the 2026-07-05 fix assumed. prompt_toolkit has no entry for those sequences,
so a real Ctrl press was silently dropped before any binding could fire.

janus must (a) register both sequences in prompt_toolkit's ANSI_SEQUENCES
table so the vt100 parser emits a key for them, and (b) bind that key to the
same day-back/day-forward handlers as [ / ] / - / =.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_ctrlnav", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_ctrlnav"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_csi_u_sequences_registered():
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    from prompt_toolkit.keys import Keys
    _load_tui()
    assert ANSI_SEQUENCES.get("\x1b[61;5u") == Keys.F23  # Ctrl+=
    assert ANSI_SEQUENCES.get("\x1b[45;5u") == Keys.F24  # Ctrl+-


def test_vt100_parser_decodes_ctrl_equal_and_minus():
    """Feed the raw bytes cmux transmits through prompt_toolkit's parser and
    confirm they surface as the aliased keys (i.e. not silently dropped)."""
    from prompt_toolkit.input.vt100_parser import Vt100Parser
    from prompt_toolkit.keys import Keys
    _load_tui()
    seen = []
    parser = Vt100Parser(seen.append)
    parser.feed("\x1b[61;5u\x1b[45;5u")
    assert [kp.key for kp in seen] == [Keys.F23, Keys.F24]


def test_aliased_keys_bound_to_day_nav_handlers():
    mod = _load_tui()
    from prompt_toolkit.keys import Keys

    def handler_for(key):
        matches = [b for b in mod.kb.bindings if b.keys == (key,)]
        assert matches, f"no binding for {key}"
        return matches[0].handler.__name__

    assert handler_for(Keys.F23) == "_day_forward"  # Ctrl+=
    assert handler_for(Keys.F24) == "_day_back"     # Ctrl+-
