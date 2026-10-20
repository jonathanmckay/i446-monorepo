"""2026-09-24 bug: "janus doesn't show neon points for a previous day when I
navigate back."

9/23/2026's 0分 row has Σ (col D) = -25: the -1₦ penalty in P (-50) outweighed
the 25分 earned (21 in Q, 4 in U). fetch_points read the row correctly on
every Ctrl+← (raw '-25.0|...|21.0|0.0|0.0|0.0|4.0|0.0|0.0|-50.0|0.0|0.0|...')
but _total_trustworthy rejected ANY negative candidate as a torn read, so
today_points stayed at the 0 the cross-day guard had blanked it to, and the
header showed 0分 for that day no matter how many reads succeeded. The
rejection log filled with identical total_ok=False lines every 120s.

A negative Σ is real whenever the P:Y cells read in the same pass sum to it;
the torn-read signature (D=-46 on a settled day) is D DISAGREEING with P:Y,
which the ±1 cross-check already catches. Only the no-cross-check fallback
keeps treating negatives as torn.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent


def _load_tui():
    spec = importlib.util.spec_from_file_location("janus_negday", HERE / "janus.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_negday"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_negative_total_accepted_when_py_agrees():
    """The exact 9/23 row: D=-25, P:Y = [21,0,0,0,4,0,0,-50,0,0] → sum -25."""
    mod = _load_tui()
    assert mod._total_trustworthy(-25, -25)


def test_negative_total_still_rejected_when_py_disagrees():
    """The original torn-read case that motivated the guard: D=-46 while the
    freshly-read P:Y cells say something else."""
    mod = _load_tui()
    assert not mod._total_trustworthy(-46, 758)


def test_negative_total_rejected_without_cross_check():
    """Loose-cap fallback (P:Y unreadable): a bare negative D has nothing to
    corroborate it and stays classified as torn."""
    mod = _load_tui()
    assert not mod._total_trustworthy(-46, None)
    assert mod._total_trustworthy(758, None)


def test_absurd_negative_total_rejected_even_if_py_agrees():
    """Symmetric to the +2000 ceiling: a torn snapshot can poison D and P:Y
    to the same absurd value in either direction."""
    mod = _load_tui()
    assert not mod._total_trustworthy(-5064, -5064)


def test_empty_blocks_consistent_with_negative_total():
    """9/23 had no G:O blocks at all. `sum({}) <= -25 + 2` used to be False, so
    every clean read of the day was also logged as a torn BLOCK read."""
    mod = _load_tui()
    assert mod._blocks_consistent(-25, {})


def test_nonempty_blocks_still_gated_by_total():
    mod = _load_tui()
    assert not mod._blocks_consistent(728, {"未": 975})
    assert mod._blocks_consistent(728, {"未": 195, "午": 178})
