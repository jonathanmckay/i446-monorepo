"""Day navigation (Ctrl+←/→): view a past day to fill in missed time entries.
day_offset 0 = today (live now); a negative offset views that past day anchored
to its end (23:59) so the whole day reads as elapsed, with a header badge."""
import datetime as dtm
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


def test_view_now_today_is_live_now():
    m = _load_tui()
    m.STATE.day_offset = 0
    now = dtm.datetime.now(m.TZ)
    vn = m.view_now()
    assert vn.date() == now.date()
    assert abs((vn - now).total_seconds()) < 5


def test_view_now_past_day_anchors_end_of_day():
    m = _load_tui()
    m.STATE.day_offset = -1
    vn = m.view_now()
    assert vn.date() == dtm.datetime.now(m.TZ).date() - dtm.timedelta(days=1)
    assert (vn.hour, vn.minute) == (23, 59), "past day anchors to end-of-day"


def test_view_now_multi_day_back():
    m = _load_tui()
    m.STATE.day_offset = -3
    vn = m.view_now()
    assert vn.date() == dtm.datetime.now(m.TZ).date() - dtm.timedelta(days=3)


def test_detail_window_shifts_to_viewed_day():
    m = _load_tui()
    m.STATE.day_offset = -2
    m.STATE.scroll_min = 0
    start, end = m.detail_window()
    viewed = (dtm.datetime.now(m.TZ).date() - dtm.timedelta(days=2))
    assert start.date() == viewed, f"detail window anchors to viewed day, got {start}"


def test_header_badges_past_day_but_not_today():
    m = _load_tui()
    m.STATE.today_points = 0
    m.STATE.day_offset = 0
    today_hdr = "".join(t for _, t, *_ in m.render_header())
    assert "◀" not in today_hdr, "today's header carries no back-arrow badge"

    m.STATE.day_offset = -1
    past_hdr = "".join(t for _, t, *_ in m.render_header())
    assert "◀" in past_hdr, "past-day header shows the ◀ date badge"
    assert "today" in past_hdr, "past-day header hints ⎋ resets to today"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
