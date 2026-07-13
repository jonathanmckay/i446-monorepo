"""Regression test (2026-06-23): 'generic placeholder' timers (tracked time the
user never categorized) rendered in their plain project color, so they didn't
nag. They must pulse red↔grey exactly like empty/gap time (class:no_entry ↔
class:idle on the _gap_alarm_on toggle) until relabelled.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_ph", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_ph"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_is_placeholder_matches_label_forms():
    m = _load_tui()
    assert m._is_placeholder("generic placeholder")
    assert m._is_placeholder("generic placeholder · infra")  # display suffix
    assert m._is_placeholder("generic placeholder [40]")     # annotation
    assert not m._is_placeholder("work")
    assert not m._is_placeholder("◇ Generic meeting")
    assert not m._is_placeholder("")


def test_placeholder_style_tracks_gap_alarm(monkeypatch):
    m = _load_tui()
    monkeypatch.setattr(m, "_gap_alarm_on", lambda now=None: True)
    assert m._placeholder_style() == "class:no_entry"   # same "on" colour as gaps
    monkeypatch.setattr(m, "_gap_alarm_on", lambda now=None: False)
    assert m._placeholder_style() == "class:idle"       # same "off" colour as gaps


def test_compact_block_placeholder_body_row_pulses(monkeypatch):
    """A placeholder entry as a body row pulses instead of using its project style."""
    m = _load_tui()
    monkeypatch.setattr(m, "_gap_alarm_on", lambda now=None: True)
    import datetime as dtm
    picks = [
        {"start_dt": dtm.datetime(2026, 6, 23, 8, 0, tzinfo=m.TZ), "time_str": "08:00",
         "label": "real task · i9", "style": "class:i9", "dur_min": 30},
        {"start_dt": dtm.datetime(2026, 6, 23, 8, 30, tzinfo=m.TZ), "time_str": "08:30",
         "label": "generic placeholder · infra", "style": "class:infra", "dur_min": 20},
    ]
    segs = m._compact_block_lines("巳", 8, picks, 0, "")
    # The placeholder body row carries the alarm colour; the real task does not.
    ph_styled = [sty for sty, txt in segs if "generic placeholder" in txt]
    assert ph_styled == ["class:no_entry"], f"placeholder must pulse, got {ph_styled}"
    real_styled = [sty for sty, txt in segs if "real task" in txt]
    assert real_styled == ["class:i9"], "non-placeholder keeps its project style"


def test_compact_block_placeholder_header_pulses(monkeypatch):
    """A placeholder as the inline header entry also pulses."""
    m = _load_tui()
    monkeypatch.setattr(m, "_gap_alarm_on", lambda now=None: False)  # "off" half-cycle
    import datetime as dtm
    picks = [
        {"start_dt": dtm.datetime(2026, 6, 23, 8, 5, tzinfo=m.TZ), "time_str": "08:05",
         "label": "generic placeholder · infra", "style": "class:infra", "dur_min": 40},
    ]
    segs = m._compact_block_lines("巳", 8, picks, 0, "")
    head_styled = [sty for sty, txt in segs if "generic placeholder" in txt]
    assert head_styled == ["class:idle"], f"header placeholder must pulse, got {head_styled}"
