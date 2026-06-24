"""Regression (2026-06-24): 申 showed the wrong 分 (it read 0 while Neon showed
90). Root cause: Neon's 申 cell is the live residual formula =D-SUM(locked) whose
VALUE is the running unallocated total. tg-tui read G:O as formulas, SKIPPED the
residual, and reconstructed Σ−locked pinned to the *clock* block — but when an
earlier block (未) locks ahead of the clock, the residual lives in 申 while the
clock sits in 未, so the 90 became homeless and 申 displayed 0.

Fix: fetch_points reads each G:O cell's VALUE in the same pass and mirrors it, so
block_points holds every nonzero block (including the residual one). The display
reads block_points directly — no Σ−locked re-attribution to the clock block."""
import datetime as dtm
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("tg_tui_bpneon", HERE / "tg-tui.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tg_tui_bpneon"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_residual_block_mirrors_neon_value(monkeypatch):
    """申 holds the residual (value 90 on the sheet) while the clock is in 未.
    The display must show 90 for 申, the locked literal for 未, and 0 for a block
    Neon shows empty — exactly mirroring the sheet."""
    m = _load_tui()
    # block_points as fetch_points now builds it: locked literals + the residual
    # block's VALUE (申=90), read straight from Neon's G:O cells.
    m.STATE.block_points = {"辰": 6, "巳": 389, "午": 254, "未": 286, "申": 90}
    fixed = dtm.datetime(2026, 6, 24, 12, 30, tzinfo=m.TZ)  # clock in 未 (12-13)

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(m.dt, "datetime", _DT)

    assert m._block_display_pts("申") == 90, "申 mirrors Neon's residual value, not 0"
    assert m._block_display_pts("未") == 286, "locked literal still shown"
    assert m._block_display_pts("酉") == 0, "a block Neon shows empty is 0"


def test_no_reattribution_to_clock_block_when_data_present(monkeypatch):
    """With block_points populated, the clock block must NOT inherit the running
    residual via Σ−locked. Guards the double-display edge: residual in 申, clock
    in 酉 → 酉 shows 0 (Neon's value), not 90."""
    m = _load_tui()
    m.STATE.block_points = {"申": 90}      # residual mirrored from Neon
    m.STATE.block_running_pts = 90         # stale reconstruction must be ignored
    fixed = dtm.datetime(2026, 6, 24, 16, 30, tzinfo=m.TZ)  # clock in 酉 (16-17)

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(m.dt, "datetime", _DT)

    assert m._block_display_pts("申") == 90
    assert m._block_display_pts("酉") == 0, "clock block not re-attributed the residual"


def test_fetch_points_reads_gio_values_and_mirrors_residual():
    """Structural guard: fetch_points must (a) read G:O VALUES in the AppleScript
    pass and (b) assign the residual cell's value into block_points, rather than
    skipping `=` cells outright."""
    src = (HERE / "tg-tui.py").read_text(encoding="utf-8")
    # Two G:O reads in the AppleScript: formulas AND values (cells 7..15 twice).
    assert src.count("repeat with c from 7 to 15") == 2, \
        "AppleScript must read G:O as both formulas and values"
    # Parser slices the appended G:O value segment and mirrors the residual.
    assert "gio_vals = parts[20:29]" in src
    assert "bp_excel[bname] = vv" in src, \
        "residual cell's VALUE must populate block_points, not be skipped"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
