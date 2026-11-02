"""JM 2026-11-01: a swiped (finished) entry logs with its own HHMM-HHMM range
so did-fast credits the 地支 block it ended in and splits a >100分 per-minute
entry per block; the entry's tags ride as #tags (did-fast recreates the
entry); the recreated Toggl ids are ledgered; a rate answer passes --rate."""
import datetime as dt
import json

from test_mobile import jm  # noqa: F401


def _today_entry(jm, sh, sm, eh, em, eid=7, desc="walk", tags=None, running=False):
    d = dt.datetime.now(jm.TZ).date()
    st = dt.datetime.combine(d, dt.time(sh, sm), jm.TZ)
    en = dt.datetime.combine(d, dt.time(eh, em), jm.TZ)
    e = {"id": eid, "description": desc, "start": st.isoformat(), "project_id": None,
         "tags": tags or [], "duration": -1 if running else int((en - st).total_seconds())}
    if not running:
        e["stop"] = en.isoformat()
    return e


def _fake_did(jm, monkeypatch, results, seen):
    def fake_run(cmd, **kw):
        seen.append(cmd)
        class P:
            returncode = 0
            stdout = json.dumps({"results": results})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)


def test_log_sends_range_and_tags_and_ledgers_created_ids(jm, monkeypatch):
    monkeypatch.setattr(jm.toggl_api, "get_entries",
                        lambda **kw: [_today_entry(jm, 9, 18, 12, 41, tags=["-2", "slack github"])])
    monkeypatch.setattr(jm, "habit_tags", lambda tags: [])
    seen = []
    results = [
        {"name": "walk", "step": "variable", "0fen": {"col": "W", "points": 42}, "block": "巳",
         "split": {"part": 0, "of": 3, "range": "0918-1000"}, "toggl": {"ok": True, "ids": [101]}},
        {"name": "walk", "step": "variable", "0fen": {"col": "W", "points": 120}, "block": "午",
         "split": {"part": 1, "of": 3, "range": "1000-1200"}, "toggl": {"ok": True, "ids": [102]}},
        {"name": "walk", "step": "variable", "0fen": {"col": "W", "points": 41}, "block": "未",
         "split": {"part": 2, "of": 3, "range": "1200-1241"}, "toggl": {"ok": True, "ids": [103]}},
    ]
    _fake_did(jm, monkeypatch, results, seen)
    r = jm.log_entry("7", "walk", 203, "hcb", tags=["-2", "slack github"])
    assert r["ok"] and r["points"] == 203
    # Range + project + single-word tag only ("slack github" would leak into the name).
    assert seen[0][-1] == "walk 0918-1241 @hcb #-2"
    assert "--rate" not in seen[0]
    assert r["tag_steps"] == []            # split segments are not habit-tag items
    assert [s["ids"] for s in r["segments"]] == [[101], [102], [103]]
    today = dt.datetime.now(jm.TZ).date()
    led = jm._ledger(today)
    assert {"7", "101", "102", "103"} <= set(led)
    assert led["102"].endswith("→午")
    pts = jm._logged_points(today)
    assert pts["7"]["points"] == 203 and pts["102"]["points"] == 120
    assert pts["103"]["credits"] == [{"col": "hcb", "points": 41}]


def test_overnight_running_or_missing_entry_keeps_minutes_form(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, [{"name": "walk", "step": "variable"}], seen)
    d = dt.datetime.now(jm.TZ).date()
    yest = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(23, 30), jm.TZ)
    stop = dt.datetime.combine(d, dt.time(7, 30), jm.TZ)
    overnight = {"id": 7, "description": "睡觉", "start": yest.isoformat(), "stop": stop.isoformat(),
                 "duration": 8 * 3600, "tags": []}
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [overnight])
    jm.log_entry("7", "walk", 480, "hcb")
    assert seen[-1][-1] == "walk 480 @hcb"
    monkeypatch.setattr(jm.toggl_api, "get_entries",
                        lambda **kw: [_today_entry(jm, 9, 0, 9, 30, eid=8, running=True)])
    jm.log_entry("8", "walk", 30, "hcb")
    assert seen[-1][-1] == "walk 30 @hcb"
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [])
    jm.log_entry("9", "walk", 30, "hcb")
    assert seen[-1][-1] == "walk 30 @hcb"


def test_rate_answer_passes_rate_flag_with_the_range(jm, monkeypatch):
    monkeypatch.setattr(jm.toggl_api, "get_entries",
                        lambda **kw: [_today_entry(jm, 9, 18, 12, 41, desc="offsite")])
    seen = []
    _fake_did(jm, monkeypatch, [{"name": "offsite", "step": "variable"}], seen)
    r = jm.log_entry("7", "offsite", 203, "i9")
    assert r["needs_points"]
    jm.log_entry("7", "offsite", 203, "i9", points="2/m")
    assert seen[-1][-1] == "offsite 0918-1241 [406] @i9"
    assert "--rate" in seen[-1] and seen[-1].index("--rate") < len(seen[-1]) - 1
    jm.log_entry("8", "offsite", 203, "i9", points="40")
    assert "--rate" not in seen[-1]


def test_run_did_fast_segments_and_tag_steps(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, [
        {"name": "run", "step": "variable", "0fen": {"col": "W", "points": 60},
         "toggl": {"ok": True, "ids": [5]}},
        {"name": "其他人", "step": "0n", "value": 60},
    ], seen)
    r = jm._run_did_fast("run 0900-1000 @hcb, 其他人 60")
    assert r["tag_steps"] == ["其他人→0n"]
    assert r["segments"] == [{"ids": [5], "block": None, "credits": [{"col": "hcb", "points": 60}]}]
