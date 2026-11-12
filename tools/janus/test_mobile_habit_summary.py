"""JM 2026-11-12: "the second line of janus should show the status of neon
habits." /api/timeline["habits"] grows a plain-text "summary" next to the
line1/line2 chips: `0₦ done/total · pending names` (or `· all done`). Excel is
stubbed exactly as in test_mobile_habit_strip.py; nothing is written."""
import datetime as dt

from test_mobile import jm  # noqa: F401
from test_mobile_habit_strip import _stub_excel


def test_summary_counts_done_over_total_and_lists_pending_in_line1_order(jm, monkeypatch):
    _stub_excel(jm, monkeypatch)
    jm._HABIT_HEADER_CACHE.clear()
    h = jm._habit_strip(dt.date.today())
    # ROW: 睡觉 449 + teams 1 done; hiit + xk20 blank -> pending; cpap explicit
    # 0 = N/A (neither); ص is a counter; 词汇 skip-listed; o314 YTD -> never counted.
    assert h["summary"] == "0₦ 2/4 · hiit xk20"
    assert [c["text"] for c in h["line1"][:2]] == ["hiit", "xk20"]
    assert isinstance(h["summary"], str) and "\n" not in h["summary"]


def test_summary_from_parsed_row_fixture_all_done_and_none_done(jm):
    assert jm._habit_summary({"pending": [], "done": [("睡觉", 449.0), ("teams", 1.0), ("hiit", 20.0)],
                              "prayer": 5.0}) == "0₦ 3/3 · all done"
    assert jm._habit_summary({"pending": ["hiit", "xk20", "cpap"], "done": [],
                              "prayer": None}) == "0₦ 0/3 · hiit xk20 cpap"
    assert jm._habit_summary({}) == "0₦ 0/0 · all done"


def test_summary_failure_keeps_the_chip_lines(jm, monkeypatch):
    _stub_excel(jm, monkeypatch)
    jm._HABIT_HEADER_CACHE.clear()
    monkeypatch.setattr(jm, "_habit_summary", lambda parsed: 1 / 0)
    h = jm._habit_strip(dt.date.today())
    assert h is not None and "summary" not in h
    assert h["line2"][-1]["text"] == "kcal 650"


def test_timeline_full_today_payload_carries_habits_summary(jm, monkeypatch):
    _stub_excel(jm, monkeypatch)
    jm._HABIT_HEADER_CACHE.clear()
    monkeypatch.setattr(jm, "_fetch_today", lambda *a: [])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda *a: [])
    assert jm.build_timeline()["habits"]["summary"] == "0₦ 2/4 · hiit xk20"
    assert "habits" not in jm.build_timeline(lite=True)
    d = jm.app.test_client().get("/api/timeline").get_json()
    assert d["ok"] and d["habits"]["summary"] == "0₦ 2/4 · hiit xk20"
