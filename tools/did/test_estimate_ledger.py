"""Estimate ledger: a retrospective row per closed task (JM 2026-11-23)."""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _mod(name, fname=None):
    spec = importlib.util.spec_from_file_location(name, HERE / (fname or f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m          # did-fast's dataclasses resolve their module by name
    spec.loader.exec_module(m)
    return m


el = _mod("estimate_ledger")
DOMAINS = {"i447", "i9", "m5x2"}


def test_key_drops_annotations_code_and_tags_so_a_task_matches_its_timer():
    assert el.key("😈 Migrate 1Password vault (20) [40] @i447") == "migrate 1password vault"
    assert el.key("Review PR #12 (10) [15]") == "review pr"
    assert el.est_minutes("x [(link)](https://a.b/c) (70) [100]") == 70
    assert el.est_minutes("variable () []") is None
    assert el.est_points("mock WAU tree [30] @h335 [10] @i9") == 40


def test_jm_minutes_matches_by_name_and_counts_a_running_entry():
    entries = [
        {"description": "migrate 1password vault", "duration": 1200},
        {"description": "Migrate 1Password vault", "duration": -1,
         "start": "2026-11-23T20:00:00+00:00"},
        {"description": "something else", "duration": 9999},
    ]
    now = 1795464600.0   # 2026-11-23T20:10:00Z
    assert el.jm_minutes(entries, "Migrate 1Password vault (70) [100] @i447", now) == (30, 2)
    assert el.jm_minutes(entries, "no timer on this", now) == (0, 0)


def test_close_row_records_estimate_against_actual_for_minutes_and_fen(monkeypatch):
    monkeypatch.setattr(el, "_domains", lambda: DOMAINS)
    entries = [{"description": "migrate to 1password", "duration": 31 * 60}]
    row = el.close_row({"id": 7, "content": "migrate to 1password (70) [110]",
                        "labels": ["grp", "i447"], "points": 100}, entries, now=1795500000.0)
    assert row["kind"] == "close" and row["id"] == "7"
    assert (row["est_min"], row["jm_min"]) == (70, 31)
    assert (row["est_fen"], row["fen"]) == (110, 100)
    assert row["domain"] == "i447" and row["grp"] is True


def test_domain_falls_back_to_the_code_in_the_name_when_labels_are_missing():
    assert el.domain_of([], "fix the thing (5) [5] @i9", DOMAINS) == "i9"
    assert el.domain_of(["grp", "m5x2"], "x", DOMAINS) == "m5x2"
    assert el.domain_of([], "no code here", DOMAINS) is None


def test_fin_and_close_rows_merge_into_one_record(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "_domains", lambda: DOMAINS)
    entries = [{"description": "ship it", "duration": 600}]
    assert el.record_closes([{"id": "9", "content": "ship it (20) [30] @i9",
                              "labels": ["i9"], "points": 30}], tmp_path, entries) == 1
    assert el.record_fin("9", "ship it (20) [30] @i9",
                         {"wall_minutes": 45, "jm": {"minutes": 12},
                          "model": {"active_minutes": 40, "turns": 6}}, tmp_path)
    rows = el.load(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["jm_min"] == 12 and r["wall_min"] == 45 and r["turns"] == 6
    assert r["fen"] == 30 and r["domain"] == "i9"


def test_record_fin_skips_an_errored_report_and_never_raises(tmp_path):
    assert el.record_fin("1", "x (5) [5]", {"error": "boom"}, tmp_path) is False
    assert el.record_fin("1", "x (5) [5]", None, tmp_path) is False
    assert el.load(tmp_path) == []


def test_suggest_reports_ratio_untimed_count_and_similar_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(el, "_domains", lambda: DOMAINS)
    for i, (name, est, jm) in enumerate([("install password manager", 60, 30),
                                         ("install printer driver", 20, 30),
                                         ("agent did all of this", 30, 0)]):
        el.append({"kind": "close", "ts": 100 + i, "date": "2026-11-23", "id": str(i),
                   "task": name, "domain": "i447", "est_min": est, "jm_min": jm}, tmp_path)
    out = el.suggest("install 1password manager", "i447", tmp_path)
    assert out["all"] == {"n": 3, "n_timed": 2, "n_zero": 1,
                          "median_actual_over_estimate": 1.0}
    assert out["domain"]["code"] == "i447" and out["domain"]["n"] == 3
    assert out["similar"][0]["task"] == "install password manager"
    assert "agent did all of this" not in [s["task"] for s in out["similar"]]


def test_did_fast_payload_lists_only_tasks_that_really_closed():
    df = _mod("did_fast_for_ledger", "did-fast.py")

    class Item:
        def __init__(self, name): self.name = name

    class R:
        def __init__(self, name, task): self.item, self.todoist_task = Item(name), task

    fast = [R("a", {"id": "1", "content": "a (5) [10]", "labels": ["i9"]}),
            R("b", {"id": "2", "content": "b (5) [10]", "labels": []}),
            R("habit", None)]
    closes = df._ledger_closes(fast, {"1": (True, None), "2": (False, "err")}, {"a": 10})
    assert closes == [{"id": "1", "content": "a (5) [10]", "labels": ["i9"], "points": 10}]


def test_did_fast_spawn_is_detached_and_swallows_failure():
    df = _mod("did_fast_for_ledger2", "did-fast.py")
    calls = []
    assert df._spawn_estimate_ledger([{"id": "1"}], popen=lambda *a, **k: calls.append((a, k)))
    argv, kw = calls[0][0][0], calls[0][1]
    assert argv[-2] == "record" and json.loads(argv[-1]) == [{"id": "1"}]
    assert kw["start_new_session"] is True
    assert df._spawn_estimate_ledger([], popen=lambda *a, **k: calls.append(1)) is False

    def boom(*a, **k):
        raise OSError("no fork")
    assert df._spawn_estimate_ledger([{"id": "1"}], popen=boom) is False


def test_done_session_ledger_failure_cannot_touch_the_close():
    ds = _mod("done_session")

    def broken(_name):
        raise RuntimeError("ledger module missing")
    assert ds.ledger_safe("1", "x (5) [5]", {"wall_minutes": 3}, load=broken) is False
