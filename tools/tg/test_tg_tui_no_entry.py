"""Regression tests for tg-tui's idle 'NO TIME ENTRY' indicator.

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
    spec = importlib.util.spec_from_file_location("tg_tui_ne", HERE / "tg-tui.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_ne"] = m
    spec.loader.exec_module(m)
    return m


def test_no_entry_style_registered():
    # The flashing indicator needs a dedicated style key, referenced by the
    # idle now-slot. (prompt_toolkit's Style doesn't expose its dict, so
    # assert the definition + use-site exist in source.)
    src = (HERE / "tg-tui.py").read_text()
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
    txt_all = "".join(t for _, t in parts)
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
    src = (HERE / "tg-tui.py").read_text()
    assert "now.timestamp() * 2" in src, "cursor flash must be 0.5s (timestamp*2)"
