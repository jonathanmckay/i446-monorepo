"""agent_triage: verdict parsing must fail closed (keep), and only ibx i9 /
ibx m5x2 timer starts may spawn a triage."""
import importlib.util
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import agent_triage as at

_spec = importlib.util.spec_from_file_location(
    "toggl_cli", Path.home() / "i446-monorepo/mcp/toggl_server/toggl_cli.py")
toggl_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(toggl_cli)

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """No test may touch the real keep cache or cost ledger."""
    monkeypatch.setattr(at, "KEEP_FILE", tmp_path / "kept.json")
    monkeypatch.setattr(at, "COST_FILE", tmp_path / "cost.jsonl")
    at._usage.clear()


def test_parse_skips_only():
    out = 'noise\n[{"i":1,"v":"keep","why":"ask"},{"i":2,"v":"skip","why":"digest"}]\n'
    assert at.parse_verdicts(out, 2) == {2: "digest"}


def test_parse_garbage_returns_none():
    assert at.parse_verdicts("", 3) is None
    assert at.parse_verdicts("I can't do that", 3) is None
    assert at.parse_verdicts('[{"i":1,"v":"skip"', 3) is None


def test_parse_ignores_out_of_range_and_bad_rows():
    out = '[{"i":0,"v":"skip"},{"i":9,"v":"skip"},{"i":"x","v":"skip"},{"i":2,"v":"SKIP"}]'
    assert at.parse_verdicts(out, 3) == {2: ""}


def test_parse_last_array_wins():
    out = 'format: [{"i": 1, "v": "skip"}]\nanswer: [{"i":1,"v":"keep"}]'
    assert at.parse_verdicts(out, 1) == {}


def test_trigger_matches_ibx_timers_only():
    with mock.patch("subprocess.Popen") as popen:
        for desc in ("ibx i9", "ibx - i9", "IBX M5X2", "ibx  -  m5x2"):
            toggl_cli._maybe_agent_triage(desc)
        assert [c.args[0][2] for c in popen.call_args_list] == ["i9", "i9", "m5x2", "m5x2"]
        popen.reset_mock()
        for desc in ("ibx s897", "ibx i9 review", "teams", "ibx"):
            toggl_cli._maybe_agent_triage(desc)
        popen.assert_not_called()


def _emails(n):
    return [{"id": f"m{i}", "from": "a@b.c", "subject": f"s{i}", "body": ""} for i in range(1, n + 1)]


def test_run_batches_and_maps_indices_within_each_batch(tmp_path, monkeypatch):
    """2026-11-15: one 100-email prompt filed 15. Mail goes BATCH at a time,
    and index i in a batch's answer means that batch's i-th email."""
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    calls, filed = [], []

    def ask(prompt):
        calls.append(prompt)
        return '[{"i": 1, "v": "skip", "why": "newsletter"}, {"i": 2, "v": "keep", "why": "tenant"}]'

    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(45), ask, lambda e: filed.append(e["id"])))
    assert at.run("m5x2") == (45, 3)
    assert len(calls) == 3                       # 20 + 20 + 5
    assert filed == ["m1", "m21", "m41"]         # first email of each batch
    assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 3


def test_run_keeps_a_failed_batch_and_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    filed, n = [], {"calls": 0}

    def ask(prompt):
        n["calls"] += 1
        if n["calls"] == 1:
            raise TimeoutError("agent timed out")
        if n["calls"] == 2:
            return "no json here"
        return '[{"i": 5, "v": "skip", "why": "promo"}]'

    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(45), ask, lambda e: filed.append(e["id"])))
    assert at.run("m5x2") == (45, 1)
    assert filed == ["m45"]


def test_dry_run_files_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    filed = []
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3),
                                             lambda p: '[{"i": 1, "v": "skip", "why": "x"}]',
                                             lambda e: filed.append(e["id"])))
    assert at.run("m5x2", dry_run=True) == (3, 0)
    assert filed == [] and not (tmp_path / "audit.jsonl").exists()


def test_property_meld_is_filed_by_rule_without_the_agent(tmp_path, monkeypatch):
    """JM 2026-11-15: file all Property Meld. The sender rule runs before the
    agent, so the m5x2 "maintenance is keep" criterion cannot hold them."""
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    emails = _emails(3)
    emails[1]["from"] = "Property Meld <abc-123@msg.propertymeld.com>"
    emails[1]["subject"] = "EMERGENCY Meld at 1905 W 2nd Ave, Unit E: (Merged) Ecall no heat"
    seen, filed = [], []

    def ask(prompt):
        seen.append(prompt)
        return '[{"i": 1, "v": "keep", "why": "tenant"}, {"i": 2, "v": "keep", "why": "tenant"}]'

    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: emails, ask, lambda e: filed.append(e["id"])))
    assert at.run("m5x2") == (3, 1)
    assert filed == ["m2"] and "Ecall no heat" not in seen[0]
    assert at.rule_skip("i9", emails[1]) is None     # rule is m5x2-only


