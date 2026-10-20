"""Regression (2026-10-03): "make sure the time shows (upper right hand
corner) even if the window is small". WIDTH_HINT is fixed at startup (up to
64), so a pane narrowed afterwards got 64-column header lines and the
right-edge clock was clipped off. The header and the running-timer line now
size to the live pane width and give up the date, then the left text,
before the clock."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_clock_narrow", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text(frags):
    return "".join(t for _, t, *_ in frags)


def test_fit_clock_line_keeps_everything_when_it_fits():
    m = _load()
    line = m._fit_clock_line(" 256分 · janus ", ["Sat 10/3 08:58:48 ", "08:58:48 "], 64)
    assert line.endswith("Sat 10/3 08:58:48 ") and line.startswith(" 256分 · janus ")
    assert m.dwidth(line) == 64


def test_fit_clock_line_drops_date_before_clock():
    m = _load()
    line = m._fit_clock_line(" 256分 · janus ", ["Sat 10/3 08:58:48 ", "08:58:48 "], 28)
    assert line.endswith("08:58:48 ") and "10/3" not in line
    assert line.startswith(" 256分 · janus ") and m.dwidth(line) == 28


def test_fit_clock_line_truncates_left_before_clock():
    m = _load()
    left = " 256分 · janus · ⚠ RESTART — code updated "
    line = m._fit_clock_line(left, ["Sat 10/3 08:58:48 ", "08:58:48 "], 20)
    assert line.endswith("08:58:48 ") and m.dwidth(line) <= 20


def test_header_clock_survives_a_pane_narrowed_after_startup(monkeypatch):
    m = _load()
    m.WIDTH_HINT = 64                       # computed when the pane was wide
    monkeypatch.setattr(m, "_live_width", lambda: 30)   # pane is now narrow
    monkeypatch.setattr(m, "_code_is_stale", lambda: False)
    m.STATE.day_offset = 0
    m.STATE.last_points_fetch = 1
    m.STATE.today_points = 256
    line = _text(m.render_header()).rstrip("\n")
    assert m.dwidth(line) <= 30, line
    assert line.rstrip().split()[-1].count(":") == 2, f"clock missing: {line!r}"


def test_running_timer_clock_survives_a_narrow_pane(monkeypatch):
    m = _load()
    m.WIDTH_HINT = 64
    monkeypatch.setattr(m, "_live_width", lambda: 36)
    m.STATE.current = {"id": 1, "description": "generic placeholder with a long description",
                       "start": "2026-10-03T08:50:00-07:00", "project_id": None}
    line = _text(m.render_current_bottom()).rstrip("\n")
    assert m.dwidth(line) <= 36, line
    assert line.rstrip().split()[-1].count(":") == 2, f"clock missing: {line!r}"


def test_render_header_uses_live_width_not_the_startup_hint():
    src = (HERE / "janus.py").read_text()
    i = src.index("def render_header()")
    body = src[i:src.index("\n\n\n", i)]
    assert "_live_width()" in body and "_fit_clock_line(" in body
    assert "WIDTH_HINT - len(left)" not in body
