"""Regression tests (2026-08-10): a single transient lock failure at fire
time corrupted the whole day's per-block point split.

The 08:00 fire's lock of column H (辰) hit one osascript_timeout,
neon_lock_cell returned FAILED, and the fire moved on with no retry and no
backstop. H stayed a live `=D-SUM(...)` residual, so every point earned
after 08:00 kept accumulating into 辰 while 巳's residual read 0 ("demon
for 辰 didn't fire so all points that should be 午 are accumulating to
辰"). The sheet needed a hand-reconstructed retro-lock.

Fix, two layers:
  1. neon_lock_cell_with_retry — the lock retries transient failures
     (FAILED / ERROR) up to `attempts` times; final statuses (LOCKED,
     ALREADY_LOCKED, EMPTY, DRY_RUN) return immediately.
  2. self_heal_unlocked_blocks — every fire sweeps all EARLIER fire-hours'
     columns for today and late-locks any still holding a formula, so a
     lock that failed every retry is corrected at the next fire instead of
     silently corrupting attribution until a human notices.
"""
import datetime as dt
import importlib.util
from pathlib import Path
from unittest.mock import patch

DAEMON = Path(__file__).parent / "build-order-daemon.py"
SRC = DAEMON.read_text(encoding="utf-8")


def _load_daemon():
    spec = importlib.util.spec_from_file_location("build_order_daemon_lr", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Structural ────────────────────────────────────────────────────────────

def test_fire_path_uses_the_retrying_lock():
    """The lock-and-mark fire must call the retry wrapper, not the bare
    single-attempt lock."""
    idx = SRC.index("lock_col = LOCK_AT_FIRE_HOUR.get(hour)")
    region = SRC[idx:idx + 400]
    assert "neon_lock_cell_with_retry(today, lock_col" in region, (
        "the fire path must lock through neon_lock_cell_with_retry")


def test_fire_path_runs_the_self_heal_sweep():
    idx = SRC.index("lock_col = LOCK_AT_FIRE_HOUR.get(hour)")
    region = SRC[idx:idx + 600]
    assert "self_heal_unlocked_blocks(today, hour" in region, (
        "every fire must sweep earlier blocks for a lock that failed all "
        "its own retries")


# ── Behavioral: retry wrapper ─────────────────────────────────────────────

def test_retry_stops_on_first_success():
    mod = _load_daemon()
    calls = []

    def fake_lock(date, col, dry_run=False):
        calls.append(col)
        return "LOCKED 12"

    with patch.object(mod, "neon_lock_cell", side_effect=fake_lock), \
         patch.object(mod.time, "sleep"):
        out = mod.neon_lock_cell_with_retry(dt.date(2026, 8, 10), "H")
    assert out == "LOCKED 12"
    assert len(calls) == 1


def test_retry_retries_transient_failure_then_succeeds():
    mod = _load_daemon()
    outcomes = iter(["FAILED", "FAILED", "LOCKED 12"])

    with patch.object(mod, "neon_lock_cell", side_effect=lambda *a, **k: next(outcomes)), \
         patch.object(mod.time, "sleep") as slept:
        out = mod.neon_lock_cell_with_retry(dt.date(2026, 8, 10), "H", attempts=3)
    assert out == "LOCKED 12"
    assert slept.call_count == 2, "must wait between attempts"


def test_retry_gives_up_after_attempts_and_reports_failure():
    mod = _load_daemon()
    with patch.object(mod, "neon_lock_cell", return_value="FAILED"), \
         patch.object(mod.time, "sleep"):
        out = mod.neon_lock_cell_with_retry(dt.date(2026, 8, 10), "H", attempts=3)
    assert out == "FAILED"


def test_retry_does_not_retry_final_statuses():
    """ALREADY_LOCKED / EMPTY are conclusive — retrying them would be
    pointless churn against a slow Excel daemon."""
    mod = _load_daemon()
    for status in ("ALREADY_LOCKED 7", "EMPTY", "DRY_RUN"):
        calls = []

        def fake_lock(date, col, dry_run=False, _s=status):
            calls.append(1)
            return _s

        with patch.object(mod, "neon_lock_cell", side_effect=fake_lock), \
             patch.object(mod.time, "sleep"):
            out = mod.neon_lock_cell_with_retry(dt.date(2026, 8, 10), "H")
        assert out == status
        assert len(calls) == 1, f"{status} must not be retried"


# ── Behavioral: self-heal sweep ───────────────────────────────────────────

def _fake_read(formulas):
    def read(sheet, col, date=None, row=None):
        return {"ok": True, "row": 220, "col": col,
                "value": "0", "formula": formulas.get(col, "0")}
    return read


def test_self_heal_locks_an_earlier_column_still_holding_a_formula():
    """THE bug scenario: it is the 10:00 fire (locks I); H failed its 08:00
    lock and still holds a residual formula — the sweep must lock it."""
    mod = _load_daemon()
    locked = []
    formulas = {"G": "0", "H": "=D220-G220"}  # G locked, H still live

    with patch.object(mod.neon_excel, "read", side_effect=_fake_read(formulas)), \
         patch.object(mod, "neon_lock_cell_with_retry",
                      side_effect=lambda d, c, dry_run=False: locked.append(c) or "LOCKED 5"):
        healed = mod.self_heal_unlocked_blocks(dt.date(2026, 8, 10), 10)

    assert locked == ["H"], "the stale live residual must be late-locked"
    assert healed and healed[0][0] == "H"


def test_self_heal_never_touches_the_current_or_future_blocks():
    """The sweep only covers fire hours strictly BEFORE this fire — the
    current block's own lock is the fire's normal job, and future blocks
    must stay live."""
    mod = _load_daemon()
    locked = []
    formulas = {c: "=D220-x" for c in "GHIJKLMNO"}  # everything live

    with patch.object(mod.neon_excel, "read", side_effect=_fake_read(formulas)), \
         patch.object(mod, "neon_lock_cell_with_retry",
                      side_effect=lambda d, c, dry_run=False: locked.append(c) or "LOCKED 5"):
        mod.self_heal_unlocked_blocks(dt.date(2026, 8, 10), 10)

    assert "I" not in locked and "J" not in locked, (
        "hour-10's own column (I) and later columns are not the sweep's job")
    assert locked == ["G", "H"]


def test_self_heal_skips_already_locked_columns():
    mod = _load_daemon()
    locked = []
    formulas = {"G": "0", "H": "516.51"}  # both literal

    with patch.object(mod.neon_excel, "read", side_effect=_fake_read(formulas)), \
         patch.object(mod, "neon_lock_cell_with_retry",
                      side_effect=lambda d, c, dry_run=False: locked.append(c) or "LOCKED"):
        healed = mod.self_heal_unlocked_blocks(dt.date(2026, 8, 10), 10)

    assert locked == [] and healed == []


def test_self_heal_survives_read_errors():
    """A read error on one column must not abort the sweep (or the fire)."""
    mod = _load_daemon()

    def flaky_read(sheet, col, date=None, row=None):
        if col == "G":
            raise RuntimeError("osascript_timeout")
        return {"ok": True, "row": 220, "col": col, "value": "0", "formula": "0"}

    with patch.object(mod.neon_excel, "read", side_effect=flaky_read):
        healed = mod.self_heal_unlocked_blocks(dt.date(2026, 8, 10), 10)
    assert healed == []




# ── Empty-block gray-out (2026-08-10 feature) ─────────────────────────────
# A block with 0 points (usually 卯, sometimes 辰) locks to a BLANK cell
# filled JM's manual medium gray (128,128,128) instead of a literal 0 —
# automating what he was doing by hand.

def test_zero_point_block_locks_blank_and_grays():
    mod = _load_daemon()
    writes, fills = [], []

    with patch.object(mod.neon_excel, "read", return_value={
            "ok": True, "row": 220, "col": "G", "value": "0", "formula": "=D220-x"}), \
         patch.object(mod.neon_excel, "write",
                      side_effect=lambda *a, **k: writes.append(k) or {"ok": True}), \
         patch.object(mod, "_fill_cell", side_effect=lambda r, c, rgb: fills.append((r, c, rgb))):
        out = mod.neon_lock_cell(dt.date(2026, 8, 10), "G")

    assert out == "LOCKED EMPTY"
    assert writes and writes[0]["value"] == "", "the cell must be blanked, not written 0"
    assert fills == [(220, "G", mod.EMPTY_BLOCK_FILL)]
    assert mod.EMPTY_BLOCK_FILL == (128, 128, 128), "must match JM's manual gray"


def test_negative_residual_clamps_into_the_grayed_empty_state():
    # The negative-clamp case (transient 0n penalties) is also "nothing
    # earned" — same blank+gray treatment.
    mod = _load_daemon()
    writes, fills = [], []
    with patch.object(mod.neon_excel, "read", return_value={
            "ok": True, "row": 220, "col": "G", "value": "-46", "formula": "=D220-x"}), \
         patch.object(mod.neon_excel, "write",
                      side_effect=lambda *a, **k: writes.append(k) or {"ok": True}), \
         patch.object(mod, "_fill_cell", side_effect=lambda r, c, rgb: fills.append((r, c, rgb))):
        out = mod.neon_lock_cell(dt.date(2026, 8, 10), "G")
    assert out == "LOCKED EMPTY" and writes[0]["value"] == "" and fills


def test_nonzero_block_locks_number_and_never_touches_fill():
    mod = _load_daemon()
    writes, fills = [], []
    with patch.object(mod.neon_excel, "read", return_value={
            "ok": True, "row": 220, "col": "H", "value": "87", "formula": "=D220-x"}), \
         patch.object(mod.neon_excel, "write",
                      side_effect=lambda *a, **k: writes.append(k) or {"ok": True}), \
         patch.object(mod, "_fill_cell", side_effect=lambda r, c, rgb: fills.append((r, c, rgb))):
        out = mod.neon_lock_cell(dt.date(2026, 8, 10), "H")
    assert out == "LOCKED 87"
    assert writes[0]["value"] == "87"
    assert fills == [], "a scored block keeps its normal formatting"


def test_locked_empty_is_final_for_the_retry_wrapper():
    mod = _load_daemon()
    calls = []
    with patch.object(mod, "neon_lock_cell",
                      side_effect=lambda *a, **k: calls.append(1) or "LOCKED EMPTY"), \
         patch.object(mod.time, "sleep"):
        out = mod.neon_lock_cell_with_retry(dt.date(2026, 8, 10), "G")
    assert out == "LOCKED EMPTY" and len(calls) == 1


def test_fill_failure_never_fails_the_lock():
    mod = _load_daemon()
    with patch.object(mod.neon_excel, "read", return_value={
            "ok": True, "row": 220, "col": "G", "value": "0", "formula": "=D220-x"}), \
         patch.object(mod.neon_excel, "write", return_value={"ok": True}), \
         patch.object(mod.subprocess, "run", side_effect=RuntimeError("osascript down")):
        out = mod.neon_lock_cell(dt.date(2026, 8, 10), "G")
    assert out == "LOCKED EMPTY", "formatting is cosmetic — its failure must not fail the lock"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
