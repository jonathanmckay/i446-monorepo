"""Dated 0l catch-up cards (JM 2026-11-19): "those tasks should fill out the
habit for the correct day if I mark them done."

A card named `0l 11.16` (made by /1hcme for a day whose 0l was skipped, or by
a defer) used to close down the generic Todoist path: [N] credited to today,
the 11/16 0n row never marked. It now runs 0n-backfill for that day and
closes that exact card. Nothing here reaches Excel, Todoist or a subprocess.
"""
import datetime as dt
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
TODAY = dt.date(2026, 11, 19)
OK = json.dumps({"moves": [{"col": "T", "name": "个", "pts": 20}], "points_awarded": 20})
ALREADY = json.dumps({"already_credited": True, "moves": [], "points_awarded": 0})


@pytest.fixture(scope="module")
def df():
    spec = importlib.util.spec_from_file_location("did_fast_dated_0l", HERE / "did-fast.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["did_fast_dated_0l"] = m
    spec.loader.exec_module(m)
    return m


class Env:
    def __init__(self, df, monkeypatch):
        self.df = df
        self.ran = []       # 0n-backfill argv
        self.closed = []    # Todoist ids closed
        self.recorded = []  # completed-today names
        self.proc = types.SimpleNamespace(returncode=0, stdout=OK, stderr="")
        self.by_id = {}
        monkeypatch.setattr(df._daytime, "today", lambda: TODAY)
        monkeypatch.setattr(df.subprocess, "run", self._run)
        monkeypatch.setattr(df, "close_todoist_task", self._close)
        monkeypatch.setattr(df, "_fetch_task_by_id", lambda tid: self.by_id.get(tid))
        monkeypatch.setattr(df.mc, "append_names",
                            lambda names, **k: self.recorded.extend(names))
        monkeypatch.setattr(df.signal, "alarm", lambda secs: 0)

    def _run(self, argv, **kw):
        self.ran.append(argv[1:])
        if isinstance(self.proc, BaseException):
            raise self.proc
        return self.proc

    def _close(self, tid, _retry=True, expected_due=None):
        self.closed.append(tid)
        return tid, True, None


@pytest.fixture
def env(df, monkeypatch):
    return Env(df, monkeypatch)


TQ = {"updated": "x", "today": [
    {"id": "LIVE", "content": "0l (8) [20]", "due": "2026-11-20"},
    {"id": "A16", "content": "0l 11.16 (8) [20]", "due": "2026-11-19"},
    {"id": "A18", "content": "0l 11.18 (8) [20]", "due": "2026-11-19"},
]}


# ── which names are dated 0l cards ──────────────────────────────────────────

def test_the_parsed_card_name_resolves_to_its_day(df, env):
    name = df.parse_input("0l 11.16 (8) [20]")[0].name
    assert df.dated_0l_day(name) == dt.date(2026, 11, 16)
    assert df.dated_0l_day("😈 0l 11.16") == dt.date(2026, 11, 16)


def test_the_live_card_and_other_habits_are_left_alone(df, env):
    for name in ("0l", "0l (8) [20]", "0t 11.16", "xk20 11.16", "0long 11.16", "0l 13.40"):
        assert df.dated_0l_day(name) is None, name


def test_today_or_a_future_date_is_not_a_catch_up(df, env):
    assert df.dated_0l_day("0l 11.19") is None            # today: the recurring card's job
    assert df.dated_0l_day("0l 12.30") is None            # not in the last 90 days
    assert df.dated_0l_day("0l 12.30", dt.date(2027, 1, 3)) == dt.date(2026, 12, 30)


# ── completing one ──────────────────────────────────────────────────────────

def test_marks_that_day_and_closes_that_card_only(env):
    out = env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ)
    assert env.ran == [[str(env.df.ON_BACKFILL), "0l", "2026-11-16"]]
    assert env.closed == ["A16"]                      # not LIVE, not the 11.18 sibling
    assert out["0n_write"]["ok"] and out["todoist"]["closed"] and out["points"] == 20
    assert "id" not in out["todoist"]                 # undo-fast must not half-reverse it
    assert env.recorded == ["0l 11.16 (8) [20]"]


def test_dtd_row_id_wins_when_it_names_the_same_day(env):
    env.by_id["DUP"] = {"id": "DUP", "content": "0l 11.16 (8) [20]", "due": "2026-11-19"}
    env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ, task_id="DUP")
    assert env.closed == ["DUP"]


def test_a_row_id_for_another_day_is_ignored(env):
    env.by_id["A18"] = TQ["today"][2]
    env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ, task_id="A18")
    assert env.closed == ["A16"]


