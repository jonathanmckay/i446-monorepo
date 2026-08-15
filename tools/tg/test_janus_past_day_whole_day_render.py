"""User report 2026-08-15: "when showing previous days I don't want there to
be any focus blocks (right now it looks like it just takes 亥 and makes it
the focus block since it was the last one)."

render_focus_compact()'s "last block of the day" fallback (2026-07-27, see
test_janus_hai_block_and_day_bleed.py) was written for TODAY's live view
once the clock passes 22:00 — but it only checks whether there's a next
block, never STATE.day_offset, so it fired on every past-day view too,
rendering 亥/子 in the wide FOCUS_ROWS "current block" card style (preceded
by a "where now is" divider that means nothing on a past day).

Fix: render_morning() gains a whole-day mode (day_offset != 0) that covers
the ENTIRE day, including 子 (which its ordinary loop can never reach —
子's own eh=23 would need cutoff.hour >= 24 to pass the old break check).
render_all() only emits the divider + calls render_focus_compact() on
day_offset == 0; render_focus_compact() itself is untouched, so TODAY's
live behavior (including its own past-22:00 fallback) is provably
unaffected."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_past_day", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_past_day"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _setup_common(mod):
    mod.STATE.current_known = True
    mod.STATE.entries_known = True
    mod.STATE.entries = []
    mod.STATE.entries_yday = []
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.day_offset = 0
    mod.STATE.current = None


def _entry(desc, start, end, project_id=None, running=False):
    return {"start_dt": start, "end_dt": end, "desc": desc,
            "project_id": project_id, "running": running, "id": 1}


def test_render_morning_covers_zi_on_a_past_day():
    """子 has no rendering path through render_morning's ordinary (today)
    loop at all — this is the whole point of whole_day mode."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.day_offset = -1
    yday = today - dtm.timedelta(days=1)
    mod.view_now = lambda: yday.replace(hour=23, minute=59, second=59)
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "子:00" in text, "past-day render_morning must reach 子"
    assert "亥:00" in text, "past-day render_morning must reach 亥 too"


def test_render_morning_past_day_shows_hai_entries():
    """A real tracked entry inside 亥 on a past day must render as a normal
    row (same as every other block that day), not vanish into the gap
    render_focus_compact used to paper over."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.day_offset = -1
    yday = today - dtm.timedelta(days=1)
    mod.view_now = lambda: yday.replace(hour=23, minute=59, second=59)
    mod.STATE.entries = [_entry("xk22", yday.replace(hour=20, minute=10),
                                yday.replace(hour=21, minute=0), project_id=1)]
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "xk22" in text


def test_render_morning_today_unchanged_still_stops_before_hai():
    """Regression guard: TODAY's live view must NOT switch to whole-day
    mode — render_morning still stops at the detail band start and leaves
    亥/子 to render_focus_compact, exactly as before this change."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.day_offset = 0
    mod.view_now = lambda: today.replace(hour=15, minute=5)  # 申 current, 酉 next
    text = "".join(t for _, t, *_ in mod.render_morning())
    assert "戌:00" not in text, "today's render_morning must not run past the detail band"
    assert "亥:00" not in text
    assert "子:00" not in text


def test_render_all_skips_focus_band_on_past_day(monkeypatch):
    """render_all must not call render_focus_compact (or emit its preceding
    divider) when viewing a past day."""
    mod = _load_tui()
    _setup_common(mod)
    mod.STATE.day_offset = -1
    calls = []
    monkeypatch.setattr(mod, "render_focus_compact", lambda: calls.append(1) or [])
    monkeypatch.setattr(mod, "render_header", lambda: [])
    monkeypatch.setattr(mod, "render_habits_today", lambda: [])
    monkeypatch.setattr(mod, "render_morning", lambda: [])
    monkeypatch.setattr(mod, "render_evening", lambda: [])
    text = "".join(t for _, t, *_ in mod.render_all())
    assert not calls, "render_focus_compact must not be called on a past-day view"
    assert "─" * 10 not in text, "the now-marking divider must not appear on a past-day view"


def test_render_all_still_shows_focus_band_today(monkeypatch):
    """Regression guard: TODAY's view must still call render_focus_compact
    and draw the divider, exactly as before this change."""
    mod = _load_tui()
    _setup_common(mod)
    mod.STATE.day_offset = 0
    calls = []
    monkeypatch.setattr(mod, "render_focus_compact", lambda: calls.append(1) or [])
    monkeypatch.setattr(mod, "render_header", lambda: [])
    monkeypatch.setattr(mod, "render_habits_today", lambda: [])
    monkeypatch.setattr(mod, "render_morning", lambda: [])
    monkeypatch.setattr(mod, "render_evening", lambda: [])
    text = "".join(t for _, t, *_ in mod.render_all())
    assert calls, "render_focus_compact must still fire on today's view"
    assert "─" * 10 in text, "the now-marking divider must still appear on today's view"


def test_mao_sleep_collapse_still_applies_in_whole_day_mode():
    """Risk flagged in the plan: whole-day mode must not bypass 卯's
    single-line sleep collapse (same scenario as
    test_janus_mao_line.py::test_render_morning_collapses_kmao_when_all_sleep,
    just under day_offset != 0)."""
    mod = _load_tui()
    _setup_common(mod)
    today = _midnight()
    mod.STATE.day_offset = -1
    yday = today - dtm.timedelta(days=1)
    mod.view_now = lambda: yday.replace(hour=23, minute=59, second=59)
    mod.STATE.entries = [_entry("睡觉", yday, yday.replace(hour=5, minute=45))]
    text = "".join(t for _, t, *_ in mod.render_morning())
    lines = [l for l in text.split("\n") if "卯" in l]
    assert len(lines) == 1, f"卯 must still collapse to one line: {lines!r}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
