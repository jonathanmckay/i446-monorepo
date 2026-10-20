"""Regression test (2026-06-23): the pinned bottom bar right-justified the running
timer to the far edge, but the eye looks LEFT for the ticking clock. The elapsed
timer (with tenths) must now lead on the left; the wall clock is right-justified.
"""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_botbar", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_botbar"] = mod
    spec.loader.exec_module(mod)
    return mod


def _flat(segments):
    # A selectable row's fragments now carry an optional 3rd element (mouse
    # click handler, added 2026-08-08) — tolerate both shapes, matching the
    # `for _, t, *_ in ...` convention test_janus_entry_selection.py already
    # uses for the same reason.
    return "".join(text for _style, text, *_ in segments)


def test_running_timer_leads_left_clock_on_right(monkeypatch):
    m = _load_tui()
    now = dtm.datetime(2026, 6, 23, 14, 30, 0, tzinfo=m.TZ)

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(m.dt, "datetime", _DT)
    m.STATE.current = {"description": "dream morning brief",
                       "start": "2026-06-23T14:25:00+00:00", "project_id": None}
    line = _flat(m.render_current_bottom())
    # The ▶ + elapsed timer is the first non-space content; the wall clock trails.
    assert line.lstrip().startswith("▶ "), "running timer must lead on the left"
    timer_i = line.index("▶")
    clock_i = line.index("14:30:00")
    assert timer_i < clock_i, "elapsed timer must sit left of the wall clock"
    # The first text segment carries the timer (its project/running style), not the clock.
    first_text = m.render_current_bottom()[0][1]
    assert "▶" in first_text and "m" in first_text and "s" in first_text


def test_idle_bottom_bar_shows_no_timer(monkeypatch):
    m = _load_tui()
    now = dtm.datetime(2026, 6, 23, 14, 30, 0, tzinfo=m.TZ)

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(m.dt, "datetime", _DT)
    m.STATE.current = None
    line = _flat(m.render_current_bottom())
    assert "(no timer)" in line and "14:30:00" in line


def test_past_day_view_badges_the_pinned_bar(monkeypatch):
    """Bug 2026-10-12: "I can't see time from 卯 in today's janus". After
    Ctrl+← to fill in yesterday, the whole-day view is tall enough that the
    header's ◀ badge scrolls off the top, and the pinned bar (always visible)
    gave no hint, so yesterday read as today. The bar must name the viewed day."""
    m = _load_tui()
    now = dtm.datetime(2026, 10, 12, 6, 15, 0, tzinfo=m.TZ)

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(m.dt, "datetime", _DT)
    monkeypatch.setattr(m, "_live_width", lambda: 60)
    for cur in ({"description": "to mom's by 8", "start": "2026-10-12T13:10:00+00:00",
                 "project_id": None}, None):
        m.STATE.current = cur
        m.STATE.day_offset = -1
        line = _flat(m.render_current_bottom())
        assert "◀ Sun 10/11" in line and "06:15:00" in line, line
        assert m.dwidth(line.rstrip("\n")) <= 60, line
        m.STATE.day_offset = 0
        assert "◀" not in _flat(m.render_current_bottom())