def test_no_card_still_marks_the_day(env):
    out = env.df.run_dated_0l("0l 11.10", dt.date(2026, 11, 10), TQ)   # /0s <date>, no card
    assert env.ran and env.closed == []
    assert out["0n_write"]["ok"] and not out["todoist"]["closed"]


def test_already_marked_closes_the_card_with_no_credit(env):
    env.proc = types.SimpleNamespace(returncode=0, stdout=ALREADY, stderr="")
    out = env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ)
    assert env.closed == ["A16"] and out["points"] == 0 and out["0n_write"]["already"]


@pytest.mark.parametrize("rc,stdout", [
    (1, ""),                                             # nothing landed
    (2, json.dumps({"recovery": "run --move-only"})),    # marked, points not moved
])
def test_a_failed_backfill_leaves_the_card_open(env, rc, stdout):
    env.proc = types.SimpleNamespace(returncode=rc, stdout=stdout, stderr="boom")
    out = env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ)
    assert env.closed == [] and env.recorded == []
    assert not out["0n_write"]["ok"] and out["error"]
    if rc == 2:
        assert "--move-only" in out["error"]


def test_a_timeout_is_an_error_not_a_crash(env):
    env.proc = env.df.subprocess.TimeoutExpired("0n-backfill", 330)
    out = env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ)
    assert env.closed == [] and "did not finish" in out["error"]


# ── wiring in main(): before the generic route, never under --points-only ───

def test_main_intercepts_before_routing_and_reports_0n_write(df):
    src = (HERE / "did-fast.py").read_text()
    i = src.index("# 1c. Dated 0l catch-up cards")
    j = src.index("# 2. Load caches", i)
    seg = src[i:j]
    assert src.index("items = parse_input(raw)") < i < src.index("routes = route_items(")
    assert "if not points_only and not past_ok:" in seg
    assert '"0n_write": dated_entries[0]["0n_write"]' in seg   # what 0s.py's _mark_0l reads
    assert "sys.exit(1)" in seg                                 # a failed mark is not a clean exit
    assert "ritual_entries + dated_entries" in src[j:]          # mixed batches keep the entry


# ── dated inbox-backlog cards ("ibx m5x2 11.16"), JM 2026-11-24 ─────────────

TQ_IBX = {"updated": "x", "today": [
    {"id": "LIVE", "content": "ibx m5x2 (4) [15]", "due": "2026-11-19"},
    {"id": "I16", "content": "ibx m5x2 11.16 (30) [15]", "due": "2026-11-19"},
    {"id": "I17", "content": "ibx m5x2 11.17 (10) [15]", "due": "2026-11-19"},
    {"id": "A16", "content": "0l 11.16 (8) [20]", "due": "2026-11-19"},
]}


def test_an_ibx_m5x2_day_card_resolves_to_habit_and_day(df, env):
    name = df.parse_input("ibx m5x2 11.16 (30) [15]")[0].name
    assert df.dated_habit_day(name) == ("ibx m5x2", dt.date(2026, 11, 16))
    assert df.dated_habit_day("0l 11.16") == ("0l", dt.date(2026, 11, 16))
    assert df.dated_0l_day(name) is None                 # the 0l-only helper stays 0l-only


def test_the_live_ibx_card_and_other_inboxes_are_left_alone(df, env):
    for name in ("ibx m5x2", "ibx m5x2 (4) [15]", "ibx i9 11.16", "ibx s897 11.16",
                 "slack m5x2 11.16", "ibx m5x2 11.19"):       # 11.19 = today
        assert df.dated_habit_day(name) is None, name


def test_an_ibx_day_card_backfills_its_own_habit_and_closes_only_itself(env):
    out = env.df.run_dated_0l("ibx m5x2 11.16", dt.date(2026, 11, 16), TQ_IBX,
                              habit="ibx m5x2")
    assert env.ran == [[str(env.df.ON_BACKFILL), "ibx m5x2", "2026-11-16"]]
    assert env.closed == ["I16"]          # not LIVE, not 11.17, not the 0l card of that day
    assert out["0n_write"]["ok"] and out["todoist"]["closed"]


def test_a_0l_card_never_closes_the_ibx_card_of_the_same_day(env):
    env.df.run_dated_0l("0l 11.16", dt.date(2026, 11, 16), TQ_IBX)
    assert env.closed == ["A16"]


def test_a_failed_ibx_backfill_names_the_habit(env):
    env.proc = types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
    out = env.df.run_dated_0l("ibx m5x2 11.16", dt.date(2026, 11, 16), TQ_IBX,
                              habit="ibx m5x2")
    assert env.closed == [] and "0n-backfill ibx m5x2 11/16 failed" in out["error"]


def test_a_dated_error_fails_a_mixed_batch_too(df):
    src = (HERE / "did-fast.py").read_text()
    assert 'failed += [str(e.get("error")) for e in dated_entries if e.get("error")]' in src
