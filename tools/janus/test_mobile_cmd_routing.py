#!/usr/bin/env python3
"""Janus web command bar routing (2026-10-17), ported from desktop janus's
Enter handler:

1. bonus `+N`/`-N` tokens and completed ranges carrying `[N]` go to did-fast;
   everything else stays on tg-fast; comma parts run sequentially.
2. `/d <task>` (or `<task> /d`) creates a due-today Todoist task, writes the
   dtd running hint, then starts the timer through tg-fast.
3. /api/timeline reports `untracked_since` for the red banner.

subprocess.run is monkeypatched throughout — no real tg-fast/did-fast runs.
"""
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SRC = (HERE / "mobile.py").read_text()


def _load():
    spec = importlib.util.spec_from_file_location("janus_mobile_routing", HERE / "mobile.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["janus_mobile_routing"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def jm(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "TASK_QUEUE", tmp_path / "task-queue.json")
    monkeypatch.setattr(mod, "DTD_RUNNING_HINT", tmp_path / "dtd-running-hint")
    from neon import excel
    monkeypatch.setattr(excel, "read", lambda *a, **k: {"ok": False})
    return mod


@pytest.fixture()
def calls(jm, monkeypatch):
    """Record every subprocess the command bar would spawn as (script, text).
    tg-fast answers with a one-line flash; did-fast with its JSON blob."""
    seen = []

    def fake_run(args, **kw):
        script = Path(args[1]).name
        seen.append((script, args[2]))
        if script == "did-fast.py":
            out = json.dumps({"results": [{"name": args[2], "0fen": {"points": 15}}]})
            return subprocess.CompletedProcess(args, 0, stdout=out + "\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=f"▶ {args[2]}\n", stderr="")
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    return seen


# ─── 1. did-vs-tg routing ───────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "+15 @i9",
    "+50 understand WoW ROAS @i9",
    "-10 0g",
    "0915-1000 XTECH @i9 [45]",
    "read COD deck +10 @i9",
])
def test_bonus_and_bracket_ranges_route_to_did_fast(jm, text):
    cmd, use_did = jm.resolve_part(text)
    assert use_did, text


@pytest.mark.parametrize("text", [
    "coffee @epcn",
    "1400 coffee",
    "0000-0550 睡觉",
    "stop",
    "-1t",                      # habit name, not a -N bonus
    "1823 desc [30]",           # backdated start, no end: not done yet
    "coffee 1400-1415 @epcn",   # range without [N]
])
def test_plain_timer_commands_stay_on_tg_fast(jm, text):
    cmd, use_did = jm.resolve_part(text)
    assert not use_did, text
    assert cmd == text


@pytest.mark.parametrize("text,expected", [
    ("600-607 coffee [5]", "0600-0607 coffee [5]"),
    ("9:07-9:30 standup @i9 [10]", "0907-0930 standup @i9 [10]"),
    ("0915-1000 XTECH @i9 [45]", "0915-1000 XTECH @i9 [45]"),
])
def test_did_bound_ranges_are_normalized_to_hhmm(jm, text, expected):
    cmd, use_did = jm.resolve_part(text)
    assert use_did
    assert cmd == expected


def test_has_completed_range_ignores_trailing_at_code(jm):
    assert jm._has_completed_range("coffee 1400-1415 @epcn")
    assert jm._has_completed_range("9:07-9:30 standup")
    assert not jm._has_completed_range("1400 coffee")
    assert not jm._has_completed_range("coffee @m5x2-1")


def test_has_bonus_points_only_leading_minus(jm):
    assert jm._has_bonus_points("+15 @i9")
    assert jm._has_bonus_points("-10 0g")
    assert jm._has_bonus_points("read deck +10 @i9")
    assert not jm._has_bonus_points("-1t")
    assert not jm._has_bonus_points("1815-1843 desc")
    assert not jm._has_bonus_points("coffee @epcn")


def test_run_command_sends_bonus_to_did_fast(jm, calls):
    r = jm.run_command("+15 @i9")
    assert calls == [("did-fast.py", "+15 @i9")]
    assert r["ok"] and "+15分" in r["msg"]


def test_run_command_sends_bracket_range_to_did_fast(jm, calls):
    jm.run_command("0915-1000 XTECH @i9 [45]")
    assert calls == [("did-fast.py", "0915-1000 XTECH @i9 [45]")]


def test_run_command_keeps_plain_commands_on_tg_fast(jm, calls):
    for text in ("coffee @epcn", "1400 coffee", "0000-0550 睡觉"):
        r = jm.run_command(text)
        assert calls[-1] == ("tg-fast.py", text)
        assert r == {"ok": True, "msg": f"▶ {text}"}


def test_comma_parts_run_sequentially_each_routed_on_its_own(jm, calls):
    r = jm.run_command("0000-0550 睡觉, +15 @i9, coffee @epcn")
    assert calls == [("tg-fast.py", "0000-0550 睡觉"),
                     ("did-fast.py", "+15 @i9"),
                     ("tg-fast.py", "coffee @epcn")]
    assert r["ok"]
    assert r["msg"].count(" | ") == 2


