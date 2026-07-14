"""janus warns when the running process is behind the file on disk — a shipped
fix is invisible until restart, which has repeatedly masked fixes. The header
flashes a RESTART banner when janus.py's mtime is newer than load time."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_stale", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_stale"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fresh_process_is_not_stale():
    mod = _load_tui()
    # Just loaded → on-disk mtime is not newer than what we captured.
    assert mod._code_is_stale() is False


def test_detects_newer_file_on_disk():
    mod = _load_tui()
    # Simulate the file being rewritten after this process loaded.
    mod._SRC_MTIME = mod._SRC.stat().st_mtime - 100  # we "loaded" 100s before the file
    mod._stale_state["checked"] = 0.0
    # now=100 forces a re-check (100 - 0 > 5) regardless of sandbox monotonic.
    assert mod._code_is_stale(now=100.0) is True


def test_header_shows_restart_banner_when_stale():
    mod = _load_tui()
    mod.STATE.today_points = 0
    mod.STATE.day_offset = 0
    # Pin the cached verdict so render_header's _code_is_stale() (called with no
    # `now`) returns it without re-statting: a far-future `checked` keeps elapsed
    # negative, so the cache is never refreshed.
    mod._stale_state.update({"checked": 1e12, "stale": False})
    assert "RESTART" not in "".join(t for _, t in mod.render_header())

    mod._stale_state.update({"checked": 1e12, "stale": True})
    frags = mod.render_header()
    text = "".join(t for _, t in frags)
    assert "RESTART" in text
    assert any(s == "class:no_entry" for s, _ in frags), "banner is rendered red"


def test_header_says_janus_not_tg():
    """Regression (2026-07-14): the tg-tui -> Janus rename (2026-07-13) missed
    render_header's title, which still read " tg · ..." — a bare "tg" with no
    hyphen/underscore, so the rename's grep for tg-tui|tg_tui|TG_TUI never
    matched it. All three header variants (live, stale-restart, past-day) must
    say "janus", not "tg"."""
    mod = _load_tui()
    mod.STATE.today_points = 0
    mod.STATE.day_offset = 0
    mod._stale_state.update({"checked": 1e12, "stale": False})
    text = "".join(t for _, t in mod.render_header())
    assert "janus" in text
    assert " tg " not in text and "tg ·" not in text

    mod._stale_state.update({"checked": 1e12, "stale": True})
    text = "".join(t for _, t in mod.render_header())
    assert "janus" in text
    assert "tg ·" not in text

    mod.STATE.day_offset = -1
    text = "".join(t for _, t in mod.render_header())
    assert "janus" in text
    assert "tg ·" not in text


def test_stale_check_is_cached():
    """The 0.1s repaint must not stat the file every frame — the result is cached
    for ~5s."""
    mod = _load_tui()
    mod._SRC_MTIME = mod._SRC.stat().st_mtime - 100
    mod._stale_state.update({"checked": 0.0, "stale": False})
    mod._code_is_stale(now=1000.0)            # first call sets stale=True, checked=1000
    assert mod._stale_state["stale"] is True
    mod._stale_state["stale"] = False         # pretend nothing changed
    mod._code_is_stale(now=1002.0)            # within 5s → cached, not re-stat'd
    assert mod._stale_state["stale"] is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
