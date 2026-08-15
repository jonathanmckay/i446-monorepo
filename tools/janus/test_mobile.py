#!/usr/bin/env python3
"""Tests for janus mobile (tools/janus/mobile.py) — timeline building,
gap splitting at 地支 boundaries, and the double-log ledger guard."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def _load():
    spec = importlib.util.spec_from_file_location("janus_mobile", HERE / "mobile.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mobile"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def jm(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    # Isolate from the REAL ~/.local/state/jm/task-queue.json (2026-08-15:
    # _resolvable_points reads it directly) — without this every test using
    # log_entry/done_current implicitly depended on today's live task queue
    # NOT happening to contain a matching description, which is exactly the
    # kind of coincidental-pass this suite shouldn't rely on. Points at a
    # nonexistent path by default (_resolvable_points treats a read failure
    # as "no match", same as an empty queue); tests that need a real match
    # write their own file at this path.
    monkeypatch.setattr(mod, "TASK_QUEUE", tmp_path / "task-queue.json")
    return mod


def _entry(jm, sh, sm, eh, em, desc="work", code="i9", eid="1"):
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    return {"id": eid, "desc": desc, "project": code,
            "color": jm.COLORS.get(code, jm.DEFAULT_COLOR),
            "start": mk(sh, sm), "end": mk(eh, em), "running": False}


def test_gap_splits_at_block_boundaries(jm, monkeypatch):
    """A long untracked stretch must be split into per-block gaps with the
    地支 dividers BETWEEN the pieces, not stacked above one huge gap."""
    e = _entry(jm, 7, 0, 7, 30)
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    monkeypatch.setattr(jm._dt, "datetime", jm._dt.datetime)  # no-op, keep real now

    now = dt.datetime.now(jm.TZ)
    if now.hour < 8:
        pytest.skip("needs the 巳 boundary to have passed for a stable assert")
    tl = jm.build_timeline()
    rows = tl["rows"]
    kinds = [(r["type"], r.get("label") or r.get("start")) for r in rows]
    # gap 00:00-04:00, divider 卯, gap 04:00-06:00, divider 辰, gap 06:00-07:00,
    # then the entry.
    assert kinds[0] == ("gap", "00:00")
    assert kinds[1] == ("divider", "卯 04:00")
    assert kinds[2] == ("gap", "04:00")
    assert kinds[3] == ("divider", "辰 06:00")
    assert kinds[4] == ("gap", "06:00")
    entry_idx = next(i for i, r in enumerate(rows) if r["type"] == "entry")
    assert rows[entry_idx]["minutes"] == 30
    assert rows[entry_idx - 1]["type"] in ("gap", "divider")


def test_short_gaps_hidden(jm, monkeypatch):
    e1 = _entry(jm, 6, 0, 6, 30, eid="1")
    e2 = _entry(jm, 6, 32, 7, 0, eid="2")  # 2-min gap < MIN_GAP_MIN
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e1, e2])
    tl = jm.build_timeline()
    gaps = [r for r in tl["rows"] if r["type"] == "gap"
            and r["start"] == "06:30"]
    assert not gaps, "sub-threshold gaps must not render"


def test_ledger_blocks_double_log(jm, monkeypatch):
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "0n"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    r1 = jm.log_entry("e1", "新闻", 25, "hcmc")
    assert r1["ok"] and r1["step"] == "0n" and len(calls) == 1
    r2 = jm.log_entry("e1", "新闻", 25, "hcmc")
    assert r2.get("already") is True and len(calls) == 1, \
        "second swipe on the same entry must not re-run did-fast"


def test_log_entry_passes_minutes_and_project(jm, monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    jm.log_entry("e9", "leadership offsite", 83, "i9")
    assert seen["text"] == "leadership offsite 83 @i9"


def test_fill_gap_rejects_bad_times(jm):
    assert jm.fill_gap("x", "10:00", "09:00")["ok"] is False
    assert jm.fill_gap("x", "junk", "09:00")["ok"] is False


def test_habit_tags_filters_to_known_habits(jm):
    """Only tags naming real habits get secondary logs; Toggl meta tags and
    junk resolve to nothing (feature 2026-07-27: run tagged 其他人 credits
    both ledgers)."""
    got = jm.habit_tags(["其他人", "-3", "冥想", "not-a-habit"])
    assert "其他人" in got and "冥想" in got
    assert "-3" not in got and "not-a-habit" not in got


def test_log_entry_appends_habit_tag_items(jm, monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = __import__("json").dumps({"results": [
                {"name": "run", "step": "variable"},
                {"name": "其他人", "step": "0n"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    monkeypatch.setattr(jm, "habit_tags", lambda tags: [t for t in tags if t == "其他人"])
    r = jm.log_entry("e2", "run", 61, "hcbp", tags=["其他人", "-3"])
    assert seen["text"] == "run 61 @hcbp, 其他人 61"
    assert r["ok"] and r["tag_steps"] == ["其他人→0n"]


# ---------------------------------------------------------------------------
# edit_entry / split_entry (2026-08-06 feature — swipe-left edit, split top/bottom)
# ---------------------------------------------------------------------------

def _raw_entry(jm, sh, sm, eh, em, desc="work", pid=None, eid=1, tags=None):
    """A raw Toggl API entry dict (the true, un-clipped shape get_entries
    returns), today, [sh:sm, eh:em)."""
    today = dt.datetime.now(jm.TZ).date()
    st = dt.datetime.combine(today, dt.time(sh, sm), jm.TZ)
    en = dt.datetime.combine(today, dt.time(eh, em), jm.TZ)
    return {"id": eid, "description": desc, "start": st.isoformat(),
            "stop": en.isoformat(), "duration": int((en - st).total_seconds()),
            "project_id": pid, "tags": tags or []}


def _raw_running(jm, sh, sm, desc="work", pid=None, eid=1):
    today = dt.datetime.now(jm.TZ).date()
    st = dt.datetime.combine(today, dt.time(sh, sm), jm.TZ)
    return {"id": eid, "description": desc, "start": st.isoformat(),
            "stop": None, "duration": -1, "project_id": pid, "tags": []}


def _raw_cross_midnight(jm, desc="睡觉", eid=1, pid=None):
    """Starts 23:30 yesterday, ends 06:00 today — the nightly 睡觉 case the
    2026-08-06 review flagged: the timeline row displays a clipped
    start="00:00" (see _fetch_today), which does NOT match this entry's true
    start, so any edit that resubmits that clipped value must be refused."""
    today = dt.datetime.now(jm.TZ).date()
    yesterday = today - dt.timedelta(days=1)
    st = dt.datetime.combine(yesterday, dt.time(23, 30), jm.TZ)
    en = dt.datetime.combine(today, dt.time(6, 0), jm.TZ)
    return {"id": eid, "description": desc, "start": st.isoformat(),
            "stop": en.isoformat(), "duration": int((en - st).total_seconds()),
            "project_id": pid, "tags": []}


def test_edit_desc_only_does_not_touch_time(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 30, desc="old", eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    calls = []
    monkeypatch.setattr(jm.toggl_api, "update_entry", lambda eid, **f: calls.append((eid, f)))
    monkeypatch.setattr(jm.toggl_api, "trim_range",
                        lambda *a, **kw: pytest.fail("trim_range must not run for a desc-only edit"))
    r = jm.edit_entry("1", "new desc", "09:00", "09:30", "")
    assert r["ok"]
    assert calls == [(1, {"description": "new desc"})]


def test_edit_time_change_trims_then_updates(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 30, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    order = []
    monkeypatch.setattr(jm.toggl_api, "trim_range",
                        lambda s, en, exclude_ids=None: order.append(("trim", s, en, exclude_ids)))
    monkeypatch.setattr(jm.toggl_api, "update_entry",
                        lambda eid, **f: order.append(("update", eid, f)))
    r = jm.edit_entry("1", "work", "09:00", "10:00", "")
    assert r["ok"]
    assert [c[0] for c in order] == ["trim", "update"]
    assert order[0][3] == {1}  # excludes the entry's own id from being trimmed against itself
    assert order[1][2]["duration"] == 3600


def test_edit_rejects_cross_midnight_time_change(jm, monkeypatch):
    """The timeline shows this row's start as a clipped '00:00' — resubmitting
    that value (unchanged, from the user's perspective) must not be treated
    as a no-op; it differs from the true start (23:30 yesterday) and must be
    refused rather than silently truncating real overnight minutes."""
    e = _raw_cross_midnight(jm, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    monkeypatch.setattr(jm.toggl_api, "trim_range",
                        lambda *a, **kw: pytest.fail("must not trim a cross-midnight entry"))
    monkeypatch.setattr(jm.toggl_api, "update_entry",
                        lambda *a, **kw: pytest.fail("must not move a cross-midnight entry's time"))
    r = jm.edit_entry("1", "睡觉", "00:00", "06:00", "")
    assert not r["ok"] and "cross-midnight" in r["error"]


def test_edit_rejects_retime_of_already_logged_entry(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 30, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    jm._ledger_add(dt.datetime.now(jm.TZ).date(), "1", "already credited")
    r = jm.edit_entry("1", "work", "09:00", "10:00", "")
    assert not r["ok"] and "logged" in r["error"]


def test_edit_rejects_retime_that_would_overlap_a_logged_entry(jm, monkeypatch):
    """Retiming entry 1 into entry 2's span would make trim_range() delete/
    shrink entry 2 (already credited) and hand its remainder a brand-new,
    unlogged id — the exact mechanism behind the 2026-08-06 xk87 triple-credit
    bug, just via edit instead of the comma-split parser."""
    target = _raw_entry(jm, 9, 0, 9, 30, eid=1)
    other = _raw_entry(jm, 9, 45, 10, 15, eid=2)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [target, other])
    jm._ledger_add(dt.datetime.now(jm.TZ).date(), "2", "already credited")
    r = jm.edit_entry("1", "work", "09:00", "10:00", "")
    assert not r["ok"] and "overlap" in r["error"]


def test_edit_running_entry_allows_start_change_only(jm, monkeypatch):
    e = _raw_running(jm, 9, 0, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    calls = []
    monkeypatch.setattr(jm.toggl_api, "update_entry", lambda eid, **f: calls.append((eid, f)))
    monkeypatch.setattr(jm.toggl_api, "trim_range",
                        lambda *a, **kw: pytest.fail("must not trim a running entry"))
    r = jm.edit_entry("1", "work", "08:45", "now", "")
    assert r["ok"]
    assert len(calls) == 1
    eid, fields = calls[0]
    assert eid == 1 and set(fields) == {"description", "start"}


def test_edit_running_entry_rejects_end_change(jm, monkeypatch):
    e = _raw_running(jm, 9, 0, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    r = jm.edit_entry("1", "work", "09:00", "10:00", "")
    assert not r["ok"] and "running" in r["error"]


def test_split_top_shrinks_original_to_earlier_chunk(jm, monkeypatch):
    """id-ownership must follow the desktop TUI's ^P convention: the id
    always stays with the temporally EARLIER piece. For split-top, the chunk
    (first 10m) IS the earlier piece, so the original id shrinks to become
    it; the remainder is the new entry."""
    e = _raw_entry(jm, 9, 0, 9, 30, pid=5, eid=1, tags=["t"])  # 30min → chunk=10
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    calls = []
    monkeypatch.setattr(jm.toggl_api, "create_entry", lambda *a: calls.append(("create", a)))
    monkeypatch.setattr(jm.toggl_api, "update_entry",
                        lambda eid, **f: calls.append(("update", eid, f)))
    r = jm.split_entry("1", "top")
    assert r["ok"] and r["chunk_minutes"] == 10
    assert [c[0] for c in calls] == ["create", "update"], \
        "the new (later) piece must be created BEFORE the original is shrunk"
    _, (desc, start_iso, stop_iso, dur, pid, tags) = calls[0]
    assert dur == 1200 and pid == 5 and tags == ["t"]  # remainder: 09:10-09:30, 20min
    assert calls[1][1] == 1 and calls[1][2]["duration"] == 600  # original → 09:00-09:10


def test_split_bottom_shrinks_original_to_earlier_remainder(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 30, eid=1)  # 30min → chunk=10
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    calls = []
    monkeypatch.setattr(jm.toggl_api, "create_entry", lambda *a: calls.append(("create", a)))
    monkeypatch.setattr(jm.toggl_api, "update_entry",
                        lambda eid, **f: calls.append(("update", eid, f)))
    r = jm.split_entry("1", "bottom")
    assert r["ok"]
    _, (desc, start_iso, stop_iso, dur, pid, tags) = calls[0]
    assert dur == 600  # new entry = the LATER chunk: 09:20-09:30
    assert calls[1][1] == 1 and calls[1][2]["duration"] == 1200  # original → remainder 09:00-09:20


def test_split_chunk_scales_down_for_short_entries(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 7, eid=1)  # 7min → chunk=5 per _split_chunk_minutes
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    monkeypatch.setattr(jm.toggl_api, "create_entry", lambda *a: None)
    monkeypatch.setattr(jm.toggl_api, "update_entry", lambda *a, **k: None)
    r = jm.split_entry("1", "top")
    assert r["ok"] and r["chunk_minutes"] == 5


def test_split_refuses_when_duration_equals_its_own_chunk(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 1, eid=1)  # 1min entry → chunk=1 → refuse (0 remainder)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    r = jm.split_entry("1", "top")
    assert not r["ok"] and "too short" in r["error"]


def test_split_refuses_running(jm, monkeypatch):
    e = _raw_running(jm, 9, 0, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    r = jm.split_entry("1", "top")
    assert not r["ok"] and "running" in r["error"]


def test_split_refuses_cross_midnight(jm, monkeypatch):
    e = _raw_cross_midnight(jm, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    r = jm.split_entry("1", "top")
    assert not r["ok"] and "cross-midnight" in r["error"]


def test_split_refuses_already_logged(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 30, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    jm._ledger_add(dt.datetime.now(jm.TZ).date(), "1", "already credited")
    r = jm.split_entry("1", "top")
    assert not r["ok"] and "logged" in r["error"]


# ---------------------------------------------------------------------------
# Calendar events → convertible rows (2026-08-14 feature: ports janus.py's
# ⌥↵ branches 1 and 3 — "convert a calendar event" and "stop + log the
# running timer" — to a swipe gesture. Branch 2 (d357 recording finalize)
# stays desktop-only per user request.)
# ---------------------------------------------------------------------------

def _ev(jm, sh, sm, eh, em, title="Meeting", calendar="Outlook",
       all_day=False, transparency="opaque"):
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    return {"title": title, "start_dt": mk(sh, sm), "end_dt": mk(eh, em),
            "calendar": calendar, "all_day": all_day, "transparency": transparency}


def test_event_covered_by_any_overlapping_entry(jm):
    ev = _ev(jm, 10, 0, 10, 30)
    entries = [_entry(jm, 10, 10, 10, 20, desc="unrelated")]
    assert jm._event_covered(ev, entries)


def test_event_not_covered_when_no_overlap(jm):
    ev = _ev(jm, 10, 0, 10, 30)
    entries = [_entry(jm, 11, 0, 11, 30, desc="later")]
    assert not jm._event_covered(ev, entries)


def test_event_tracked_by_same_title_overlap(jm):
    ev = _ev(jm, 10, 0, 10, 30, title="Standup")
    entries = [_entry(jm, 10, 5, 10, 25, desc="Standup")]
    assert jm._event_tracked(ev, entries)


def test_event_tracked_by_early_running_start(jm):
    """A running timer started up to TRACKED_EARLY_START_MIN before the
    event's own start still counts as tracking it — without this, swiping
    the still-shown event would fire an un-backdated tg-fast start that
    stops the real running timer and starts a duplicate."""
    ev = _ev(jm, 10, 0, 10, 30, title="Standup")
    e = _entry(jm, 9, 50, 9, 50, desc="Standup")
    e["running"] = True
    assert jm._event_tracked(ev, [e])


def test_event_not_tracked_when_running_started_too_early(jm):
    ev = _ev(jm, 10, 0, 10, 30, title="Standup")
    e = _entry(jm, 9, 40, 9, 40, desc="Standup")  # 20min early > 15min threshold
    e["running"] = True
    assert not jm._event_tracked(ev, [e])


def test_filter_events_drops_all_day_and_transparent(jm):
    """Pure filtering logic — no calendar I/O involved (see
    _fetch_calendar_source for why the fetch itself is subprocess-isolated
    and not exercised by this unit test)."""
    all_day = _ev(jm, 0, 0, 23, 59, title="Someone's Birthday", all_day=True)
    free = _ev(jm, 9, 0, 10, 0, title="Focus block", transparency="transparent")
    real = _ev(jm, 11, 0, 11, 30, title="Standup")
    out = jm._filter_events([all_day, free, real], [])
    assert len(out) == 1 and out[0]["title"] == "Standup"


def test_filter_events_excludes_covered_and_tracked(jm):
    covered = _ev(jm, 10, 0, 10, 30, title="Covered mtg")
    tracked = _ev(jm, 11, 0, 11, 30, title="Standup")
    entries = [
        _entry(jm, 10, 5, 10, 20, desc="unrelated"),   # overlaps `covered`
        _entry(jm, 11, 0, 11, 30, desc="Standup"),      # same title as `tracked`
    ]
    assert jm._filter_events([covered, tracked], entries) == []


def test_fetch_calendar_raw_dedupes_gcal_outlook_mirror(jm, monkeypatch):
    """Same meeting mirrored across both calendar sources collapses to one
    (title, start, end) match — each source fetched via its own isolated
    subprocess (_fetch_calendar_source), stubbed out here so the test
    doesn't spawn real processes or touch real credentials."""
    ev = _ev(jm, 13, 0, 13, 30, title="Sync")

    def fake_fetch(client_name, day_start, day_end):
        return [dict(ev)]

    monkeypatch.setattr(jm, "gcal_client", object())     # just needs to be non-None
    monkeypatch.setattr(jm, "outlook_client", object())
    monkeypatch.setattr(jm, "_fetch_calendar_source", fake_fetch)
    today = dt.datetime.now(jm.TZ).date()
    day0 = dt.datetime.combine(today, dt.time(0, 0), jm.TZ)
    out = jm._fetch_calendar_raw(day0, day0 + dt.timedelta(days=1))
    assert len(out) == 1


def test_fetch_calendar_source_kills_hung_subprocess_on_timeout(jm, monkeypatch):
    """2026-08-14 incident: outlook_client's own internal timeout did not
    reliably fire from this daemon's environment, and a hung request wedged
    the entire (single-threaded dev-server) app — not just calendar rows.
    _fetch_calendar_source must return [] rather than propagate/hang when
    the underlying subprocess.run times out."""
    def fake_run(cmd, **kw):
        assert kw.get("timeout") == jm.CAL_FETCH_TIMEOUT_SEC
        raise jm.subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    today = dt.datetime.now(jm.TZ).date()
    day0 = dt.datetime.combine(today, dt.time(0, 0), jm.TZ)
    out = jm._fetch_calendar_source("outlook_client", day0, day0 + dt.timedelta(hours=1))
    assert out == []


def test_fetch_calendar_source_records_and_clears_cooldown(jm, monkeypatch):
    monkeypatch.setattr(jm, "_CAL_FAIL_UNTIL", {})
    monkeypatch.setattr(jm.subprocess, "run",
                        lambda cmd, **kw: (_ for _ in ()).throw(
                            jm.subprocess.TimeoutExpired(cmd, kw.get("timeout"))))
    today = dt.datetime.now(jm.TZ).date()
    day0 = dt.datetime.combine(today, dt.time(0, 0), jm.TZ)
    jm._fetch_calendar_source("outlook_client", day0, day0 + dt.timedelta(hours=1))
    assert "outlook_client" in jm._CAL_FAIL_UNTIL, "a timeout must start a cooldown"

    class _OKProc:
        returncode = 0
        stdout = "[]"
        stderr = ""
    monkeypatch.setattr(jm.subprocess, "run", lambda cmd, **kw: _OKProc())
    jm._fetch_calendar_source("outlook_client", day0, day0 + dt.timedelta(hours=1))
    assert "outlook_client" not in jm._CAL_FAIL_UNTIL, "a success must clear the cooldown"


def test_fetch_calendar_raw_skips_source_during_cooldown(jm, monkeypatch):
    """A source still inside CAL_FAIL_COOLDOWN_SEC of its last failure must
    not be re-fetched at all — 2026-08-14: without this, a known-broken
    source (outlook_client hung on every single call from ix) taxed every
    timeline reload with the full CAL_FETCH_TIMEOUT_SEC wait for nothing."""
    monkeypatch.setattr(jm, "gcal_client", None)
    monkeypatch.setattr(jm, "outlook_client", object())
    monkeypatch.setattr(jm, "_CAL_FAIL_UNTIL", {"outlook_client": jm.time.time() + 100})
    called = []
    monkeypatch.setattr(jm, "_fetch_calendar_source",
                        lambda *a: called.append(a) or [])
    today = dt.datetime.now(jm.TZ).date()
    day0 = dt.datetime.combine(today, dt.time(0, 0), jm.TZ)
    out = jm._fetch_calendar_raw(day0, day0 + dt.timedelta(days=1))
    assert out == [] and called == [], "cooling-down source must not be fetched at all"


def test_split_gaps_around_events_carves_out_event_window(jm):
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    gap = {"start_dt": mk(9, 0), "end_dt": mk(11, 0)}
    ev = {"start_dt": mk(9, 45), "end_dt": mk(10, 15)}
    out = jm._split_gaps_around_events([gap], [ev])
    assert [(g["start_dt"], g["end_dt"]) for g in out] == [
        (mk(9, 0), mk(9, 45)), (mk(10, 15), mk(11, 0))]


def test_split_gaps_around_events_drops_fully_covered_gap(jm):
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    gap = {"start_dt": mk(9, 0), "end_dt": mk(9, 30)}
    ev = {"start_dt": mk(8, 0), "end_dt": mk(10, 0)}  # fully swallows the gap
    assert jm._split_gaps_around_events([gap], [ev]) == []


def test_split_gap_at_boundaries(jm):
    today = dt.datetime.now(jm.TZ).date()
    day0 = dt.datetime.combine(today, dt.time(0, 0), jm.TZ)
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    out = jm._split_gap_at_boundaries(mk(3, 0), mk(7, 0), day0)
    assert out == [(mk(3, 0), mk(4, 0)), (mk(4, 0), mk(6, 0)), (mk(6, 0), mk(7, 0))]


def test_convert_past_event_shells_did_fast_with_time_range(jm, monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    r = jm.convert_event("Standup", mk(9, 0).isoformat(), mk(9, 30).isoformat(), "i9", True)
    assert r["ok"] and r["mode"] == "logged" and r["step"] == "variable"
    assert seen["text"] == "Standup 0900-0930 @i9"


def test_convert_past_event_sanitizes_comma_in_title(jm, monkeypatch):
    """did-fast splits multi-item input on [,;，；] — an event title with a
    comma must not silently become two bogus items (same bug janus.py's
    _safe_event_title fixed on desktop, 2026-07-29)."""
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    jm.convert_event("CosmosDB Deprecation, Part 3", mk(9, 0).isoformat(),
                     mk(9, 30).isoformat(), "", True)
    assert "," not in seen["text"]


def test_convert_started_event_backdates_tg_fast(jm, monkeypatch):
    """An event already in progress (start <= now) backdates the tg-fast
    start — mirrors janus.py's _event_to_tg_command."""
    seen = {}
    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    now = dt.datetime.now(jm.TZ)
    start = now - dt.timedelta(minutes=5)
    end = now + dt.timedelta(minutes=25)
    r = jm.convert_event("Standup", start.isoformat(), end.isoformat(), "i9", False)
    assert r["ok"] and r["mode"] == "started"
    assert str(jm.TG_FAST) in seen["cmd"]
    assert seen["cmd"][-1] == f"{start:%H%M} Standup @i9"


def test_convert_future_event_not_yet_started_omits_backdate(jm, monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    now = dt.datetime.now(jm.TZ)
    start = now + dt.timedelta(minutes=10)
    end = now + dt.timedelta(minutes=40)
    jm.convert_event("Standup", start.isoformat(), end.isoformat(), "i9", False)
    assert seen["text"] == "Standup @i9"


def test_convert_event_inflight_guard_rejects_concurrent_duplicate(jm):
    """did-fast's point write is an accumulating formula append, not
    idempotent like log_entry's ledger pre-check — a double-tap before the
    first call returns must not fire did-fast twice."""
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    key = f"event:Standup|{mk(9, 0).isoformat()}|{mk(9, 30).isoformat()}"
    jm._INFLIGHT.add(key)
    try:
        r = jm.convert_event("Standup", mk(9, 0).isoformat(), mk(9, 30).isoformat(), "", True)
        assert not r["ok"] and "already" in r["error"]
    finally:
        jm._INFLIGHT.discard(key)


# ---------------------------------------------------------------------------
# done_current — /done for the pinned running timer (2026-08-14)
# ---------------------------------------------------------------------------

def test_done_current_not_found(jm, monkeypatch):
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [])
    r = jm.done_current("999", "work", "i9")
    assert not r["ok"] and "not found" in r["error"]


def test_done_current_refuses_when_not_actually_running(jm, monkeypatch):
    e = _raw_entry(jm, 9, 0, 9, 30, eid=1)  # a completed entry, not running
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    r = jm.done_current("1", "work", "i9")
    assert not r["ok"] and "not running" in r["error"]


def test_done_current_refuses_just_started(jm, monkeypatch):
    now = dt.datetime.now(jm.TZ)
    e = _raw_running(jm, now.hour, now.minute, eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    r = jm.done_current("1", "work", "i9")
    assert not r["ok"] and "just started" in r["error"]


def test_done_current_stops_and_logs(jm, monkeypatch):
    now = dt.datetime.now(jm.TZ)
    start = now - dt.timedelta(minutes=5)
    e = _raw_running(jm, start.hour, start.minute, desc="deep work", eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    r = jm.done_current("1", "deep work", "i9")
    assert r["ok"] and r["step"] == "variable"
    assert seen["text"].startswith("deep work ") and seen["text"].endswith("@i9")
    assert "1" in jm._ledger(dt.datetime.now(jm.TZ).date())


def test_done_current_inflight_guard(jm):
    jm._INFLIGHT.add("done:1")
    try:
        r = jm.done_current("1", "work", "i9")
        assert not r["ok"] and "already" in r["error"]
    finally:
        jm._INFLIGHT.discard("done:1")


# ---------------------------------------------------------------------------
# _resolvable_points — dtd-task point value carried through to the swipe
# (2026-08-15 user report: "we are not passing the number of points that a
# task is worth when I start them in dtd" — a dtd-started task's [N] wasn't
# reliably landing, since the swipe just sent raw elapsed minutes and hoped
# did-fast's own fuzzy Todoist re-match happened to find the same task and
# override it).
# ---------------------------------------------------------------------------

def _write_task_queue(jm, contents):
    jm.TASK_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    jm.TASK_QUEUE.write_text(json.dumps(
        {"today": [{"id": str(i), "content": c} for i, c in enumerate(contents)]}))


def test_resolvable_points_reads_inline_bracket_without_touching_queue(jm):
    assert jm._resolvable_points("write recap [12]") == 12


def test_resolvable_points_matches_task_queue_by_exact_normalized_key(jm):
    _write_task_queue(jm, ["Odyssey Prep [30]", "unrelated task [5]"])
    assert jm._resolvable_points("odyssey prep") == 30
    # tolerant of dash/whitespace drift, same as did-fast's own _norm
    assert jm._resolvable_points("Odyssey  -  Prep") == 30


def test_resolvable_points_none_when_no_match(jm):
    _write_task_queue(jm, ["something else entirely [30]"])
    assert jm._resolvable_points("odyssey prep") is None


def test_resolvable_points_none_when_matched_task_has_no_bracket(jm):
    _write_task_queue(jm, ["odyssey prep"])  # no [N] at all
    assert jm._resolvable_points("odyssey prep") is None


def test_log_entry_uses_resolved_points_not_elapsed_minutes(jm, monkeypatch):
    """The whole point of this fix: a dtd task worth [30] must be credited
    30, not however many minutes the Toggl entry happened to run for."""
    _write_task_queue(jm, ["odyssey prep [30]"])
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    jm.log_entry("e1", "odyssey prep", 83, "i9")
    assert seen["text"] == "odyssey prep [30] @i9", \
        "must send the resolved [30], not the 83 elapsed minutes"


def test_log_entry_still_falls_back_to_minutes_when_unresolved(jm, monkeypatch):
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    jm.log_entry("e2", "unmatched activity", 40, "i9")
    assert seen["text"] == "unmatched activity 40 @i9"


def test_done_current_uses_resolved_points_explicitly(jm, monkeypatch):
    _write_task_queue(jm, ["odyssey prep [30]"])
    now = dt.datetime.now(jm.TZ)
    start = now - dt.timedelta(minutes=5)
    e = _raw_running(jm, start.hour, start.minute, desc="odyssey prep", eid=1)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [e])
    seen = {}
    def fake_run(cmd, **kw):
        seen["text"] = cmd[-1]
        class P:
            returncode = 0
            stdout = json.dumps({"results": [{"step": "variable"}]})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    r = jm.done_current("1", "odyssey prep", "i9")
    assert r["ok"]
    assert "[30]" in seen["text"]


def test_build_timeline_surfaces_resolved_points_on_entry_row(jm, monkeypatch):
    _write_task_queue(jm, ["odyssey prep [30]"])
    e = _entry(jm, 9, 0, 9, 30, desc="odyssey prep")
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda entries: [])
    tl = jm.build_timeline()
    row = next(r for r in tl["rows"] if r["type"] == "entry")
    assert row["points"] == 30


def test_resolvable_points_never_overrides_a_registered_habit(jm, monkeypatch):
    """Caught live (2026-08-15) before this ever shipped: "0l" is a
    registered 0n habit — did-fast routes it entirely by REGISTRY match
    (Steps 1-4), where [N]/points play no part at all. Its own 0neon
    Todoist reminder card can still carry an unrelated [N] (verified live:
    resolved to 20, meaningless for the 0n column write) — if that were
    passed through, did-fast would strip it as an annotation before the
    registry lookup, silently losing the real elapsed-minutes duration.
    A description that names a registered habit must never resolve here,
    inline bracket or not, regardless of what's cached in the task queue."""
    _write_task_queue(jm, ["0l reminder card [20]"])
    monkeypatch.setattr(jm, "registered_habit_names", lambda: {"0l"})
    assert jm._resolvable_points("0l") is None
    # even an inline bracket on a registered habit's own description
    assert jm._resolvable_points("0l [20]") is None


def test_build_timeline_points_none_when_unresolved(jm, monkeypatch):
    e = _entry(jm, 9, 0, 9, 30, desc="unmatched activity")
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda entries: [])
    tl = jm.build_timeline()
    row = next(r for r in tl["rows"] if r["type"] == "entry")
    assert row["points"] is None