def test_fullwidth_comma_parts_split_each_routed_on_its_own(jm, calls):
    # A CJK IME emits a fullwidth comma "，"; split_parts must treat it as a
    # separator (like did-fast / tg-fast / desktop janus) or both commands
    # lump into one bogus part and the 睡觉 entry never logs. Regression for
    # "adding a 睡觉 entry does not work" (fullwidth-comma multi-command).
    r = jm.run_command("0000-0744 睡觉，0744-0810 xk22")
    assert calls == [("tg-fast.py", "0000-0744 睡觉"),
                     ("tg-fast.py", "0744-0810 xk22")]
    assert r["ok"]


def test_fullwidth_semicolon_splits_parts(jm, calls):
    jm.run_command("睡觉；coffee @epcn")
    assert calls == [("tg-fast.py", "睡觉"), ("tg-fast.py", "coffee @epcn")]


def test_multi_part_failure_reports_error(jm, monkeypatch):
    def fake_run(args, **kw):
        if args[2] == "x @zz":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="unknown project @zz\n")
        return subprocess.CompletedProcess(args, 0, stdout="▶ ok\n", stderr="")
    monkeypatch.setattr(jm.subprocess, "run", fake_run)
    r = jm.run_command("coffee, x @zz")
    assert r == {"ok": False, "error": "▶ ok | unknown project @zz"}


def test_did_fast_failure_surfaces_as_error(jm, monkeypatch):
    monkeypatch.setattr(jm.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="no domain for +15\n"))
    assert jm.run_command("+15") == {"ok": False, "error": "no domain for +15"}


def test_did_summary_formats_points_and_agent_needed(jm):
    out = json.dumps({"results": [{"name": "xtech", "0fen": {"points": 45},
                                   "todoist": {"closed": True}}],
                      "agent_needed": [{"name": "foo", "reason": "needs domain"}]})
    assert jm._did_summary("noise\n" + out) == "✓ xtech +45分 · todoist ✓  ⚠ foo: needs domain"


_REFUSED = json.dumps({"results": [{
    "name": "set up quarterly readjustment 2028", "step": "refused",
    "reason": "set up quarterly readjustment 2028: inferred 2028分 > 500 — refused; "
              "give the points explicitly as [N]"}], "agent_needed": []})


def test_did_fast_refusal_is_a_failed_log_not_a_credit(jm, monkeypatch):
    """2026-10-22: a year in task text was credited as +2028 分. did-fast now
    refuses inferred credits over janus_done.MAX_INFERRED_POINTS (exit 0,
    step "refused"); the phone must report it, not ledger the entry."""
    monkeypatch.setattr(jm.subprocess, "run", lambda a, **k: subprocess.CompletedProcess(
        a, 0, stdout=_REFUSED, stderr=""))
    r = jm._run_did_fast("set up quarterly readjustment 2028 1922-1956 @i9")
    assert r["ok"] is False
    assert "500" in r["error"] and r["credits"] == []
    assert jm._did_summary(_REFUSED).startswith("✋")
    typed = jm._run_did_cmd("x 2028 1922-1956 [5]")
    assert typed["ok"] is False and typed["error"].startswith("✋")


def test_plain_switch_clears_dtd_running_hint(jm, calls):
    jm.DTD_RUNNING_HINT.write_text("cook\t1\t42")
    jm.run_command("coffee @epcn")
    assert not jm.DTD_RUNNING_HINT.exists()
    jm.DTD_RUNNING_HINT.write_text("cook\t1\t42")
    jm.run_command("stop")
    assert not jm.DTD_RUNNING_HINT.exists()
    # a backfilled range leaves the running timer (and its hint) alone
    jm.DTD_RUNNING_HINT.write_text("cook\t1\t42")
    jm.run_command("0000-0550 睡觉")
    assert jm.DTD_RUNNING_HINT.exists()


def test_api_run_routes_through_run_command(jm, calls):
    d = jm.app.test_client().post("/api/run", json={"text": "+15 @i9"}).get_json()
    assert d["ok"]
    assert calls == [("did-fast.py", "+15 @i9")]


# ─── 2. /d <task> ───────────────────────────────────────────────────────────
def test_parse_d_command_leading_and_trailing(jm):
    assert jm.parse_d_command("/d cook breakfast with theo @xk87 [20]") == (
        "cook breakfast with theo @xk87 [20]", "cook breakfast with theo @xk87")
    assert jm.parse_d_command("cook breakfast with theo @xk87 /d") == (
        "cook breakfast with theo @xk87", "cook breakfast with theo @xk87")
    assert jm.parse_d_command("/d") is None
    assert jm.parse_d_command("/d @i9") is None          # no task words
    assert jm.parse_d_command("coffee @epcn") is None
    assert jm.parse_d_command("/did x") is None


