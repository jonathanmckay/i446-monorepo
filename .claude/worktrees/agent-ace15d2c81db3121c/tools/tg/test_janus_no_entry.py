"""Regression tests for janus's idle 'NO TIME ENTRY' indicator.

When no Toggl timer is running, the detail band's now-slot should show a
flashing red 'NO TIME ENTRY' with the elapsed idle time, in the spot the
running task would otherwise occupy.
"""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_ne", HERE / "janus.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["janus_ne"] = m
    spec.loader.exec_module(m)
    # Isolate from the shared cross-process cooldown file (a live cooldown there
    # would make _toggl_blocked() skip the fetch these tests exercise).
    m.toggl_throttle.cooling_down = lambda: False
    m.STATE.toggl_blocked_until = 0.0
    return m


def test_no_entry_style_registered():
    # The flashing indicator needs a dedicated style key, referenced by the
    # idle now-slot. (prompt_toolkit's Style doesn't expose its dict, so
    # assert the definition + use-site exist in source.)
    src = (HERE / "janus.py").read_text()
    assert '"no_entry"' in src, "no_entry style not defined"
    assert "class:no_entry" in src, "no_entry style never applied"


def test_idle_since_returns_latest_completed_end():
    m = _load()
    now = dt.datetime.now(m.TZ)
    m.STATE.entries = [
        {"end_dt": now - dt.timedelta(minutes=40), "running": False},
        {"end_dt": now - dt.timedelta(minutes=7), "running": False},
        # a running entry must be ignored (there shouldn't be one when idle,
        # but guard anyway)
        {"end_dt": now, "running": True},
    ]
    since = m._idle_since(now)
    assert since == now - dt.timedelta(minutes=7)


def test_idle_since_none_when_no_completed_entries():
    m = _load()
    m.STATE.entries = []
    assert m._idle_since(dt.datetime.now(m.TZ)) is None


def test_now_slot_shows_no_time_entry_when_idle():
    m = _load()
    now = dt.datetime.now(m.TZ)
    m.STATE.current = None
    m.STATE.current_known = True  # confirmed idle (a successful fetch saw no timer)
    m.STATE.events = []
    m.STATE.block_points = {}
    m.STATE.scroll_min = 0
    m.STATE.entries = [{
        "start_dt": now - dt.timedelta(minutes=40),
        "end_dt": now - dt.timedelta(minutes=7),
        "desc": "push", "project_id": None, "running": False,
    }]
    parts = m.render_detail()
    frag = [(sty, txt) for sty, txt in parts if "NO TIME ENTRY" in txt]
    assert frag, "idle now-slot should render NO TIME ENTRY"
    sty, txt = frag[0]
    assert sty == "class:no_entry", f"expected no_entry style, got {sty}"
    assert "7m" in txt, f"idle duration missing: {txt!r}"
    # tenths of a second on the idle duration
    import re
    assert re.search(r"\d+m\d{2}\.\ds", txt), f"idle duration needs tenths: {txt!r}"
    # a rule line drawn across the now-row
    assert "─" in txt, f"now-row should have a line across: {txt!r}"


def test_running_now_row_has_line_across():
    m = _load()
    now = dt.datetime.now(m.TZ)
    m.STATE.current = {
        "description": "work", "project_id": None,
        "start": (now - dt.timedelta(minutes=5, seconds=3)).isoformat(),
    }
    m.STATE.events = []
    m.STATE.block_points = {}
    m.STATE.scroll_min = 0
    m.STATE.entries = []
    parts = m.render_detail()
    frag = [(sty, txt) for sty, txt in parts if "▶ work" in txt]
    assert frag, "running now-row missing"
    assert "─" in frag[0][1], f"running now-row should have a line across: {frag[0][1]!r}"


def test_running_timer_suppresses_no_time_entry():
    m = _load()
    now = dt.datetime.now(m.TZ)
    m.STATE.current = {
        "description": "work", "project_id": None,
        "start": (now - dt.timedelta(minutes=5)).isoformat(),
    }
    m.STATE.events = []
    m.STATE.block_points = {}
    m.STATE.scroll_min = 0
    m.STATE.entries = []
    parts = m.render_detail()
    txt_all = "".join(t for _, t, *_ in parts)
    assert "NO TIME ENTRY" not in txt_all, "must not show idle alarm while a timer runs"
    assert "▶ work" in txt_all


def test_flash_cursor_toggles_every_half_second():
    # The cursor toggles every 0.5s: int(t*2) % 2 flips each half-second.
    def cur(t):
        return "█" if int(t * 2) % 2 == 0 else " "
    base = 1_000_000.0  # whole second → phase 0
    seq = [cur(base + i * 0.5) for i in range(4)]
    assert seq == ["█", " ", "█", " "]
    # source uses the 0.5s formula, not the old 4×/sec one
    src = (HERE / "janus.py").read_text()
    assert "now.timestamp() * 2" in src, "cursor flash must be 0.5s (timestamp*2)"


def test_no_flash_when_timer_state_unknown():
    """Bug: starting a 2nd hci while Toggl was rate-limiting (402) left the TUI
    flashing 'no timer' over a live entry. When current is unconfirmed
    (current_known False), neither the input-row flash nor the red NO TIME
    ENTRY alarm may fire."""
    m = _load()
    now = dt.datetime.now(m.TZ)
    m.STATE.current = None
    m.STATE.current_known = False  # fetch failed / rate-limited: state unknown
    m.STATE.events = []
    m.STATE.block_points = {}
    m.STATE.scroll_min = 0
    m.STATE.entries = [{
        "start_dt": now - dt.timedelta(minutes=40),
        "end_dt": now - dt.timedelta(minutes=7),
        "desc": "push", "project_id": None, "running": False,
    }]
    assert m._no_timer_flash_on() is False, "must not flash the input row when unknown"
    txt_all = "".join(t for _, t, *_ in m.render_detail())
    assert "NO TIME ENTRY" not in txt_all, "must not nag when timer state is unconfirmed"


