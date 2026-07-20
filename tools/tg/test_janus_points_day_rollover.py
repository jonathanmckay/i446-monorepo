"""Regression: points state must not leak across days, and 分 must render as
integers everywhere.

Two defects behind "current block points rendering wrong" (2026-07-07):

1. Cross-day staleness. fetch_points keeps last-good state on a rejected read
   — right within a day, but after midnight every read of the NEW day's row
   can be rejected for hours (torn D during active writes), so YESTERDAY's Σ
   and block points stayed on screen (990 shown at 16:11 on a 323分 day, per
   /tmp/janus-points-rejected.log). fetch_points must blank the state when
   the viewed day changes.

2. Float repr next to 分. The sheet now carries fractional cells (variable
   tasks write minutes/7; D=695.357142857143 observed live), and a float
   reaching an f-string prints its full repr beside 分, which reads as
   concatenated garbage digits on the rule line. Every 分 render site must
   int(round()) first, and the compact morning view must use the same
   Σ-clamped accessor as the focus rules."""
import datetime as dtm
import importlib.util
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
TZ = ZoneInfo("America/Los_Angeles")


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_ptsday", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_ptsday"] = mod
    spec.loader.exec_module(mod)
    return mod


def _block_excel_reads(mod, monkeypatch):
    """Make the ix-osa Excel read fail so fetch_points exercises only its
    state handling (no network, no Excel)."""
    def _fail(*a, **k):
        raise OSError("no ix in tests")
    monkeypatch.setattr(subprocess, "run", _fail)


def test_fetch_points_resets_state_on_new_day(monkeypatch):
    mod = _load_tui()
    _block_excel_reads(mod, monkeypatch)
    yesterday = dtm.datetime.now(TZ).date() - dtm.timedelta(days=1)
    mod.STATE.points_day = yesterday
    mod.STATE.today_points = 990
    mod.STATE.block_points = {"酉": 317}
    mod.fetch_points()
    assert mod.STATE.points_day == dtm.datetime.now(TZ).date()
    assert mod.STATE.today_points == 0, "yesterday's Σ must not display today"
    assert mod.STATE.block_points == {}, "yesterday's blocks must not display today"


def test_fetch_points_keeps_last_good_within_day(monkeypatch):
    """The keep-last-good behavior for torn reads is unchanged INSIDE a day."""
    mod = _load_tui()
    _block_excel_reads(mod, monkeypatch)
    today = dtm.datetime.now(TZ).date()
    mod.STATE.points_day = today
    mod.STATE.today_points = 323
    mod.STATE.block_points = {"酉": 100}
    mod.fetch_points()
    assert mod.STATE.today_points == 323
    assert mod.STATE.block_points == {"酉": 100}


def test_section_rule_rounds_float_pts():
    mod = _load_tui()
    text = "".join(t for _, t in mod.section_rule("酉", focus=True,
                                                  pts=323.7142857142857))
    assert " 324分" in text
    assert "." not in text, f"float repr must never reach the rule: {text!r}"


def test_compact_header_rounds_float_pts():
    mod = _load_tui()
    out = mod._compact_block_lines("酉", 16, [], 323.7142857142857, "")
    text = "".join(t for _, t in out)
    assert "324分" in text
    assert "." not in text.split("\n")[0]


def test_header_rounds_float_total():
    mod = _load_tui()
    mod.STATE.day_offset = 0
    mod.STATE.today_points = 695.357142857143
    text = "".join(t for _, t in mod.render_header())
    assert "695分" in text
    assert "." not in text


def test_morning_blocks_use_clamped_accessor():
    """render_morning must route per-block 分 through _block_display_pts (the
    Σ-clamped path the focus rules use), not raw block_points."""
    src = (HERE / "janus.py").read_text()
    i_def = src.index("def render_morning")
    i_end = src.index("def _block_display_pts")
    seg = src[i_def:i_end]
    assert "_block_display_pts(blk_name)" in seg
    assert "STATE.block_points.get(blk_name" not in seg


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
