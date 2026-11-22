#!/usr/bin/env python3
"""daily_watch: the pure decision (2026-11-21).

Bug it exists for: the `ibx s897` dtd badge was blank for nine days because
its counter failed the same way on every cycle and nothing alerted; and three
dtd claims sat idle for 9-11 days unseen.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOW = 1_795_000_000.0
DAY = 86400


def _load():
    spec = importlib.util.spec_from_file_location("daily_watch", HERE / "daily_watch.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["daily_watch"] = mod
    spec.loader.exec_module(mod); return mod


def _snap(age=60, at=NOW, **habits):
    return {"ts": at - age, "host": "ix-local", "habits": habits}


def _reasons(alerts):
    return [(a["tool"], a["reason"]) for a in alerts]


def test_badge_alerts_only_on_the_second_consecutive_failing_run():
    dw = _load()
    ok, bad = {"count": 0, "ok": True}, {"count": 0, "ok": False}
    a, st = dw.decide(_snap(**{"ibx m5x2": ok, "slack m5x2": bad}), {}, {}, NOW)
    assert a == [] and st["failing"] == ["slack m5x2"]            # first sighting: remembered, quiet
    a, st2 = dw.decide(_snap(at=NOW + DAY, **{"ibx m5x2": ok, "slack m5x2": bad}), {}, st, NOW + DAY)
    assert _reasons(a) == [("inbox_counts", "stuck_failing")] and "slack m5x2" in a[0]["detail"]
    a, _ = dw.decide(_snap(at=NOW + 2 * DAY, **{"ibx m5x2": ok, "slack m5x2": bad}), {}, st2, NOW + 2 * DAY)
    assert _reasons(a) == [("inbox_counts", "stuck_failing")]     # re-emitted daily while it holds
    a, st3 = dw.decide(_snap(at=NOW + 2 * DAY, **{"ibx m5x2": ok, "slack m5x2": ok}), {}, st2, NOW + 2 * DAY)
    assert a == [] and st3["failing"] == []                       # recovery clears it


def test_badge_ok_wins_over_ok_and_a_vanished_habit_counts_as_failing():
    dw = _load()
    s897 = {"count": 0, "ok": False, "badge_count": 0, "badge_ok": True}   # ix: imsg unreadable, badge fine
    a, st = dw.decide(_snap(**{"ibx s897": s897}), {}, {"failing": ["ibx s897"]}, NOW)
    assert a == [] and st == {"known": ["ibx s897"], "failing": []}
    # habit drops out of the snapshot (its badge channel stopped reporting)
    a, st = dw.decide(_snap(at=NOW + DAY), {}, st, NOW + DAY)
    assert a == [] and st["failing"] == ["ibx s897"]
    a, _ = dw.decide(_snap(at=NOW + 2 * DAY), {}, st, NOW + 2 * DAY)
    assert _reasons(a) == [("inbox_counts", "stuck_failing")]


def test_dead_counter_alerts_stale_and_keeps_habit_memory():
    dw = _load()
    prev = {"known": ["ibx m5x2"], "failing": ["ibx m5x2"]}
    for snap in (None, {"habits": {}}, _snap(age=2 * 3600, **{"ibx m5x2": {"count": 1, "ok": True}})):
        a, st = dw.decide(snap, {}, prev, NOW)
        assert _reasons(a) == [("inbox_counts", "stale")] and st == prev


def test_stale_claims_one_summary_line_and_only_open_idle_ones():
    dw = _load()
    def c(task, days, state="idle", done=False, state_days=None):
        return {"task": task, "claimed_at": NOW - days * DAY, "state": state, "done": done,
                "state_ts": NOW - (days if state_days is None else state_days) * DAY}
    claims = {
        "a": c("LX bday 2: book Chinese dinner for two on 12/08", 11.2, "working"),
        "b": c("Inkwell ios: caret drawn on the wrong line", 9.4),
        "c": c("fresh thread", 0.5),
        "d": c("old but touched yesterday", 12, state_days=1),
        "e": c("finished, waiting on JM", 20, "done", done=True),
        "f": c("released", 20, "released"),
    }
    a, _ = dw.decide(_snap(), claims, {}, NOW)
    assert _reasons(a) == [("agent_claims", "stale")]
    d = a[0]["detail"]
    assert d.startswith("2 dtd claim(s) idle 7+ days") and "(11d)" in d and "(9d)" in d and len(d) <= 120
    assert "fresh" not in d and "touched" not in d and "finished" not in d and "released" not in d
    assert dw.decide(_snap(), None, {}, NOW)[0] == []             # unreadable claim list: no alert


def test_detail_fits_the_weekly_reports_120_chars():
    dw = _load()
    many = {str(i): {"task": f"some fairly long task name number {i}", "claimed_at": NOW - 30 * DAY,
                     "state_ts": NOW - 30 * DAY, "state": "idle", "done": False} for i in range(12)}
    d = dw.decide(_snap(), many, {}, NOW)[0][0]["detail"]
    assert len(d) <= 120 and d.startswith("12 dtd claim(s)") and "more" in d
