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


# --- worker permissions --------------------------------------------------------
def test_worker_allowlist_is_scoped_and_never_sends():
    import run
    allow = run.worker_permissions(Path("/Users/x/vault/z_ibx/agent-picks/receipts/t"),
                                   Path("/private/tmp/agent-t"), "t")
    assert "Write(//Users/x/vault/z_ibx/agent-picks/receipts/t/**)" in allow
    assert "Edit(//private/tmp/agent-t/**)" in allow
    assert "Bash(git -C /private/tmp/agent-t push origin HEAD:agent/t)" in allow
    # a push must name its worktree: the plain form could land the wrong repo's tip
    assert "Bash(git push origin HEAD:agent/t)" not in allow
    assert not any("send" in a or "HEAD:wip" in a for a in allow)
    assert "mcp__workspace-mcp__send_gmail_message" in run.DENY
    assert "mcp__imessage_server__imessage_send" in run.DENY
    no_wt = run.worker_permissions(Path("/r"), None, "t")
    assert not any(a.startswith("Bash(") for a in no_wt)
    # two repos (2026-11-09): each worktree writable and pushable to agent/<tid>, nothing else
    two = run.worker_permissions(Path("/r"), {"monorepo": Path("/private/tmp/agent-t"),
                                              "dashboards": Path("/private/tmp/agent-t-dashboards")}, "t")
    assert "Edit(//private/tmp/agent-t-dashboards/**)" in two
    assert "Bash(git -C /private/tmp/agent-t-dashboards push origin HEAD:agent/t)" in two
    assert not any("HEAD:main" in a for a in two)


def test_branches_parses_legacy_and_per_repo_lines(store):
    d = picks.receipt_dir("t")
    d.mkdir(parents=True)
    (d / "branch").write_text("agent/t\n")
    assert picks.branches("t") == [("monorepo", "agent/t")]
    (d / "branch").write_text("monorepo agent/t\ndashboards agent/t\nnowhere agent/t\nmonorepo agent/t\n")
    assert picks.branches("t") == [("monorepo", "agent/t"), ("dashboards", "agent/t")]
    assert picks.branch("t")  # validate_receipt's truthiness check still holds
    assert picks.REPOS["dashboards"]["land"] == "pr" and picks.REPOS["monorepo"]["land"] == "merge"


# --- second pass: asks (2026-11-09) ----------------------------------------------
def test_parse_picker_object_and_legacy_array():
    picks_, near = picks.parse_picker('noise {"picks": [{"task_id": "1"}], "near": [{"task_id": "2"}]} tail')
    assert [p["task_id"] for p in picks_] == ["1"] and [n["task_id"] for n in near] == ["2"]
    picks_, near = picks.parse_picker('[{"task_id": "1", "class": "code"}]')
    assert picks_[0]["task_id"] == "1" and near == []
    picks_, near = picks.parse_picker('{"picks": [], "near": []}')
    assert picks_ == [] and near == []
    with pytest.raises(ValueError):
        picks.parse_picker("I would pick nothing today.")
    with pytest.raises(ValueError):
        picks.parse_picker('{"task_id": "1"}')


def test_filter_near_keeps_known_fact_or_capability_with_question():
    arr = [
        {"task_id": "1", "needs": "fact", "question": "Which number\nis right?", "class": "code"},
        {"task_id": "2", "needs": "Capability", "question": "repo outside monorepo"},
        {"task_id": "9", "needs": "fact", "question": "unknown task"},
        {"task_id": "3", "needs": "mood", "question": "bad needs"},
        {"task_id": "4", "needs": "fact", "question": ""},
        "garbage",
    ]
    out = picks.filter_near(arr, {"1", "2", "3", "4"})
    assert [(n["task_id"], n["needs"]) for n in out] == [("1", "fact"), ("2", "capability")]
    assert out[0]["question"] == "Which number is right?"
    assert len(picks.filter_near([{"task_id": str(i), "needs": "fact", "question": "q"} for i in range(9)],
                                 {str(i) for i in range(9)}, maxn=2)) == 2


def test_open_asks_and_asked_recently():
    now = 1_000_000
    d = 86400
    evs = [
        {"ts": now - 10 * d, "event": "ask", "task_id": "old"},             # outside window
        {"ts": now - 2 * d, "event": "ask", "task_id": "a", "question": "q1"},
        {"ts": now - 1 * d, "event": "ask", "task_id": "b"},
        {"ts": now - 12 * 3600, "event": "answered", "task_id": "b"},
        {"ts": now - 1 * d, "event": "capability_gap", "task_id": "c"},
    ]
    assert set(picks.open_asks(evs, now)) == {"a"}
    assert picks.open_asks(evs, now)["a"]["question"] == "q1"
    assert picks.asked_recently(evs, now) == {"a", "b", "c"}


def test_answer_for_description_edit_or_plain_comment():
    desc = "Dashboard is wrong.\n"
    ask = {"ts": 1_000_000, "desc_hash": picks.desc_hash(desc)}
    # an untouched ctrl-o save (edit-form rstrips) is not an answer
    assert picks.answer_for(ask, {"description": "Dashboard is wrong."}, []) is None
    # a description edit is
    assert picks.answer_for(ask, {"description": "Dashboard is wrong. 17% is right."}, []) == \
        "Dashboard is wrong. 17% is right."
    # agent comments and comments from before the ask are not
    older = {"posted_at": "1970-01-12T13:46:39.000000Z", "content": "earlier note"}       # ts 999999
    agent = {"posted_at": "1970-01-12T13:46:41.000000Z", "content": "😈? which one?"}
    short = {"posted_at": "1970-01-12T13:46:42.000000Z", "content": "dtd-short:abc:x"}
    assert picks.answer_for(ask, {"description": desc}, [older, agent, short]) is None
    # a plain comment after the ask is; the newest wins
    c1 = {"posted_at": "1970-01-12T13:46:45.000000Z", "content": "17% is right"}
    c2 = {"posted_at": "1970-01-12T13:47:00.000000Z", "content": "actually 16.8%, see sheet"}
    assert picks.answer_for(ask, {"description": desc}, [c1, agent, c2]) == "actually 16.8%, see sheet"


def test_ask_stats_counts_conversion():
    evs = [
        {"ts": 10, "event": "ask", "task_id": "a"},
        {"ts": 11, "event": "ask", "task_id": "b"},
        {"ts": 12, "event": "answered", "task_id": "a"},
        {"ts": 13, "event": "pick", "task_id": "a"},
        {"ts": 14, "event": "accept", "task_id": "a"},
        {"ts": 15, "event": "pick", "task_id": "c"},         # never asked: not conversion
    ]
    assert picks.ask_stats(evs) == {"asks": 2, "answered": 1, "answered_picked": 1, "answered_accepted": 1}
