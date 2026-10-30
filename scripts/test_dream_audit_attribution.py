#!/usr/bin/env python3
"""Regression (v170 5 + v171 16 false "DREAM CREATED" banners, 2026-10-27/28).

Every flagged "added" event was client Python-urllib: the -1neon emitter, JM's
/defer (a "deferred: …" record plus the one-off copy) and 0g/-1g block goals.
Dream writes through todoist-mcp. Fixtures below are the live v171 events.
"""
import importlib.util
import json
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "audit_attr", Path(__file__).with_name("dream-audit-todoist.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

U = "Python-urllib/3.9"


def ev(oid, ts, content, client=U):
    x = {"content": content}
    if client is not None:
        x["client"] = client
    return {"object_id": oid, "event_date": ts, "extra_data": x}


V171_ADDED = [
    ev("6hrPv2MqP3h9qFCP", "2026-10-28T13:49:46.719084Z",
       "deferred: 大孩子文学时间 (30) [50] → 2026-10-31 (recurring; next 2026-10-29) [0]"),
    ev("6hrPv2Px788Hh9fP", "2026-10-28T13:49:46.339237Z", "大孩子文学时间 10.28 (30) [50]"),
    ev("6hrPv24Fp9wq4HXw", "2026-10-28T13:49:42.413114Z",
       "deferred: xk26 (10) [10] → 2026-10-31 (recurring; next 2026-10-29) [0]"),
    ev("6hrPv24gcRxgPFXP", "2026-10-28T13:49:42.046651Z", "xk26 10.28 (10) [10]"),
    ev("6hrPqPjFhHGxfVm2", "2026-10-28T13:40:26.569474Z", "250分 「10」 {10}"),
    ev("6hrPcm8fqjQwfHp6", "2026-10-28T13:00:22.064807Z", "😈 -1l"),
    ev("6hrPcjvrGwrPx2J6", "2026-10-28T13:00:19.725900Z", "😈 s سمش"),
    ev("6hrPXgMpc7MWmpPR", "2026-10-28T12:53:51.289196Z", "1n {7}"),
]


def run(added, since="2026-10-28T10:00:00", extra_args=(), run_dir=None):
    events = {"added": added, "updated": [], "deleted": [], "completed": []}
    audit.fetch_events = lambda etype, s: [e for e in events[etype] if e["event_date"] >= s]
    d = run_dir or Path(__import__("tempfile").mkdtemp())
    sys.argv = ["x", "--run-dir", str(d), "--until", "2026-10-28T14:00:00", *extra_args]
    if since:
        sys.argv += ["--since", since]
    return audit.main(), d


def test_v171_all_non_dream_no_banner():
    rc, d = run(V171_ADDED)
    m = json.loads((d / "revert-mapping.json").read_text())
    assert rc == 0 and not (d / "morning-brief.md").exists()
    assert m["created"] == [] and len(m["emitter_added"]) == len(V171_ADDED)


def test_mcp_creation_flagged_even_with_emitter_like_content():
    added = V171_ADDED + [ev("d1", "2026-10-28T13:30:00Z", "😈 dream-made {5}", "todoist-mcp/13.6.0")]
    rc, d = run(added)
    brief = (d / "morning-brief.md").read_text()
    assert rc == 2 and "`d1`" in brief and "6hrPv2Px788Hh9fP" not in brief


def test_other_non_mcp_client_listed_not_bannered():
    rc, d = run([ev("j1", "2026-10-28T13:10:00Z", "buy milk", "todoist-ios/1.0")])
    m = json.loads((d / "revert-mapping.json").read_text())
    assert rc == 0 and m["other_added"][0]["id"] == "j1" and m["created"] == []


def test_defer_copy_needs_a_deferred_sibling():
    # Same shape as a /defer copy but no "deferred:" record nearby -> "other", not emitter.
    split = audit.classify_added([ev("c1", "2026-10-28T13:00:00Z", "xk26 10.28 (10) [10]")])
    assert [r["id"] for r in split["other"]] == ["c1"] and not split["emitter"]


def test_no_client_falls_back_to_content():
    split = audit.classify_added([ev("n1", "2026-10-28T13:00:00Z", "😈 -1t", client=None),
                                  ev("n2", "2026-10-28T13:00:00Z", "draft LP memo", client=None)])
    assert [r["id"] for r in split["emitter"]] == ["n1"]
    assert [r["id"] for r in split["dream"]] == ["n2"]


def test_label_based_emitter():
    e = ev("l1", "2026-10-28T13:00:00Z", "write tests", client=None)
    e["extra_data"]["labels"] = ["0g"]
    assert audit.classify_added([e])["emitter"]


def test_window_defaults_to_claude_start_in_log(tmp_path):
    from datetime import datetime, timezone
    # Intake started 03:00 local, claude at 03:20 local; a task added at 03:10 local is outside.
    start_local = datetime(2026, 10, 28, 3, 20, 0)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "agent-run.log").write_text(
        "[2026-10-28 03:00:00] Running dream-intake.py...\n"
        f"[{start_local:%Y-%m-%d %H:%M:%S}] Starting claude (full-run)\n"
        "[2026-10-28 05:00:00] Starting claude (pass3-retry)\n")
    since = audit.claude_start_from_log(tmp_path)
    expect = start_local.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    assert since == expect
    before = datetime(2026, 10, 28, 3, 10).astimezone().astimezone(timezone.utc)
    after = datetime(2026, 10, 28, 3, 30).astimezone().astimezone(timezone.utc)
    added = [ev("pre", before.strftime("%Y-%m-%dT%H:%M:%SZ"), "intake-era", "todoist-mcp/1"),
             ev("in", after.strftime("%Y-%m-%dT%H:%M:%SZ"), "real", "todoist-mcp/1")]
    rc, d = run(added, since=None, run_dir=tmp_path)
    m = json.loads((d / "revert-mapping.json").read_text())
    assert [c["id"] for c in m["created"]] == ["in"] and m["window"]["since"] == expect


def test_missing_since_and_log_errors(tmp_path):
    rc, _ = run([], since=None, run_dir=tmp_path)
    assert rc == 1
