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
