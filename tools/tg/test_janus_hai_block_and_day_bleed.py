"""User report 2026-07-27: "it's not showing 亥 at all... make sure it shows
亥 before showing 子. Also, it should show one day, not bleed into the next
day."

Two holes, both opening once the current block is 子 (22:00+, and EVERY
past-day view, since those anchor view_now at 23:59):

1. 亥 vanished. detail_window falls back to prev+current (band start 20:00)
   when 子 has no next block, and render_morning stops at the band start
   trusting the band to cover the rest — but render_focus_compact only drew
   current + next, never prev. 亥 fell into the hole between them.

2. render_evening skipped already-covered blocks via `eh + 1 <= cutoff.hour`
   where cutoff is the detail band's END. With 子 in the band, end_h=24 puts
   the end datetime on the NEXT day, `.hour` reads 0, nothing is skipped,
   and 卯–戌 re-render after 子 as phantom next-day blocks."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_hai", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_hai"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup_common(mod):
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.current = None


def _entry(desc, start, end, project_id=None, running=False):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": running, "id": 1}


def test_hai_renders_before_zi_when_zi_is_current():
    """At 22:30 the focus band must show 亥 (fully elapsed) and THEN 子."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.entries = [_entry("xk22", today.replace(hour=20, minute=10),
                                today.replace(hour=21, minute=0), project_id=1)]
    mod.view_now = lambda: today.replace(hour=22, minute=30)
    text = "".join(t for _, t, *_ in mod.render_focus_compact())
    # 2026-08-06: xk22 (20:10) is 亥's first real entry, so it rides the
    # header itself (`亥:10`) rather than a bare `亥:00` header.
    assert "亥:10" in text, "亥 must render in the focus band when 子 is current"
    assert "子:00" in text
    assert text.index("亥:10") < text.index("子:00"), "亥 must come before 子"
    assert "xk22" in text.split("子:00")[0], "亥's own entries must render in its card"


def test_hai_renders_on_past_day_view():
    """Past days anchor view_now at 23:59 — the exact hole 亥 fell into.
    Backfilling a past day is when seeing 亥's gaps matters most."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.day_offset = -1
    yday = today - dtm.timedelta(days=1)
    mod.view_now = lambda: yday.replace(hour=23, minute=59, second=59)
    text = "".join(t for _, t, *_ in mod.render_focus_compact())
    assert "亥:00" in text
    assert text.index("亥:00") < text.index("子:00")


def test_evening_empty_once_detail_band_reaches_midnight():
    """With 子 current (band = 亥+子, end 24:00 = next-day midnight),
    render_evening must render NOTHING — not re-render 卯–戌 as phantom
    next-day blocks after 子."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.view_now = lambda: today.replace(hour=22, minute=30)
    assert mod.render_evening() == []


def test_evening_empty_when_hai_is_current():
    """Same hole one block earlier: 亥 current → next is 子 → band end 24."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.view_now = lambda: today.replace(hour=20, minute=15)
    assert mod.render_evening() == []


def test_evening_still_renders_remaining_blocks_midday():
    """Daytime behavior unchanged: at 15:05 (申 current, 酉 next, band end
    18:00) the evening band still carries 戌 and 亥."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.view_now = lambda: today.replace(hour=15, minute=5)
    text = "".join(t for _, t, *_ in mod.render_evening())
    assert "戌:00" in text and "亥:00" in text
    assert "子:00" not in text, "the sleep block stays out of the evening band"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
