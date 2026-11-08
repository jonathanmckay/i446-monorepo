#!/usr/bin/env python3
"""agent-picks pilot (2026-11-08): eligibility, receipts, accept/reject accounting, calibration."""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import picks  # noqa: E402
import report  # noqa: E402

GOOD = "## Summary\nFixed it.\n## Proof\nsee diff.patch\n## How to verify\nrun x\n## Not done\nnothing\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(picks, "RECEIPTS", tmp_path / "receipts")
    monkeypatch.setattr(picks, "EVENTS", tmp_path / "events.jsonl")
    monkeypatch.setattr(picks, "ALERTS", tmp_path / "alerts.jsonl")
    return tmp_path


def _receipt(tid, text=GOOD, artifacts=("query.txt",), branch=None):
    d = picks.receipt_dir(tid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "receipt.md").write_text(text)
    for a in artifacts:
        (d / a).write_text("x")
    if branch:
        (d / "branch").write_text(branch)
    return d


# --- eligibility -------------------------------------------------------------
@pytest.mark.parametrize("task,why", [
    ({"id": "1", "content": "Fix leasing dashboard TS rate (30) [30]"}, "ok"),
    ({"id": "2", "content": "Fix dashboard (30)"}, "no [N]"),
    ({"id": "3", "content": "1 f693 (30) [30]", "labels": ["1neon"]}, "label 1neon"),
    ({"id": "4", "content": "Call Alex to resolve things (20) [8]"}, "needs JM"),
    ({"id": "5", "content": "review q2 rm (20) [20]", "labels": ["i9"]}, "label i9"),
    ({"id": "6", "content": "goodnight math (5) [15]", "due": {"is_recurring": True}}, "recurring"),
    ({"id": "7", "content": "intros Tian+Taichi (20) [30]"}, "needs JM"),
])
def test_eligible(task, why):
    assert picks.eligible(task, set(), set())[1] == why


def test_claimed_and_recent_are_skipped():
    t = {"id": "9", "content": "Fix x (10) [20]"}
    assert picks.eligible(t, {"9"}, set())[1] == "claimed"
    assert picks.eligible(t, set(), {"9"})[1] == "attempted recently"


def test_recently_attempted_retries_old_abstains_only():
    now = time.time()
    evs = [{"event": "abstain", "task_id": "old", "ts": now - 8 * 86400},
           {"event": "abstain", "task_id": "new", "ts": now - 86400},
           {"event": "ready", "task_id": "r", "ts": now - 30 * 86400},
           {"event": "fail", "task_id": "f", "ts": now - 3600}]
    assert picks.recently_attempted(evs, now) == {"new", "r", "f"}


# --- picker output -----------------------------------------------------------
def test_filter_picks_drops_unknown_disabled_and_unsure():
    arr = [{"task_id": "a", "class": "code", "confidence": 0.9},
           {"task_id": "zz", "class": "code", "confidence": 0.9},
           {"task_id": "b", "class": "draft", "confidence": 0.9},
           {"task_id": "c", "class": "code", "confidence": 0.5},
           {"task_id": "d", "class": "code", "confidence": "high"},
           "garbage"]
    allowed = {"code": ""}
    assert [p["task_id"] for p in picks.filter_picks(arr, {"a", "b", "c", "d"}, allowed, 3)] == ["a"]


# --- receipts ----------------------------------------------------------------
def test_receipt_needs_sections_and_artifact(store):
    _receipt("t", text="## Summary\nok\n", artifacts=())
    ok, why = picks.validate_receipt("t", "analysis")
    assert not ok and "## Proof" in why
    _receipt("u", artifacts=())
    assert picks.validate_receipt("u", "analysis") == (False, "no artifact besides receipt.md")
    _receipt("v")
    assert picks.validate_receipt("v", "analysis") == (True, "ok")


def test_code_receipt_needs_diff_and_branch(store):
    _receipt("c", artifacts=("test-output.txt",))
    assert not picks.validate_receipt("c", "code")[0]
    _receipt("c", artifacts=("diff.patch",), branch="agent/c")
    assert picks.validate_receipt("c", "code")[0]


def test_outcome(store):
    assert picks.outcome("did stuff\nABSTAIN: no AppFolio access", False, "x", "analysis") == \
        ("abstain", "no AppFolio access")
    assert picks.outcome("TIMEOUT", True, "x", "analysis")[0] == "fail"
    assert picks.outcome("RECEIPT /r", False, "x", "analysis") == ("fail", "no receipt.md")
    _receipt("y")
    assert picks.outcome("RECEIPT /r", False, "y", "analysis") == ("ready", "ok")


