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
    assert r["ok"] is False
    assert [(g["id"], g["start"], g["minutes"]) for g in r["gaps"]] == [(1, "09:00", 6)]


def test_small_holes_never_fail_however_many():
    holes = [(t(h), t(h, 4)) for h in range(1, 11)]
    r = check(full_day(*holes))
    assert r["missing_min"] == 40 and r["gaps"] == [] and r["ok"] is True


def test_render_shows_blocks_suggestions_and_commands():
    r = check(full_day((t(9), t(9, 30))))
    r["gaps"][0].update(fill="task 8", code="i9", confidence="med")
    r["long_entries"] = [{"desc": "prep", "code": "g245", "minutes": 300, "start": "08:00",
                          "end": "13:00", "key": "k", "overridden": False}]
    r["needs_override"] = True
    text = tc.render(r)
    assert "#1 09:00–09:30 30m → task 8 @i9 (med)" in text
    assert "08:00–13:00 300m prep @g245" in text
    assert "/0t fill 1 " in text and f"/0t --check {DAY}" in text
    assert f"/0t ok {DAY}" in text and "❌ 1 hole" in text and "⚠️ 1 task" in text


def test_render_gap_with_no_suggestion_asks_for_one():
    r = check(full_day((t(9), t(9, 30))))
    r["gaps"][0]["fill"] = "? no idea"
    r["ok"] = False
    text = tc.render(r)
    assert "no suggestion" in text and "/0t fill 1=<what it was> @<code>" in text


def _long_day(overrides=frozenset()):
    return tc.check(DAY, full_day((t(8), t(12))) + [e(t(8), t(12), "prep", "g245")], cov,
                    use_calendar=False, overrides=overrides)[0]


def test_long_task_is_a_warning_not_an_error():
    r = _long_day()
    assert r["ok"] is True and r["needs_override"] is True
    text = tc.render(r)
    assert "⚠️ 1 task over 2h needs your override" in text and "✅" not in text


def test_override_clears_the_warning_and_says_so():
    key = _long_day()["long_entries"][0]["key"]
    r = _long_day({key})
    assert r["needs_override"] is False
    assert "✅ no hole over 5m, 1 over 2h overridden" in tc.render(r)


def test_a_task_that_grows_warns_again():
    key = _long_day()["long_entries"][0]["key"]
    r = tc.check(DAY, full_day((t(8), t(13))) + [e(t(8), t(13), "prep", "g245")], cov,
                 use_calendar=False, overrides={key})[0]
    assert r["needs_override"] is True


def test_override_key_survives_a_timezone_change():
    a = tc.long_entries(DAY, [e(t(9), t(12))], cov)[0]["key"]
    other = tc.load_coverage(ZoneInfo("America/New_York"))
    s = dt.datetime(2026, 11, 3, 12, tzinfo=ZoneInfo("America/New_York"))
    b = tc.long_entries(DAY, [e(s, s + dt.timedelta(hours=3))], other)[0]["key"]
    assert a == b


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
    monkeypatch.setattr(tc, "OVERRIDE_DIR", tmp_path)
    monkeypatch.setattr(tc, "load_coverage", lambda tz: cov)
    monkeypatch.setattr(cov, "STATE_PATH", tmp_path / "2n-last.json")
    seen = {}
    def fetch(first, last):
        seen["live"] = tc.os.environ.get("TOGGL_LIVE")
        return full_day((t(9), t(9, 30)))
    monkeypatch.setattr(cov, "fetch_entries", fetch)
    monkeypatch.setattr(tc.os, "environ", {})
    return tmp_path, seen


@pytest.fixture()
def over(monkeypatch, tmp_path):
    d = tmp_path / "ovr"
    d.mkdir()
    monkeypatch.setattr(tc, "OVERRIDE_DIR", d)
    return d


def test_hosts_merge_on_read(over):
    (over / "0t-overrides-a.json").write_text('{"2026-11-03": ["k1"]}')
    (over / "0t-overrides-b.json").write_text('{"2026-11-03": ["k2"], "2026-11-04": ["k3"]}')
    assert tc.load_overrides(DAY) == ({"k1", "k2"}, [])


def test_unreadable_file_is_named_and_never_stops_the_check(over):
    (over / "0t-overrides-a.json").write_text("{nope")
    (over / "0t-overrides-b.json").write_text('["list"]')
    assert tc.load_overrides(DAY) == (set(), ["0t-overrides-a.json", "0t-overrides-b.json"])


def test_record_refuses_a_malformed_file_rather_than_wipe_it(over):
    p = tc._override_path()
    p.write_text("{nope")
    with pytest.raises(ValueError):
        tc.record_overrides(DAY, ["k"])
    assert p.read_text() == "{nope"


def test_record_keeps_earlier_days(over):
    tc.record_overrides(dt.date(2026, 11, 1), ["old"])
    tc.record_overrides(DAY, ["new"])
    assert tc.load_overrides(DAY)[0] == {"new"}
    assert tc.load_overrides(dt.date(2026, 11, 1))[0] == {"old"}


def test_override_records_only_open_warnings_and_reports_them(over):
    r = _long_day()
    tc._apply_override(DAY, r)
    assert r["needs_override"] is False and [x["desc"] for x in r["recorded"]] == ["prep"]
    assert "Overridden: 08:00–12:00 240m prep @g245" in tc.render(r)
    assert tc.load_overrides(DAY)[0] == {r["long_entries"][0]["key"]}


def test_override_on_no_data_or_no_long_tasks_writes_nothing(over):
    tc._apply_override(DAY, {"date": "2026-11-03", "status": "no data", "ok": None})
    tc._apply_override(DAY, check(full_day()))
    assert not tc._override_path().exists()


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
