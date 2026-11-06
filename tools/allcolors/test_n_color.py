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
    def _run(cells, times, unknown=None, day=D):
        writes = []
        monkeypatch.setattr(ac_tool, "Day", lambda d, h: FakeDay(d, h, cells))
        monkeypatch.setattr(ac_tool, "_done_at", lambda c, s, col, r: times.get(col))
        monkeypatch.setattr(ac_tool, "write",
                            lambda d, col, v, why, dry: writes.append((d, col, v)))
        ac_tool.settle_n_color(day, HEADERS, None, False, unknown=unknown)
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