def test_d_command_creates_task_writes_hint_and_starts_timer(jm, calls, monkeypatch):
    created = []

    def fake_new_task(content):
        created.append(content)
        return {"id": "777", "content": content}
    monkeypatch.setattr(jm, "_new_task", fake_new_task)

    r = jm.run_command("/d cook breakfast with theo @xk87 [20]")
    assert created == ["cook breakfast with theo @xk87 [20]"]
    assert calls == [("tg-fast.py", "cook breakfast with theo @xk87")]
    assert r == {"ok": True, "msg": "+todo cook breakfast with theo @xk87 [20] | "
                                    "▶ cook breakfast with theo @xk87"}
    name, epoch, tid = jm.DTD_RUNNING_HINT.read_text().split("\t")
    assert name == "cook breakfast with theo @xk87"
    assert tid == "777"
    assert abs(int(epoch) - int(dt.datetime.now().timestamp())) < 5


def test_d_command_trailing_form_via_api(jm, calls, monkeypatch):
    monkeypatch.setattr(jm, "_new_task", lambda c: {"id": "1", "content": c})
    d = jm.app.test_client().post("/api/run", json={"text": "cook breakfast with theo @xk87 /d"}).get_json()
    assert d["ok"]
    assert d["msg"].startswith("+todo cook breakfast with theo @xk87 | ")
    assert calls == [("tg-fast.py", "cook breakfast with theo @xk87")]


def test_d_command_todo_failure_still_starts_timer(jm, calls, monkeypatch):
    def boom(content):
        raise RuntimeError("todoist down")
    monkeypatch.setattr(jm, "_new_task", boom)
    r = jm.run_command("/d write memo @i9")
    assert calls == [("tg-fast.py", "write memo @i9")]
    assert r == {"ok": True, "msg": "todo err: todoist down | ▶ write memo @i9"}
    assert not jm.DTD_RUNNING_HINT.exists()


# ─── 3. untracked_since ─────────────────────────────────────────────────────
def _entry(jm, sh, sm, eh, em, running=False, eid="1"):
    today = dt.datetime.now(jm.TZ).date()
    mk = lambda h, m: dt.datetime.combine(today, dt.time(h, m), jm.TZ)
    return {"id": eid, "desc": "work", "project": "i9", "color": "#fff", "tags": [],
            "start": mk(sh, sm), "end": mk(eh, em), "running": running}


def _at(jm, h, m):
    return dt.datetime.combine(dt.datetime.now(jm.TZ).date(), dt.time(h, m), jm.TZ)


def test_untracked_since_after_threshold(jm):
    entries = [_entry(jm, 9, 0, 11, 0, eid="a"), _entry(jm, 11, 30, 12, 16, eid="b")]
    assert jm.untracked_since(entries, _at(jm, 13, 3)) == ("12:16", 47)


def test_untracked_since_below_threshold_is_null(jm):
    entries = [_entry(jm, 11, 30, 12, 16)]
    assert jm.untracked_since(entries, _at(jm, 12, 45)) == (None, None)
    assert jm.untracked_since(entries, _at(jm, 12, 46)) == ("12:16", 30)


def test_untracked_since_null_while_timer_runs_or_no_entries(jm):
    entries = [_entry(jm, 9, 0, 11, 0, eid="a"), _entry(jm, 11, 0, 13, 0, running=True, eid="b")]
    assert jm.untracked_since(entries, _at(jm, 13, 0)) == (None, None)
    assert jm.untracked_since([], _at(jm, 13, 0)) == (None, None)


def test_timeline_json_carries_untracked_since(jm, monkeypatch):
    now = dt.datetime.now(jm.TZ)
    if now.hour < 2:
        pytest.skip("needs two hours of today")
    end = now - dt.timedelta(minutes=47)
    e = {"id": "1", "desc": "work", "project": "i9", "color": "#fff", "tags": [],
         "start": end - dt.timedelta(minutes=30), "end": end, "running": False}
    monkeypatch.setattr(jm, "_fetch_today", lambda: [e])
    monkeypatch.setattr(jm, "_fetch_events_today", lambda entries: [])
    monkeypatch.setattr(jm, "registered_habit_names", lambda: set())
    d = jm.app.test_client().get("/api/timeline").get_json()
    assert d["ok"]
    assert d["untracked_since"] == end.strftime("%H:%M")
    assert d["untracked_min"] in (47, 48)

    e["running"] = True
    e["end"] = now
    d = jm.app.test_client().get("/api/timeline").get_json()
    assert d["untracked_since"] is None and d["untracked_min"] is None


def test_page_renders_untracked_banner():
    assert '<div id="untracked"></div>' in SRC
    assert "untrackedBanner(d.untracked_since, d.untracked_min);" in SRC
    assert "'untracked since '+since+' · '+mins+'m'" in SRC
    assert "#untracked { flex-basis:100%; order:-1; display:none; background:#e53935" in SRC
