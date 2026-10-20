"""Regression (2026-06-24): 申 showed the wrong 分 (it read 0 while Neon showed
90). Root cause: Neon's 申 cell is the live residual formula =D-SUM(locked) whose
VALUE is the running unallocated total. janus read G:O as formulas, SKIPPED the
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
    spec = importlib.util.spec_from_file_location("janus_bpneon", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_bpneon"] = mod
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


def test_residual_block_before_clock_mirrors_neon(monkeypatch):
    """Regression (2026-06-26): 午 was the first unlocked block (residual value
    215.5 ≈ 216) while the clock had moved PAST it into 未. The stale pre-fix
    process skipped 午 (residual) and pinned the running total to the clock
    block, so 午 displayed 0 against Neon's 216. With the fix, 午 mirrors its
    Neon cell value regardless of where the clock sits."""
    m = _load_tui()
    # Today's real shape: 卯/辰/巳 locked literals, 午 = residual (216), rest 0.
    m.STATE.block_points = {"卯": 75, "辰": 256, "巳": 225, "午": 216}
    m.STATE.block_running_pts = 216  # stale reconstruction must not leak elsewhere
    fixed = dtm.datetime(2026, 6, 26, 12, 30, tzinfo=m.TZ)  # clock in 未, PAST 午

    class _DT(dtm.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(m.dt, "datetime", _DT)

    assert m._block_display_pts("午") == 216, "午 mirrors Neon's residual value, not 0"
    assert m._block_display_pts("巳") == 225, "locked literal still shown"
    assert m._block_display_pts("未") == 0, "clock block not re-attributed the residual"


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
    src = (HERE / "janus.py").read_text(encoding="utf-8")
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


def test_block_display_clamped_to_day_total():
    """Regression (2026-07-02): 辰 showed 666 on a 272分 day. A block is a
    residual of Σ (=D-SUM(locked) ≤ D), so it can never exceed today_points; a
    stuck/torn value that does must be clamped, not displayed."""
    m = _load_tui()
    m.STATE.today_points = 272
    m.STATE.block_points = {"卯": 6, "辰": 666}  # 辰 stuck above Σ
    assert m._block_display_pts("辰") == 272, "block capped at the day total"
    assert m._block_display_pts("卯") == 6, "a normal block is unaffected"


def test_block_display_normal_value_not_clamped():
    m = _load_tui()
    m.STATE.today_points = 272
    m.STATE.block_points = {"辰": 266}
    assert m._block_display_pts("辰") == 266


def test_block_display_no_clamp_when_total_unknown():
    """A failed/zero Σ read must NOT blank a real block value (clamp only when Σ
    is a sane positive)."""
    m = _load_tui()
    m.STATE.today_points = 0
    m.STATE.block_points = {"辰": 100}
    assert m._block_display_pts("辰") == 100


def test_block_display_hard_ceiling_when_total_unknown():
    """Regression (2026-07-14): "8442分" shown on a block. Never traced to any
    value fetch_points actually computed, adopted, or rejected (its own gates
    — _total_trustworthy, _blocks_plausible — cap everything it writes to
    STATE at _MAX_PLAUSIBLE_TOTAL before it ever lands there) — but nothing
    re-verifies what's already SITTING in STATE once today_points has gone
    back to 0 (cross-day reset, or a read failure), so a stale/torn value in
    that branch had no ceiling at all. The "no clamp when total unknown" gap
    above must still let a NORMAL value (100) through unclamped, but an
    implausible one must not display raw."""
    m = _load_tui()
    m.STATE.today_points = 0
    m.STATE.block_points = {"辰": 8442}
    assert m._block_display_pts("辰") == m._MAX_PLAUSIBLE_TOTAL
    # A sane value in the same no-total-known state is untouched (no regression
    # on the fix directly above).
    m.STATE.block_points = {"辰": 100}
    assert m._block_display_pts("辰") == 100
