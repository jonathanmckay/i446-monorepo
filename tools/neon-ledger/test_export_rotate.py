"""Retention policy of export_rotate (2026-11-25): 7 blocks, 2 days, a 7 day
and a 30 day snapshot, simulated over a quarter of block-by-block exports."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_rotate as er  # noqa: E402

HOURS = [(4, 30)] + [(h, 10) for h in range(6, 23, 2)]     # ten exports a day


def name(ts: dt.datetime) -> str:
    return f"Neon分v14-export-{ts:%Y%m%d-%H%M}.xlsx"


def simulate(start: dt.date, days: int, skip=()):
    """Run the export + prune for `days` days; yield (now, surviving names)."""
    alive: set = set()
    for i in range(days):
        day = start + dt.timedelta(days=i)
        if day in skip:
            continue
        for h, m in HOURS:
            now = dt.datetime(day.year, day.month, day.day, h, m)
            alive.add(name(now))
            alive = er.keep(alive, now.date())
            yield now, set(alive)


def ages(alive, now):
    return sorted((now - er.stamp_of(n)).total_seconds() / 86400 for n in alive)


def test_steady_state_has_every_tier_and_stays_small():
    start = dt.date(2026, 11, 25)
    for now, alive in simulate(start, 120):
        assert len(alive) <= 13
        if (now.date() - start).days < 60:
            continue
        a = ages(alive, now)
        newest = sorted(alive)[-er.KEEP_BLOCKS:]
        assert all(n in alive for n in newest)                       # last 7 blocks
        days_before = {er.stamp_of(n).date() for n in alive if er.stamp_of(n).date() < now.date()}
        assert now.date() - dt.timedelta(days=1) in days_before      # yesterday
        assert now.date() - dt.timedelta(days=2) in days_before      # the day before
        assert any(6 <= x < 14 for x in a), a                        # a week back
        assert any(29 <= x < 60 for x in a), a                       # a month back


def test_a_day_keeps_its_last_snapshot():
    alive = set()
    for h, m in HOURS:
        alive.add(name(dt.datetime(2026, 11, 25, h, m)))
    kept = er.keep(alive, dt.date(2026, 11, 26))
    assert name(dt.datetime(2026, 11, 25, 22, 10)) in kept


def test_missing_days_do_not_lose_the_long_tiers():
    start = dt.date(2026, 11, 25)
    skip = {start + dt.timedelta(days=i) for i in range(60, 64)}     # Ix off for four days
    for now, alive in simulate(start, 100, skip=skip):
        if (now.date() - start).days < 70:
            continue
        a = ages(alive, now)
        # A bucket whose first days were missed starts late, so its copy is
        # younger by at most the length of the gap; it is never absent.
        assert any(7 - 4 <= x < 18 for x in a), a
        assert any(30 - 4 <= x < 64 for x in a), a


def test_other_files_are_never_candidates():
    names = {"Neon分v14-export.xlsx", "notes.txt", "Neon分v14-export-20261125-0810.xlsx",
             "Neon分v14-export-2026112-0810.xlsx"}
    assert er.keep(names, dt.date(2026, 11, 25)) == {"Neon分v14-export-20261125-0810.xlsx"}
    assert er.stamp_of("Neon分v14-export-20261340-0810.xlsx") is None


def test_rotate_copies_and_prunes(tmp_path):
    export = tmp_path / "Neon分v14-export.xlsx"
    export.write_bytes(b"book")
    snap = tmp_path / er.DIR_NAME
    snap.mkdir()
    (snap / "keep-me.txt").write_text("x")
    for i in range(12):                                              # twelve older same-day blocks
        (snap / name(dt.datetime(2026, 11, 25, 6, i))).write_bytes(b"old")
    res = er.rotate(export, now=dt.datetime(2026, 11, 25, 8, 10))
    left = sorted(p.name for p in snap.iterdir())
    assert res["snapshot"] == "Neon分v14-export-20261125-0810.xlsx"
    assert (snap / res["snapshot"]).read_bytes() == b"book"
    assert "keep-me.txt" in left
    assert len(left) == er.KEEP_BLOCKS + 1 and len(res["removed"]) == 6
