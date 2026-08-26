"""Regression test for salat-fast.py's Neon row-lookup date.

Bug (found 2026-08-26, live): the "M/D" row key for the ص (prayer) column
write was computed via naive `datetime.now()` — reads whichever machine's OS
clock actually executes the script, not the traveler's actual current day.
Correct by coincidence on the traveler's own laptop when its OS timezone
auto-follows physical location, but wrong the moment salat-fast.py runs
somewhere that doesn't travel (ix), or under an explicit /travel override
that diverges from the OS's own auto-detected zone — silently writing the
prayer count onto the WRONG day's row in Neon. Same root cause, same fix
pattern already applied across did-fast.py, dtd.sh, mark-completed.py, etc.
in the international-travel hardening pass.

Fix: route through lib/daytime.py's local_now() (checks an explicit
/travel override first, else follows the OS's own local time) instead of
a bare datetime.now() call.
"""
import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent


def _load(monkeypatch, frozen_now):
    spec = importlib.util.spec_from_file_location("salat_fast_t", HERE / "salat-fast.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["salat_fast_t"] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.daytime, "local_now", lambda: frozen_now)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda *a, **kw: MagicMock(returncode=0))
    return mod


def test_row_date_follows_daytime_not_a_different_machine_clock(monkeypatch):
    """Freeze daytime.local_now() to a date that would differ from
    whatever the raw OS clock happens to read right now — proves the
    write's row-lookup date comes from lib/daytime.py, not a bare
    datetime.now() call that would ignore an active /travel override or a
    divergent host clock (e.g. running on ix, which never travels)."""
    frozen = datetime(2026, 3, 7, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    mod = _load(monkeypatch, frozen)

    captured = {}

    def fake_append(sheet, col, *, date=None, value=None):
        captured["date"] = date
        return {"ok": True, "value": "1"}

    monkeypatch.setattr(mod.excel, "append", fake_append)
    monkeypatch.setattr(sys, "argv", ["salat-fast.py"])

    rc = mod.main()
    assert rc == 0
    assert captured["date"] == "3/7", (
        f"expected the row date to come from the frozen daytime.local_now() "
        f"(3/7), got {captured['date']!r} — this means the write is reading "
        f"some other clock, reintroducing the wrong-day-row bug"
    )


def test_does_not_import_bare_datetime_now():
    """Guard against reverting to a naive datetime.now() call for the row
    key — source-level check since the bug is specifically about WHICH
    clock is consulted, not whether the write succeeds."""
    source = (HERE / "salat-fast.py").read_text()
    assert "datetime.now()" not in source, (
        "salat-fast.py must not call datetime.now() directly for its Neon "
        "row-lookup date — route through daytime.local_now() (lib/daytime.py) "
        "so an active /travel override or a non-traveling host (ix) doesn't "
        "silently write to the wrong day's row."
    )
