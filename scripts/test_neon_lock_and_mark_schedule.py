"""Regression test for com.jm.neon-lock-and-mark's launchd schedule.

2026-08-27: run_lock_and_mark's block-fire gate now resolves 'hour' via
daytime.local_now() (an active /travel override, else ix's own OS-local
zone) instead of ix's raw system clock — see build-order-daemon.py and
test_build_order_daemon_lock.py::test_run_lock_and_mark_uses_travel_aware_now.
launchd's StartCalendarInterval still only fires at REAL ix-clock hours,
though, so it must cover every hour (0-23): with a travel override whose
offset from PT is odd (e.g. Berlin, PT+9h), every even-hour PT firing maps
to an ODD active-zone hour, and BLOCK_FIRE_HOURS (all even) would then
never match, permanently no-opping the ritual-card/scoring pipeline for the
rest of the trip (confirmed live 2026-08-26 before it did damage — the
override was set and reverted the same evening). Firing every real hour
guarantees some firing lands on the active zone's even hour regardless of
offset parity.
"""
import plistlib
from pathlib import Path

PLIST = (Path.home() / "Library" / "LaunchAgents"
         / "com.jm.neon-lock-and-mark.plist")


def _load_intervals():
    with open(PLIST, "rb") as f:
        data = plistlib.load(f)
    return data["StartCalendarInterval"]


def test_schedule_covers_every_hour_of_the_day():
    intervals = _load_intervals()
    hours = sorted(i["Hour"] for i in intervals)
    assert hours == list(range(24)), (
        "StartCalendarInterval must fire every hour (0-23) so the "
        "travel-aware block-fire gate in run_lock_and_mark can't be "
        "permanently skipped by an odd PT-offset /travel override"
    )


def test_schedule_has_no_duplicate_hours():
    intervals = _load_intervals()
    hours = [i["Hour"] for i in intervals]
    assert len(hours) == len(set(hours)) == 24, (
        "exactly one entry per hour — a duplicate hour would mask a "
        "missing one under a naive len()==24 check"
    )


def test_schedule_fires_on_the_hour():
    intervals = _load_intervals()
    assert all(i.get("Minute") == 0 for i in intervals), (
        "every entry must fire at :00 — a stray Minute would desync the "
        "'one firing per real hour' guarantee this schedule relies on"
    )