def test_fetch_current_marks_unknown_on_rate_limit(monkeypatch):
    """A 402 (or any error) from get_current must set current_known False and
    leave the last-known current untouched, so the idle nag stays off."""
    m = _load()
    sentinel = {"description": "hci", "project_id": None, "start": "x"}
    m.STATE.current = sentinel
    m.STATE.current_known = True

    def _boom():
        raise RuntimeError("Toggl API GET /me/time_entries/current -> 402: hourly limit")

    monkeypatch.setattr(m.toggl_api, "get_current", _boom)
    m.fetch_current()
    assert m.STATE.current_known is False
    assert m.STATE.current is sentinel, "must keep last-known current on failure"


def test_fetch_current_marks_known_on_success(monkeypatch):
    m = _load()
    m.STATE.current_known = False
    entry = {"description": "hci", "project_id": None, "start": "x"}
    monkeypatch.setattr(m.toggl_api, "get_current", lambda: entry)
    m.fetch_current()
    assert m.STATE.current_known is True
    assert m.STATE.current is entry


# ─── no-timer flash grace period (2026-08-11) ──────────────────────────────
# A normal task switch (stop, type the next command, start) always has a
# beat with no timer running. Flashing instantly during that beat read as
# the UI panicking over a routine transition rather than nagging about an
# actually-forgotten timer, so the whole-screen flash now waits
# NO_TIMER_FLASH_DELAY after the first confirmed "no timer" observation.

def test_fetch_current_sets_no_timer_since_on_first_idle_observation(monkeypatch):
    m = _load()
    m.STATE.current = {"description": "work", "project_id": None, "start": "x"}
    m.STATE.no_timer_since = None
    monkeypatch.setattr(m.toggl_api, "get_current", lambda: None)
    m.fetch_current()
    assert m.STATE.current is None
    assert m.STATE.no_timer_since is not None, \
        "must stamp the moment idle was first confirmed"


def test_fetch_current_does_not_rewind_no_timer_since_on_repeat_idle_polls(monkeypatch):
    m = _load()
    m.STATE.current = None
    m.STATE.current_known = True
    first = 12345.0
    m.STATE.no_timer_since = first
    monkeypatch.setattr(m.toggl_api, "get_current", lambda: None)
    m.fetch_current()
    assert m.STATE.no_timer_since == first, \
        "a later idle poll must not reset the grace-period clock"


def test_fetch_current_clears_no_timer_since_when_timer_starts(monkeypatch):
    m = _load()
    m.STATE.current = None
    m.STATE.no_timer_since = 12345.0
    entry = {"description": "work", "project_id": None, "start": "x"}
    monkeypatch.setattr(m.toggl_api, "get_current", lambda: entry)
    m.fetch_current()
    assert m.STATE.no_timer_since is None


def test_no_flash_within_grace_period_after_stopping(monkeypatch):
    m = _load()
    m.STATE.current = None
    m.STATE.current_known = True
    m.STATE.no_timer_since = m.time.monotonic() - 5  # 5s idle, under the 15s grace
    assert m._no_timer_flash_on() is False, \
        "must not flash yet — still inside the transition grace period"


def test_flash_resumes_after_grace_period_elapses(monkeypatch):
    m = _load()
    m.STATE.current = None
    m.STATE.current_known = True
    m.STATE.no_timer_since = m.time.monotonic() - (m.NO_TIMER_FLASH_DELAY + 1)

    class _FixedDT(m.dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz).replace(microsecond=0)

    monkeypatch.setattr(m.dt, "datetime", _FixedDT)
    assert m._no_timer_flash_on() is True, \
        "grace period elapsed — the idle nag should flash again"


def test_no_timer_flash_style_reflects_flash_state(monkeypatch):
    """_no_timer_flash_style (the Window `style` callable) must return
    'reverse' exactly when _no_timer_flash_on() is True, and nothing
    otherwise — this is what actually drives the visible flash now that
    it's scoped to a single window instead of a StyleTransformation."""
    m = _load()
    monkeypatch.setattr(m, "_no_timer_flash_on", lambda: True)
    assert m._no_timer_flash_style() == "reverse"
    monkeypatch.setattr(m, "_no_timer_flash_on", lambda: False)
    assert m._no_timer_flash_style() == ""


def test_no_timer_flash_scoped_to_input_row_not_whole_screen():
    """Regression (2026-08-20 user request): the no-timer nag used to be a
    whole-screen StyleTransformation (attrs.reverse on every rendered
    cell). Now it must be scoped to just the input row — prompt_window and
    input_window carry the flash style, and Application must no longer be
    given a style_transformation at all."""
    m = _load()
    assert m.input_window.style is not None and callable(m.input_window.style), \
        "input_window must carry a dynamic style (the flash callable)"
    assert m.prompt_window.style is not None and callable(m.prompt_window.style), \
        "prompt_window must carry a dynamic style (the flash callable)"

    src = (HERE / "janus.py").read_text()
    assert "style_transformation" not in src, (
        "the whole-screen style_transformation mechanism must be gone entirely — "
        "the flash lives on the input row's own `style` now"
    )
    assert "class _NoTimerFlash" not in src, \
        "the old whole-screen StyleTransformation class must be removed, not just unused"
