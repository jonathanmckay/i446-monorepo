#!/usr/bin/env python3
"""N color (0n!AF) is stamped here, not in did-fast (JM 2026-11-06): the first
moment both 0t and 0l were done, in either order. Before, did-fast stamped on
any 0l write, so 10/27 got 800 with 0t never done and a 0l re-log on 11/3 moved
613 to 2121."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("allcolors_tool", HERE / "allcolors.py")
ac_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac_tool)

D = dt.date(2026, 11, 6)
HEADERS = ["0t", "0l", "N color"]


def at(h, m=0):
    return dt.datetime.combine(D, dt.time(h, m))


class FakeDay:
    def __init__(self, d, headers, cells):
        self.date, self.row = d, 311
        self.cells = {"Q": ("0t", cells[0]), "S": ("0l", cells[1]), "AF": ("N color", cells[2])}

    raw = ac_tool.Day.raw


@pytest.fixture
def run(monkeypatch):
    def _run(cells, times, unknown=None, day=D, closed=False):
        writes = []
        monkeypatch.setattr(ac_tool, "Day", lambda d, h: FakeDay(d, h, cells))
        monkeypatch.setattr(ac_tool, "_done_at", lambda c, s, col, r: times.get(col))
        monkeypatch.setattr(ac_tool, "write",
                            lambda d, col, v, why, dry: writes.append((d, col, v)))
        ac_tool.settle_n_color(day, HEADERS, None, False, unknown=unknown, closed=closed)
        return writes
    return _run


def test_stamps_when_0t_lands_second(run):
    assert run(("1", "12", ""), {"Q": at(6, 57), "S": at(6, 12)}) == [(D, "AF", 657)]


def test_stamps_when_0l_lands_second(run):
    assert run(("1", "23", ""), {"Q": at(9, 8), "S": at(9, 45)}) == [(D, "AF", 945)]


def test_0l_alone_stamps_nothing(run):
    assert run(("", "4", ""), {"S": at(8)}) == []


def test_an_existing_stamp_is_never_moved(run):
    assert run(("1", "69", "613"), {"Q": at(6, 57), "S": at(21, 21)}) == []


def test_a_habit_finished_after_the_day_ended_does_not_count(run):
    late = dt.datetime(2026, 11, 7, 7, 0)
    assert run(("1", "5", ""), {"Q": at(8), "S": late}) == []


def test_closed_day_on_time_keeps_the_real_time(run):
    assert run(("1", "5", ""), {"Q": at(8), "S": at(9)}, closed=True) == [(D, "AF", 900)]


def test_closed_day_finished_late_gets_3000(run):
    late = dt.datetime(2026, 11, 7, 7, 0)
    assert run(("1", "5", ""), {"Q": at(8), "S": late}, closed=True) == [(D, "AF", 3000)]


def test_closed_day_with_an_untraced_cell_gets_3000_not_2359(run):
    assert run(("1", "5", ""), {"Q": at(8)}, closed=True) == [(D, "AF", 3000)]


def test_closed_day_missing_0t_stamps_nothing(run):
    assert run(("", "5", ""), {"S": at(8)}, closed=True) == []


def test_closed_day_never_overwrites(run):
    assert run(("1", "5", "711"), {}, closed=True) == []


def test_sweep_only_settles_filled_blank_days(monkeypatch):
    headers = ["0t", "0l", "N color"]
    rows = [["1", "5", "800"], ["1", "", ""], ["1", "5", ""]]
    seen = []
    monkeypatch.setattr(ac_tool.excel, "lookup_row", lambda s, d: 311)
    monkeypatch.setattr(ac_tool, "_rows", lambda s, r: rows if r == "D308:AS310" else [])
    monkeypatch.setattr(ac_tool, "settle_n_color",
                        lambda d, h, c, dry, closed: seen.append((d, closed)))
    ac_tool.settle_closed_n_color(D, headers, None, False, lookback=3)
    assert seen == [(dt.date(2026, 11, 5), True)]


def test_sweep_stops_at_the_first_day_row(monkeypatch):
    reads = []
    monkeypatch.setattr(ac_tool.excel, "lookup_row", lambda s, d: 7)
    monkeypatch.setattr(ac_tool, "_rows", lambda s, r: reads.append(r) or [])
    ac_tool.settle_closed_n_color(D, ["0t", "0l", "N color"], None, False)
    assert reads == ["D5:AS6"]


class LateDay:
    """One closed day for settle_late_all_colors, with cell timing faked."""

    def __init__(self, monkeypatch, row, strict_ag="", late_hit=True, day=dt.date(2026, 11, 4)):
        self.writes, self.gathered = [], []
        monkeypatch.setattr(ac_tool, "_closed_rows", lambda t, h, n: [(day, row)])
        monkeypatch.setattr(ac_tool, "settle", lambda d, h, c, dry: (strict_ag, row.get("#")))
        monkeypatch.setattr(ac_tool, "gather",
                            lambda d, h, c, late_ok: self.gathered.append(late_ok) or (None, [], []))
        monkeypatch.setattr(ac_tool.ac, "evaluate", lambda *a, **k: at(23, 59) if late_hit else None)
        monkeypatch.setattr(ac_tool, "write",
                            lambda d, col, v, why, dry: self.writes.append((d, col, v)))


ROW = {"0t": "1", "0l": "5", "0g": "1", "⎣∀clr": "", "#": "6", "n color": "630", "o314": ""}


def _late(lock):
    ac_tool.settle_late_all_colors(D, [], None, False)
    return lock.writes


def test_late_writes_that_reach_all_colors_stamp_3000(monkeypatch):
    lock = LateDay(monkeypatch, dict(ROW))
    assert _late(lock) == [(dt.date(2026, 11, 4), "AG", 3000)]
    assert lock.gathered == [True]


def test_an_on_time_stamp_beats_3000(monkeypatch):
    assert _late(LateDay(monkeypatch, dict(ROW), strict_ag=955)) == []


def test_no_3000_when_late_writes_still_fall_short(monkeypatch):
    assert _late(LateDay(monkeypatch, dict(ROW), late_hit=False)) == []


def test_a_manual_ag_is_left_alone(monkeypatch):
    assert _late(LateDay(monkeypatch, {**ROW, "⎣∀clr": "2130"})) == []


def test_days_before_the_automation_are_left_alone(monkeypatch):
    assert _late(LateDay(monkeypatch, dict(ROW), day=dt.date(2026, 10, 28))) == []


def test_too_many_raw_blanks_skip_the_gather(monkeypatch):
    lock = LateDay(monkeypatch, {**ROW, "#": "0"})
    assert _late(lock) == [] and lock.gathered == []


def test_did_fast_no_longer_stamps_af():
    src = (HERE.parent / "did" / "did-fast.py").read_text()
    assert "build_0l_time_script" not in src
    assert "did 0l completion time" not in src


def test_run_py_hands_0t_0l_to_did_fast():
    # did-fast is what spawns allcolors --live after a same-day write.
    src = (HERE.parent / "did" / "run.py").read_text().split("VIA_DID_FAST = ")[1].splitlines()[0]
    assert '"0t"' in src and '"0l"' in src


def test_a_traveler_gets_local_time_not_ix_time(run, monkeypatch):
    # Ledger times are Ix's Pacific clock; 12:47 PST is 05:47 next day in Tokyo.
    from zoneinfo import ZoneInfo
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    import time
    time.tzset()
    monkeypatch.setattr(ac_tool.daytime, "local_now",
                        lambda: dt.datetime(2026, 11, 7, 6, 0, tzinfo=ZoneInfo("Asia/Tokyo")))
    assert run(("1", "5", ""), {"Q": at(9), "S": at(12, 47)}) == [(D, "AF", 547)]
