"""2026-10-27 Dream v170 code review: double-credit paths in janus mobile.

- An entry crossing midnight shows on BOTH days (_fetch_today returns every
  overlapping entry) but is ledgered under one; the other day's view must
  treat it as logged, not credit it again.
- Two overlapping /api/log calls for one entry (double-tap, iOS retry while
  did-fast is still running) must credit once.
- A past-day log tests THAT day's meeting titles for the 25m→[30] rule."""
import datetime as dt
import json
import threading

from test_mobile import jm  # noqa: F401


def _fake_did(jm, monkeypatch, seen, gate=None):
    def fake_run(cmd, **kw):
        seen.append(cmd)
        if gate is not None:
            gate.wait(5)
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"name": "x", "step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)


def test_entry_logged_today_is_already_logged_in_yesterdays_view(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, seen)
    today = dt.datetime.now(jm.TZ).date()
    jm._ledger_add(today, "5", "sleep 80 → variable")  # done-current at 00:20
    r = jm.log_entry("5", "sleep", 80, "", day=today - dt.timedelta(days=1))
    assert r.get("already") is True
    assert seen == []


def test_entry_logged_yesterday_is_already_logged_today(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, seen)
    today = dt.datetime.now(jm.TZ).date()
    jm._ledger_add(today - dt.timedelta(days=1), "6", "work 90 → variable")
    r = jm.log_entry("6", "work", 90, "")
    assert r.get("already") is True and seen == []
    assert "6" in jm._ledger_near(today)


def test_concurrent_logs_of_one_entry_credit_once(jm, monkeypatch):
    seen = []
    gate = threading.Event()
    _fake_did(jm, monkeypatch, seen, gate)
    out = []
    t = threading.Thread(target=lambda: out.append(jm.log_entry("9", "x", 20, "")))
    t.start()
    for _ in range(100):
        if seen:
            break
        threading.Event().wait(0.01)
    second = jm.log_entry("9", "x", 20, "")
    gate.set()
    t.join(5)
    assert len(seen) == 1
    assert second["ok"] is False
    assert out and out[0]["ok"]


def test_past_day_speedy_meeting_uses_that_days_titles(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, seen)
    past = dt.datetime.now(jm.TZ).date() - dt.timedelta(days=2)
    monkeypatch.setitem(jm._MEETING_TITLES, past, {jm._norm_meeting_title("Reid sync")})
    monkeypatch.setattr(jm, "_resolvable_points", lambda desc: None)
    jm.log_entry("11", "Reid sync", 25, "", day=past)
    assert seen and "Reid sync [30]" in " ".join(map(str, seen[0]))
