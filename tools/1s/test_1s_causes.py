"""Tests for 1s-causes.py (feature 2026-11-15: /1s states the week's causes in
分, from the points cache, the neon ledger and the Toggl store). Pure functions
on fixtures only: no Excel, no network, no live stores."""
import datetime as dt
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).parent
SUN = dt.date(2026, 11, 8)


def _load():
    spec = importlib.util.spec_from_file_location("causes", HERE / "1s-causes.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["causes"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cache():
    """Two weeks: the prior week scores 100 i9 a day, the review week 50,
    with Tuesday's hcb at zero and all of Sunday's points in 辰."""
    c = {}
    for i in range(7):
        c[(SUN - dt.timedelta(days=7) + dt.timedelta(days=i)).isoformat()] = {
            "i9": 100, "hcb": 20, "__block__": {"辰": 120}}
        c[(SUN + dt.timedelta(days=i)).isoformat()] = {
            "i9": 50, "hcb": 0 if i == 2 else 20, "0₲": 10 * i, "__block__": {"辰": 70 if i == 0 else 0}}
    return c


def test_norm_name_strips_annotations_and_prefix():
    m = _load()
    assert m.norm_name("did Interview Prep (60) [225] →午") == "interview prep"
    assert m.norm_name("review X [(link)](https://a.b)") == "review x"


def test_weekday_and_block_profiles_compare_to_trailing_weeks():
    m = _load()
    wd = m.weekday_profile(_cache(), SUN, 1)
    assert wd[0] == ("Sun", 70, 120) and wd[2] == ("Tue", 70, 120)   # 50 i9 + 0 hcb + 20 0₲
    blocks = dict((b, (t, mean)) for b, t, mean in m.block_profile(_cache(), SUN, 1))
    assert blocks["辰"] == (70, 840) and blocks["亥"] == (0, 0)


def test_activity_yield_joins_by_name_and_reports_unmatched():
    m = _load()
    toggl = [("2026-11-09", "g245", "interview prep", 60), ("2026-11-10", "g245", "Interview Prep", 60),
             ("2026-11-09", "i444", "through airport", 90), ("2026-11-09", "i9", "short", 5),
             ("2026-11-09", "睡觉", "睡觉", 480)]
    credits = [{"date": "2026-11-09", "field": "个", "points": 420, "src": "did interview prep (120) [420]"},
               {"date": "2026-11-09", "field": "社", "points": 80, "src": "did dinner"}]
    rows, unmatched = m.activity_yield(toggl, credits)
    assert rows[0] == (0, 90, 0, "i444", "through airport")      # lowest yield first
    assert rows[1] == (210.0, 120, 420, "g245", "interview prep")
    assert all(r[4] not in ("short", "睡觉") for r in rows)
    assert unmatched == [(80, "dinner")]


def test_split_corrections_removes_write_error_and_its_repair():
    m = _load()
    credits = [{"date": "2026-11-08", "field": "xk", "points": 5228, "src": "+80 @xk87 manual"},
               {"date": "2026-11-08", "field": "xk", "points": -5148, "src": "fix: +80 concatenated onto +52"},
               {"date": "2026-11-08", "field": "xk", "points": 60, "src": "did lego"},
               {"date": "2026-11-09", "field": "个", "points": -40, "src": "did -1g"}]
    real, corrected = m.split_corrections(credits)
    assert [c["src"] for c in real] == ["did lego"]
    assert len(corrected) == 3


def test_overhead_nets_points_and_zero_time_groups_by_project():
    m = _load()
    toggl = [("2026-11-09", "g245", "-1g", 15), ("2026-11-10", "g245", "-1g", 10),
             ("2026-11-09", "g245", "1 cal", 65), ("2026-11-09", "i444", "to msft", 118),
             ("2026-11-09", "i9", "XCORE prep", 60)]
    credits = [{"date": "2026-11-09", "field": "个", "points": 7, "src": "did 1 cal (15) [15]"}]
    assert m.overhead(toggl, credits) == [("1 cal", 65, 1, 7), ("-1g", 25, 2, 0)]
    assert m.zero_point_time(toggl) == {"i444": [(118, "to msft", 1)]}


def test_build_reports_totals_gaps_and_zero_hcb_days():
    m = _load()
    toggl = [("2026-11-09", "i9", "XCORE prep", 600), ("2026-11-09", "睡觉", "睡觉", 480)]
    credits = [{"date": "2026-11-09", "field": "i9", "points": 100, "src": "did XCORE prep"}]
    r = m.build(SUN, 1, _cache(), toggl, dt.date(2026, 11, 13), credits)
    assert r["total"] == 50 * 7 + 20 * 6 + sum(10 * i for i in range(7))
    i9 = next(d for d in r["domains"] if d[0] == "i9")
    assert i9[1:] == (350, 700, 100, 250)                     # 分, trailing mean, ledger, habit gap
    assert r["zero_hcb_days"] == ["11-10"]
    assert r["waking_hours"] == 10 and round(r["avg_per_waking_hour"]) == round(r["total"] / 10)
    assert "settled only through 2026-11-13" in m.render(r)
