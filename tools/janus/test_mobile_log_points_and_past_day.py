"""2026-10-26 iOS janus requests:
- the log toast says what 分 went where (did-fast results → "credits"),
- a logged row's right column shows the 分 credited (timeline logged_points),
- swipes work on previous days (log / edit / split / delete / fill carry the
  viewed date; did-fast gets M/D + --past-ok).
Toggl and did-fast are always mocked."""
import datetime as dt
import json

from test_mobile import _entry, jm  # noqa: F401


def _fake_did(jm, monkeypatch, results, seen=None):
    def fake_run(cmd, **kw):
        if seen is not None:
            seen.append(cmd)
        class P:
            returncode = 0
            stdout = json.dumps({"results": results})
            stderr = ""
        return P()
    monkeypatch.setattr(jm.subprocess, "run", fake_run)


def _past(jm, days=2):
    return dt.datetime.now(jm.TZ).date() - dt.timedelta(days=days)


def _raw_on(jm, day, sh, sm, eh, em, eid=7, desc="work"):
    st = dt.datetime.combine(day, dt.time(sh, sm), jm.TZ)
    en = dt.datetime.combine(day, dt.time(eh, em), jm.TZ)
    return {"id": eid, "description": desc, "start": st.isoformat(), "stop": en.isoformat(),
            "duration": int((en - st).total_seconds()), "project_id": None, "tags": []}


# ── credits ──────────────────────────────────────────────────────────────
def test_credits_merge_columns_and_curly_0g(jm):
    c = jm._credits({"results": [
        {"name": "gym", "step": "todoist", "0fen": {"col": "W", "points": 12}, "curly_q": 5},
        {"name": "extra", "step": "variable", "0fen": {"col": "W", "points": 3, "bonus": 2}},
        {"name": "build", "step": "build_order", "0fen": {"col": "Z", "points": 4}},
    ]})
    assert c == [{"col": "hcb", "points": 17}, {"col": "0g", "points": 5}, {"col": "Z", "points": 4}]


def test_credits_hcbi_routes_and_bare_0n(jm, monkeypatch):
    monkeypatch.setattr(jm, "_habit_rate_points", lambda name, m: 2 * m if name == "hiit" else None)
    c = jm._credits({"results": [
        {"name": "run", "step": "variable", "hcbi": {"col": "Y", "pts": 30}},
        {"name": "hiit", "step": "0n", "value": 20},
        {"name": "新闻", "step": "0n", "value": 25},
        {"name": "skipme", "step": "skipped"},
    ]})
    assert c == [{"col": "hcb", "points": 30}, {"col": "hiit", "points": 40}, {"col": "新闻", "points": 25}]


def test_credits_empty_on_garbage(jm):
    assert jm._credits(None) == [] and jm._credits({"results": ["x", {}]}) == []


# ── log → toast + logged points ─────────────────────────────────────────
def test_log_returns_credits_and_points_and_timeline_shows_them(jm, monkeypatch):
    _fake_did(jm, monkeypatch, [{"name": "bball", "step": "variable",
                                 "0fen": {"col": "W", "points": 12}, "curly_q": 5}])
    d = jm.app.test_client().post("/api/log", json={"id": "1", "desc": "bball", "minutes": 12,
                                                    "project": "hcb"}).get_json()
    assert d["ok"] and d["credits"] == [{"col": "hcb", "points": 12}, {"col": "0g", "points": 5}]
    assert d["points"] == 17
    e = _entry(jm, 6, 0, 6, 12, desc="bball", code="hcb", eid="1")
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    row = next(r for r in jm.build_timeline(lite=True)["rows"] if r["type"] == "entry")
    assert row["logged"] and row["logged_points"] == 17


def test_log_without_credits_falls_back_to_minutes(jm, monkeypatch):
    _fake_did(jm, monkeypatch, [{"name": "x", "step": "variable"}])
    r = jm.log_entry("2", "bball", 33, "")
    assert r["credits"] == [] and r["points"] == 33


def test_logged_row_without_sidecar_uses_would_be_credit(jm, monkeypatch):
    jm._ledger_add(dt.datetime.now(jm.TZ).date(), "1", "old note")
    e = _entry(jm, 6, 0, 6, 20, desc="reading", code="hcmc", eid="1")
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    row = next(r for r in jm.build_timeline(lite=True)["rows"] if r["type"] == "entry")
    assert row["logged_points"] == 20 and row["preview_points"] is None


def test_unlogged_row_has_no_logged_points(jm, monkeypatch):
    e = _entry(jm, 6, 0, 6, 20, eid="1")
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    row = next(r for r in jm.build_timeline(lite=True)["rows"] if r["type"] == "entry")
    assert "logged_points" not in row


# ── past days ────────────────────────────────────────────────────────────
def test_past_day_log_appends_date_and_past_ok_and_ledgers_that_day(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, [{"name": "bball", "step": "variable",
                                 "0fen": {"col": "U", "points": 40}}], seen)
    day = _past(jm)
    d = jm.app.test_client().post("/api/log", json={"id": "9", "desc": "bball", "minutes": 40,
                                                    "project": "hcmc", "date": day.isoformat()}).get_json()
    assert d["ok"]
    assert seen[0][-1] == f"bball 40 @hcmc {day.month}/{day.day}"
    assert "--past-ok" in seen[0][:-1]
    assert "9" in jm._ledger(day) and "9" not in jm._ledger(dt.datetime.now(jm.TZ).date())
    assert jm._logged_points(day)["9"]["points"] == 40


def test_today_date_is_treated_as_today(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, [{"name": "x", "step": "variable"}], seen)
    today = dt.datetime.now(jm.TZ).date()
    jm.app.test_client().post("/api/log", json={"id": "3", "desc": "bball", "minutes": 5,
                                                "date": today.isoformat()})
    assert seen[0][-1] == "bball 5" and "--past-ok" not in seen[0]