def test_a_restored_message_is_never_filed_again(tmp_path, monkeypatch):
    """2026-11-15: a recruiter reply JM's side restored to the inbox was filed
    again on the next run. An id already in the audit file that is back in the
    inbox was put there by a person: keep it, and don't show it to the agent."""
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"source": "m5x2", "id": "m2", "subject": "s2"}\n{"source": "i9", "id": "m3"}\n')
    monkeypatch.setattr(at, "AUDIT_FILE", audit)
    seen, filed = [], []

    def ask(prompt):
        seen.append(prompt)
        return '[{"i": 1, "v": "skip", "why": "x"}, {"i": 2, "v": "skip", "why": "x"}]'

    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3), ask, lambda e: filed.append(e["id"])))
    assert at.run("m5x2") == (3, 2)
    assert filed == ["m1", "m3"]                 # m2 restored; m3's audit row is another source
    assert "Subject: s2" not in seen[0]


# ── Keep cache (2026-11-24): a kept email is not sent to the agent again ────

def _keep_all(calls):
    def ask(prompt):
        calls.append(prompt)
        n = prompt.count("--- [")
        return json.dumps([{"i": i, "v": "keep", "why": "tenant"} for i in range(1, n + 1)])
    return ask


def test_kept_mail_is_not_reclassified_on_the_next_run(tmp_path, monkeypatch):
    """2026-11-24: a run sent 69 emails to claude to file 9; the 60 kept ones
    were paid for again on every run until JM cleared them."""
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    calls = []
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3), _keep_all(calls), lambda e: None))
    assert at.run("m5x2") == (3, 0)
    assert len(calls) == 1
    assert at.run("m5x2") == (3, 0)
    assert len(calls) == 1                      # second run: nothing new, no agent call


def test_only_new_mail_goes_to_the_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    calls, n = [], [3]
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(n[0]), _keep_all(calls), lambda e: None))
    at.run("m5x2")
    n[0] = 5
    at.run("m5x2")
    assert calls[1].count("--- [") == 2 and "Subject: s4" in calls[1] and "Subject: s1" not in calls[1]


def test_changed_criteria_invalidate_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    calls = []
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3), _keep_all(calls), lambda e: None))
    at.run("m5x2")
    monkeypatch.setitem(at.EXTRA, "m5x2", at.EXTRA["m5x2"] + " Newsletters from lenders are keep.")
    at.run("m5x2")
    assert len(calls) == 2


def test_recheck_ignores_the_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    calls = []
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3), _keep_all(calls), lambda e: None))
    at.run("m5x2")
    at.run("m5x2", recheck=True)
    assert len(calls) == 2


def test_failed_batch_and_missing_verdicts_are_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    answers = ["garbage", '[{"i": 1, "v": "keep", "why": "tenant"}]']
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return answers[len(calls) - 1] if len(calls) <= 2 else "[]"

    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3), ask, lambda e: None))
    at.run("m5x2")                               # garbage: nothing cached
    at.run("m5x2")                               # verdict for m1 only
    at.run("m5x2")
    assert calls[1].count("--- [") == 3
    assert calls[2].count("--- [") == 2 and "Subject: s1" not in calls[2]


def test_dry_run_does_not_write_the_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(3), _keep_all(calls), lambda e: None))
    at.run("m5x2", dry_run=True)
    assert not at.KEEP_FILE.exists()


def test_old_cache_entries_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")
    at.KEEP_FILE.write_text(json.dumps({"m5x2": {"key": at.criteria_key("m5x2"), "ids": {
        "old": {"why": "x", "ts": "2020-01-01T00:00:00"}}}}))
    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(1), _keep_all([]), lambda e: None))
    at.run("m5x2")
    ids = json.loads(at.KEEP_FILE.read_text())["m5x2"]["ids"]
    assert "old" not in ids and "m1" in ids


# ── Cost ledger ─────────────────────────────────────────────────────────────

def test_ask_claude_returns_the_text_and_notes_the_cost(monkeypatch):
    payload = {"result": '[{"i": 1, "v": "keep", "why": "t"}]', "total_cost_usd": 0.0604,
               "usage": {"input_tokens": 2, "cache_creation_input_tokens": 100,
                         "cache_read_input_tokens": 50, "output_tokens": 23}}
    monkeypatch.setattr(at.subprocess, "run",
                        lambda *a, **k: mock.Mock(stdout=json.dumps(payload)))
    assert at.ask_claude("p") == payload["result"]
    assert at._usage == [{"cost_usd": 0.0604, "in": 152, "out": 23}]


def test_ask_claude_falls_back_to_raw_text(monkeypatch):
    monkeypatch.setattr(at.subprocess, "run", lambda *a, **k: mock.Mock(stdout="not json"))
    assert at.ask_claude("p") == "not json" and at._usage == []


def test_run_writes_one_cost_row_per_agent_call(tmp_path, monkeypatch):
    monkeypatch.setattr(at, "AUDIT_FILE", tmp_path / "audit.jsonl")

    def ask(prompt):
        at._usage.append({"cost_usd": 0.05, "in": 1000, "out": 40})
        return _keep_all([])(prompt)

    monkeypatch.setitem(at.SOURCES, "m5x2", (lambda: _emails(25), ask, lambda e: None))
    at.run("m5x2")
    rows = [json.loads(l) for l in at.COST_FILE.read_text().splitlines()]
    assert [(r["source"], r["emails"], r["cost_usd"]) for r in rows] == [("m5x2", 20, 0.05), ("m5x2", 5, 0.05)]
    at.run("m5x2")                               # all cached: no call, no row
    assert len(at.COST_FILE.read_text().splitlines()) == 2
