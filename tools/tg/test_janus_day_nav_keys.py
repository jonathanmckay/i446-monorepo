"""Regression: -/= must scrub the viewed day back/forward.

Ctrl+-/Ctrl+= carry no control-character encoding in most terminals — they
transmit the PLAIN character — so before 2026-07-05 "ctrl+= to go forward to
today" hit the unbound "=" key and silently did nothing, leaving the view
stuck on a past day ([/] and c-left/c-right were the only working bindings).
The plain-char bindings must exist, be gated on an empty command line (a time
range like 05:00-05:23 types "-" mid-input), and move day_offset correctly."""
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


def _binding(mod, key):
    hits = [b for b in mod.kb.bindings if b.keys == (key,)]
    assert hits, f"no binding for {key!r}"
    return hits[0]


class _FakeApp:
    def create_background_task(self, coro):
        coro.close()  # never awaited in tests; close to avoid warnings


class _FakeEvent:
    app = _FakeApp()


def test_minus_and_equals_are_bound():
    mod = _load_tui()
    assert _binding(mod, "-").handler is mod._day_back
    assert _binding(mod, "=").handler is mod._day_forward


def test_equals_returns_to_today():
    mod = _load_tui()
    mod.STATE.day_offset = -1
    mod._day_forward(_FakeEvent())
    assert mod.STATE.day_offset == 0


def test_equals_capped_at_today():
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod._day_forward(_FakeEvent())
    assert mod.STATE.day_offset == 0, "must never scrub into the future"


def test_minus_goes_back_a_day():
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod._day_back(_FakeEvent())
    assert mod.STATE.day_offset == -1


def test_plain_chars_only_fire_on_empty_input():
    """Typing a time range must not scrub days: the -/= bindings are filtered
    on an empty input buffer; [ and ] stay unconditional."""
    mod = _load_tui()
    for key in ("-", "="):
        b = _binding(mod, key)
        mod.input_buffer.text = ""
        assert bool(b.filter()), f"{key!r} must fire on an empty command line"
        mod.input_buffer.text = "05:00-05:23 睡觉"
        assert not bool(b.filter()), f"{key!r} must not intercept mid-input text"
    mod.input_buffer.text = ""


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