def test_past_day_delete_split_edit_use_the_viewed_day(jm, monkeypatch):
    day = _past(jm)
    e = _raw_on(jm, day, 9, 0, 9, 40)
    windows = []
    def get_entries(**kw):
        windows.append(kw["start_date"])
        return [e]
    monkeypatch.setattr(jm.toggl_api, "get_entries", get_entries)
    monkeypatch.setattr(jm.toggl_api, "delete_entry", lambda eid: True)
    monkeypatch.setattr(jm.toggl_api, "create_entry", lambda *a, **k: {"id": 8})
    upd = []
    monkeypatch.setattr(jm.toggl_api, "update_entry", lambda eid, **f: upd.append(f))
    monkeypatch.setattr(jm.toggl_api, "trim_range", lambda *a, **k: None)
    c = jm.app.test_client()
    # Without the date the entry isn't on today → refused, nothing deleted.
    assert not c.post("/api/delete", json={"id": "7"}).get_json()["ok"]
    assert c.post("/api/split", json={"id": "7", "mode": "top", "date": day.isoformat()}).get_json()["ok"]
    r = c.post("/api/edit", json={"id": "7", "desc": "work", "start": "09:05", "end": "09:40",
                                  "project": "", "date": day.isoformat()}).get_json()
    assert r["ok"]
    assert upd[-1]["start"].startswith(day.isoformat() + "T09:05")
    assert c.post("/api/delete", json={"id": "7", "date": day.isoformat()}).get_json()["ok"]
    assert windows[-1] == (day - dt.timedelta(days=1)).isoformat()


def test_past_day_refuses_logged_entry_delete(jm, monkeypatch):
    day = _past(jm)
    monkeypatch.setattr(jm.toggl_api, "get_entries", lambda **kw: [_raw_on(jm, day, 9, 0, 9, 40)])
    jm._ledger_add(day, "7", "credited")
    d = jm.app.test_client().post("/api/delete", json={"id": "7", "date": day.isoformat()}).get_json()
    assert not d["ok"] and "logged" in d["error"]


def test_past_day_fill_creates_entry_on_that_day_and_refuses_ongoing(jm, monkeypatch):
    day = _past(jm)
    seen = {}
    monkeypatch.setattr(jm.toggl_api, "create_entry",
                        lambda desc, st, en, dur, project_id=None, tags=None: seen.update(st=st) or {"id": 1})
    assert jm.fill_gap("x", "10:00", "10:30", day=day)["ok"]
    assert seen["st"].startswith(day.isoformat() + "T10:00")
    assert not jm.fill_gap("x", "10:00", "", day=day)["ok"]


def test_past_day_event_log_carries_date(jm, monkeypatch):
    seen = []
    _fake_did(jm, monkeypatch, [{"name": "Standup", "step": "variable",
                                 "0fen": {"col": "R", "points": 30}}], seen)
    day = _past(jm)
    mk = lambda h, m: dt.datetime.combine(day, dt.time(h, m), jm.TZ).isoformat()
    r = jm.convert_event("Standup", mk(9, 0), mk(9, 30), "i9", True, day=day)
    assert r["ok"] and r["credits"] == [{"col": "i9", "points": 30}]
    assert seen[0][-1].endswith(f" {day.month}/{day.day}") and "--past-ok" in seen[0]


def test_parse_day(jm):
    today = dt.datetime.now(jm.TZ).date()
    assert jm._parse_day(None) is None and jm._parse_day("junk") is None
    assert jm._parse_day(today.isoformat()) is None
    assert jm._parse_day((today - dt.timedelta(days=1)).isoformat()) == today - dt.timedelta(days=1)


def test_task_done_in_dtd_marks_its_timer_entry_logged_with_points(tmp_path, monkeypatch):
    """JM 2026-10-26: set 0g in the timer, mark it done in dtd → janus shows
    the entry as logged with its 分 on the right, not minutes."""
    import datetime as _dt, json
    import mobile as m
    today = _dt.datetime.now(m._tz()).date()
    ct = tmp_path / "completed-today.json"
    ct.write_text(json.dumps({"date": today.isoformat(), "names": ["0g", "write memo"],
                              "points": {"write memo": 30}}))
    monkeypatch.setattr(m, "COMPLETED_TODAY", ct)
    import neon.rates as nr
    monkeypatch.setattr(nr, "zero_n_done_points", lambda h: 17 if h == "0g" else None)
    assert m._dtd_done_points(today) == {"0g": 17, "write memo": 30}
    monkeypatch.setattr(m, "_dtd_done_points", lambda d: {"0g": 17})
    t0 = _dt.datetime.combine(today, _dt.time(0, 5), m._tz())
    def ent(i, mins):
        s = t0 + _dt.timedelta(minutes=i * 20)
        return {"id": i, "desc": "0g", "project": "g245", "color": "#fff", "tags": [],
                "start": s, "end": s + _dt.timedelta(minutes=mins), "running": False}
    monkeypatch.setattr(m, "_fetch_today", lambda *a, **k: [ent(1, 5), ent(2, 4)])
    monkeypatch.setattr(m, "_ledger", lambda d: {})
    monkeypatch.setattr(m, "_logged_points", lambda d: {})
    tl = m.build_timeline(lite=True)
    rows = {r["id"]: r for r in tl["rows"] if r.get("type") == "entry"}
    assert rows[2]["logged"] and rows[2]["logged_points"] == 17 and rows[2]["done_in"] == "dtd"
    assert not rows[1]["logged"]   # only the latest same-name entry is claimed
