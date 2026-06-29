"""Focus band renders completed time at REAL entry boundaries, not rounded to
15-min slots: each entry shows its actual HH:MM-HH:MM span, untracked stretches
are their own flashing HH:MM-HH:MM gap rows, and the live now-row is anchored to
the running timer's real start."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_spans", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_spans"] = mod
    spec.loader.exec_module(mod)
    return mod


def _midnight():
    return dtm.datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


def _entry(desc, s, e, pid=None, running=False):
    return {"start_dt": s, "end_dt": e, "desc": desc, "project_id": pid,
            "running": running, "id": 1}


def _setup(mod, monkeypatch, entries, now_h, now_m, win=(4, 8), current=None):
    today = _midnight()
    monkeypatch.setattr(mod, "view_now",
                        lambda: today.replace(hour=now_h, minute=now_m))
    monkeypatch.setattr(mod, "detail_window",
                        lambda: (today.replace(hour=win[0]), today.replace(hour=win[1])))
    mod.STATE.entries = entries
    mod.STATE.events = []
    mod.STATE.block_points = {}
    mod.STATE.scroll_min = 0
    mod.STATE.day_offset = 0
    mod.STATE.current = current
    mod.STATE.current_known = True
    return today


def test_completed_entry_shows_start_time_only(monkeypatch):
    """'review elliot paper' 05:18-05:31 reads as a 05:18 start row (the end is
    implied by the next row), not a rounded 05:15 slot and not a start-end span."""
    mod = _load_tui()
    _setup(mod, monkeypatch,
           [_entry("review elliot paper", _midnight().replace(hour=5, minute=18),
                   _midnight().replace(hour=5, minute=31))],
           now_h=6, now_m=30)
    text = "".join(t for _, t in mod.render_detail())
    assert "05:18 │" in text, f"entry must show its real start:\n{text}"
    assert "review elliot paper" in text
    assert "05:18-05:31" not in text, "no redundant end time"
    assert "05:15-05:30" not in text


def test_gap_after_entry_flashes(monkeypatch):
    """The 05:31 untracked stretch is its own flashing gap row (start-keyed)."""
    mod = _load_tui()
    base = _midnight()
    _setup(mod, monkeypatch,
           [_entry("review elliot paper", base.replace(hour=5, minute=18),
                   base.replace(hour=5, minute=31)),
            _entry("next thing", base.replace(hour=5, minute=48),
                   base.replace(hour=6, minute=0))],
           now_h=6, now_m=30)
    monkeypatch.setattr(mod, "_gap_alarm_on", lambda *a, **k: True)
    frags = mod.render_detail()
    text = "".join(t for _, t in frags)
    assert "05:31 │" in text, f"interior gap must show its start:\n{text}"
    assert any(s == "class:no_entry" and "┄" in t for s, t in frags), "gap flashes"


def test_tiny_entry_is_absorbed_no_row(monkeypatch):
    """A sub-DETAIL_MIN entry (e.g. a 4-min 'ibx s897') gets no line; it's
    absorbed into the timeline rather than cluttering the focus band."""
    mod = _load_tui()
    base = _midnight()
    _setup(mod, monkeypatch,
           [_entry("deep work", base.replace(hour=5, minute=0),
                   base.replace(hour=5, minute=40)),
            _entry("ibx s897", base.replace(hour=5, minute=40),
                   base.replace(hour=5, minute=44)),  # 4 min < DETAIL_MIN
            _entry("more work", base.replace(hour=5, minute=44),
                   base.replace(hour=6, minute=0))],
           now_h=6, now_m=30)
    text = "".join(t for _, t in mod.render_detail())
    assert "deep work" in text and "more work" in text
    assert "ibx s897" not in text, "tiny entry must be absorbed, not given a row"


def test_running_now_row_anchored_to_real_start(monkeypatch):
    """The live now-row starts at the running timer's actual start time."""
    mod = _load_tui()
    base = _midnight()
    start_iso = base.replace(hour=6, minute=3).isoformat()
    _setup(mod, monkeypatch, [], now_h=6, now_m=30,
           current={"start": start_iso, "description": "vibing", "project_id": None})
    text = "".join(t for _, t in mod.render_detail())
    assert "06:03 │" in text, f"now-row anchors to the timer's real start:\n{text}"
    assert "▶ vibing" in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
