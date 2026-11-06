"""/0t's Toggl checksum: no hole over 5m, no non-sleep task over 2h."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("time_checksum", HERE / "time_checksum.py")
tc = importlib.util.module_from_spec(spec)
sys.modules["time_checksum"] = tc
spec.loader.exec_module(tc)

TZ = ZoneInfo("America/Los_Angeles")
DAY = dt.date(2026, 11, 3)
cov = tc.load_coverage(TZ)


def t(h, m=0, day=DAY):
    return dt.datetime.combine(day, dt.time(h, m), TZ)


def e(s, end, desc="work", code="i9"):
    return {"id": 1, "start": s, "end": end, "desc": desc, "code": code}


def full_day(*holes):
    """Whole day of alternating hour-long tasks, minus the (start, end) holes."""
    out, cur = [], t(0)
    for s, end in [*holes, (t(23, 59), None)]:
        while cur < s:
            nxt = min(cur + dt.timedelta(hours=1), s)
            out.append(e(cur, nxt, desc=f"task {cur.hour}"))
            cur = nxt
        cur = end or cur
    return out


def check(entries):
    return tc.check(DAY, entries, cov, use_calendar=False)[0]


def test_clean_day_passes():
    r = check(full_day())
    assert r["ok"] is True and r["missing_min"] == 0 and r["gaps"] == []


def test_five_minute_gap_is_allowed():
    r = check(full_day((t(9), t(9, 5))))
    assert r["ok"] is True and r["missing_min"] == 5


def test_six_minute_gap_is_flagged_with_a_number():
    r = check(full_day((t(9), t(9, 6))))
    assert r["coverage_ok"] is False
    assert [(g["id"], g["start"], g["minutes"]) for g in r["gaps"]] == [(1, "09:00", 6)]


def test_small_holes_never_fail_however_many():
    holes = [(t(h), t(h, 4)) for h in range(1, 11)]
    r = check(full_day(*holes))
    assert r["missing_min"] == 40 and r["gaps"] == [] and r["ok"] is True


def test_render_shows_blocks_suggestions_and_commands():
    r = check(full_day((t(9), t(9, 30))))
    r["gaps"][0].update(fill="task 8", code="i9", confidence="med")
    r["long_entries"] = [{"desc": "prep", "code": "g245", "minutes": 300, "start": "08:00", "end": "13:00"}]
    r["ok"] = False
    text = tc.render(r)
    assert "#1 09:00–09:30 30m → task 8 @i9 (med)" in text
    assert "08:00–13:00 300m prep @g245" in text
    assert "/0t fill 1 " in text and f"/0t --check {DAY}" in text


def test_render_gap_with_no_suggestion_asks_for_one():
    r = check(full_day((t(9), t(9, 30))))
    r["gaps"][0]["fill"] = "? no idea"
    r["ok"] = False
    text = tc.render(r)
    assert "no suggestion" in text and "/0t fill 1=<what it was> @<code>" in text


def test_render_never_offers_fill_when_nothing_was_checked():
    assert "fill" not in tc.render("FAILED: boom")
    assert "fill" not in tc.render({"date": "2026-11-03", "status": "no data", "ok": None})


def test_two_hour_entry_is_allowed_one_minute_over_is_not():
    assert tc.long_entries(DAY, [e(t(9), t(11))], cov) == []
    assert [x["minutes"] for x in tc.long_entries(DAY, [e(t(9), t(11, 1))], cov)] == [121]


def test_block_splits_of_one_task_count_as_one():
    es = [e(t(8), t(10), "interview prep"), e(t(10), t(12), "interview prep"),
          e(t(12), t(13), "interview prep")]
    assert [(x["desc"], x["minutes"]) for x in tc.long_entries(DAY, es, cov)] == [("interview prep", 300)]


def test_a_different_task_between_breaks_the_run():
    es = [e(t(8), t(10), "a"), e(t(10), t(10, 30), "b"), e(t(10, 30), t(12), "a")]
    assert tc.long_entries(DAY, es, cov) == []


def test_sleep_and_placeholders_are_exempt():
    es = [e(t(0), t(7), "睡觉", "睡觉"), e(t(9), t(12), "?", "")]
    assert tc.long_entries(DAY, es, cov) == []


def test_entry_is_clipped_to_the_day():
    next_day = DAY + dt.timedelta(days=1)
    es = [e(t(23), dt.datetime.combine(next_day, dt.time(3), TZ))]
    assert tc.long_entries(DAY, es, cov) == []


def test_no_entries_is_no_data_not_a_day_missing():
    r, audit = tc.check(DAY, [], cov, use_calendar=False)
    assert r["status"] == "no data" and r["ok"] is None and audit is None


def test_calendar_failure_still_gives_the_numbers(monkeypatch):
    real = cov.audit
    def flaky(*a, **k):
        if k["use_calendar"]:
            raise OSError("no calendar")
        return real(*a, **k)
    monkeypatch.setattr(cov, "audit", flaky)
    r, _ = tc.check(DAY, full_day((t(9), t(9, 30))), cov, use_calendar=True)
    assert r["missing_min"] == 30


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "STATE_PATH", tmp_path / "0t-gaps.json")
    monkeypatch.setattr(tc, "load_coverage", lambda tz: cov)
    monkeypatch.setattr(cov, "STATE_PATH", tmp_path / "2n-last.json")
    seen = {}
    def fetch(first, last):
        seen["live"] = tc.os.environ.get("TOGGL_LIVE")
        return full_day((t(9), t(9, 30)))
    monkeypatch.setattr(cov, "fetch_entries", fetch)
    monkeypatch.setattr(tc.os, "environ", {})
    return tmp_path, seen


def test_recheck_reads_live_toggl_and_restores_env(wired):
    _, seen = wired
    tc.run(dt.datetime.now(TZ).date() - dt.timedelta(days=3), TZ, interactive=False)
    assert seen["live"] == "1" and "TOGGL_LIVE" not in tc.os.environ


def test_old_day_stays_on_the_store(wired):
    _, seen = wired
    tc.run(dt.datetime.now(TZ).date() - dt.timedelta(days=120), TZ, interactive=False)
    assert seen["live"] is None


def test_unattended_run_writes_no_state(wired):
    tmp, _ = wired
    tc.run(DAY, TZ, interactive=False)
    assert not (tmp / "0t-gaps.json").exists()


def test_interactive_run_never_touches_2n_list(wired):
    tmp, _ = wired
    tc.run(DAY, TZ, interactive=True)
    assert json.loads((tmp / "0t-gaps.json").read_text())["gaps"]
    assert not (tmp / "2n-last.json").exists()