def test_summary_comment_keeps_summary_and_verify(store):
    _receipt("s", text=GOOD + "## Side effects observed\n- vault/x\n")
    c = picks.summary_comment("s")
    assert "Fixed it." in c and "run x" in c and "## Proof" not in c and "receipt.md" in c


# --- accept / reject / review time ---------------------------------------------
def test_close_with_ready_receipt_is_accept(store, monkeypatch):
    spawned = []
    monkeypatch.setattr(picks.subprocess, "Popen", lambda *a, **k: spawned.append(a[0]))
    _receipt("a", branch="agent/a", artifacts=("diff.patch",))
    picks.log("ready", "a", **{"class": "code", "minutes": 30, "value": 30})
    picks.set_status("a", "ready")
    picks.on_viewed("a")
    assert picks.on_close("a")
    acc = [e for e in picks.events() if e["event"] == "accept"]
    assert acc[0]["class"] == "code" and acc[0]["value"] == 30 and acc[0]["review_sec"] is not None
    assert picks.status("a") == "accepted"
    assert spawned and spawned[0][-1] == "a"          # merge.py launched detached
    assert not picks.on_close("a")                    # a second close logs nothing


def test_close_without_receipt_or_after_reject_is_not_accept(store):
    assert not picks.on_close("plain")
    picks.set_status("r", "rejected")
    assert not picks.on_close("r")
    assert picks.events() == []


def test_viewed_only_for_ready(store):
    picks.on_viewed("none")
    picks.set_status("x", "accepted")
    picks.on_viewed("x")
    assert picks.events() == []


def test_review_seconds_prefers_first_view():
    evs = [{"event": "ready", "task_id": "t", "ts": 100},
           {"event": "viewed", "task_id": "t", "ts": 500},
           {"event": "viewed", "task_id": "t", "ts": 900}]
    assert picks.review_seconds(evs, "t", 620) == 120
    assert picks.review_seconds(evs[:1], "t", 160) == 60
    assert picks.review_seconds([], "t", 160) is None


# --- calibration -------------------------------------------------------------
def _decided(cls, ev, review_sec=60, minutes=30):
    return {"event": ev, "class": cls, "review_sec": review_sec, "minutes": minutes, "task_id": "x"}


def test_class_disabled_by_reject_rate():
    evs = [_decided("draft", "accept"), _decided("draft", "reject"), _decided("draft", "reject")]
    assert "draft" in picks.disabled_classes(evs)


def test_class_disabled_by_review_cost():
    evs = [_decided("analysis", "accept", review_sec=15 * 60)] * 3   # 15 of 30 min = 50% > 33%
    assert "review" in picks.disabled_classes(evs)["analysis"]


def test_too_few_outcomes_never_disables():
    assert picks.disabled_classes([_decided("code", "reject")] * 2) == {}


# --- report ------------------------------------------------------------------
def test_report_net_per_review_minute(store):
    evs = [{"event": "accept", "task_id": "a", "ts": 10, "value": 80, "review_sec": 600},
           {"event": "reject", "task_id": "b", "ts": 11, "value": 30, "review_sec": 600},
           {"event": "ready", "task_id": "a", "ts": 5, "cost": 1.25}]
    out = report.report(evs, 0, 100)
    assert "accepted 1 (80 分)" in out and "reject rate 50%" in out
    assert "4.0 分/review-min" in out and "PAYS" in out and "$1.25" in out


# --- agent_claims keeps ✅ while a receipt waits ---------------------------------
def test_done_claim_survives_ttl_while_receipt_ready(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ac_pick", HERE.parent / "did" / "agent_claims.py")
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    monkeypatch.setattr(ac, "RECEIPTS_DIR", tmp_path / "receipts")
    root = tmp_path / "claims"
    for tid in ("waiting", "stale"):
        ac.claim({"id": tid, "content": tid}, "pick-s", root, now=1000, also=True)
    ac.mark_done("pick-s", None, root, now=1000)
    (tmp_path / "receipts" / "waiting").mkdir(parents=True)
    (tmp_path / "receipts" / "waiting" / "status").write_text("ready\n")
    left = ac.load(root, now=1000 + ac.RELEASED_TTL + 5)
    assert "waiting" in left and left["waiting"]["done"] and "stale" not in left
