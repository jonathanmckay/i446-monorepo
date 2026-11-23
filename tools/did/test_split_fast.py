"""split-fast.py (2026-10-22): the standalone port of dtd.sh's split.sh that
dtd.py's POST /api/split calls. Same arithmetic/tags as the heredoc; Todoist,
did-fast and completed-today are faked here (never the real ones)."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("split_fast", Path(__file__).parent / "split-fast.py")
sf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sf)

T = "2026-10-22"


def test_plan_pts_and_minutes():
    p = sf.plan("write memo (60) [40]", pts=15, minutes=20, today=T)
    assert p["posthoc_content"] == "write memo (20) [15]"
    assert p["new_content"] == "write memo (40) [25]"
    assert p["rem_date"] == T


def test_plan_no_minutes_keeps_duration_on_both_halves():
    p = sf.plan("write memo (60) [40]", pts=40, today=T)
    assert p["posthoc_content"] == "write memo (60) [40]"
    assert p["new_content"] == "write memo (60)"          # [0] is never written


def test_plan_bonus_only_task_and_done_name():
    p = sf.plan("😈 ship it {10}", bonus=4, done_name="drafted", today=T)
    assert p["posthoc_content"] == "drafted (4) {4}"
    assert p["new_content"] == "😈 ship it {6}"


def test_plan_rem_due_and_comma_safe_name():
    p = sf.plan("buy nightshade, 2 (10) [10]", pts=5, rem_due="2026-10-30", today=T)
    assert p["rem_date"] == "2026-10-30"
    assert p["safe_name"] == "buy nightshade  2"


def _fake(task):
    calls = []

    def api(method, path, body=None):
        calls.append((method, path, body))
        if method == "GET":
            return task
        if path == "/tasks":
            return {"id": "PH1"}
        return None
    return api, calls


def test_run_keep_renames_remainder_and_credits(tmp_path):
    task = {"id": "A1", "content": "write memo (60) [40]", "labels": ["m5x2"], "project_id": "P"}
    api, calls = _fake(task)
    credited, tied = [], []
    res = sf.run("A1", cache_file=tmp_path / "none.json", pts=15, minutes=20, api=api,
                 credit=lambda item: credited.append(item) or {"results": [{}]},
                 label_for=lambda labels: "@m5x2", tie=lambda n, i: tied.append((n, i)))
    assert res["ok"] and res["new_content"] == "write memo (40) [25]" and not res["closed"]
    assert ("POST", "/tasks/PH1/close", None) in calls
    assert ("POST", "/tasks/A1", {"content": "write memo (40) [25]", "due_date": res["rem_date"]}) in calls
    assert credited == ["write memo [15] @m5x2"]
    assert tied == [("write memo", "PH1")]
    assert res["message"].startswith("✂ +[15] today / [25]")


def test_run_no_keep_closes_task(tmp_path):
    task = {"id": "A1", "content": "write memo (60) [40]", "labels": [], "project_id": "P"}
    api, calls = _fake(task)
    res = sf.run("A1", cache_file=tmp_path / "none.json", pts=10, keep=False, api=api,
                 credit=lambda item: {"results": [{}]}, label_for=lambda l: "@g245",
                 tie=lambda n, i: None)
    assert res["closed"] and ("POST", "/tasks/A1/close", None) in calls
    assert not any(c[0] == "POST" and c[1] == "/tasks/A1" for c in calls)
    assert "task closed" in res["message"]


def test_run_unlogged_credit_is_flagged(tmp_path):
    task = {"id": "A1", "content": "x (10) [10]", "labels": [], "project_id": "P"}
    api, _ = _fake(task)
    res = sf.run("A1", cache_file=tmp_path / "none.json", pts=5, api=api,
                 credit=lambda item: None, label_for=lambda l: "@g245", tie=lambda n, i: None)
    assert res["ok"] and res["lost"] and res["message"].startswith("⚠ split: [5]")


def test_run_needs_points(tmp_path):
    res = sf.run("A1", cache_file=tmp_path / "none.json", api=lambda *a, **k: 1 / 0)
    assert not res["ok"]


def test_run_names_remainder_from_live_task_not_stale_cache(tmp_path):
    # 2026-11-05: a rename followed by a split reverted to the cached title.
    cache = tmp_path / "c.json"
    cache.write_text('{"today": [{"id": "A1", "content": "old name (60) [40]"}]}')
    api, _calls = _fake({"id": "A1", "content": "new name (60) [40]", "labels": [], "project_id": "P"})
    res = sf.run("A1", cache_file=cache, pts=15, api=api,
                 credit=lambda item: {"results": [{}]},
                 label_for=lambda labels: "", tie=lambda n, i: None)
    assert res["new_content"] == "new name (60) [25]"


def test_run_falls_back_to_cache_when_live_title_is_blank(tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text('{"today": [{"id": "A1", "content": "cached (60) [40]"}]}')
    api, _calls = _fake({"id": "A1", "content": "", "labels": [], "project_id": "P"})
    res = sf.run("A1", cache_file=cache, pts=15, api=api,
                 credit=lambda item: {"results": [{}]},
                 label_for=lambda labels: "", tie=lambda n, i: None)
    assert res["new_content"] == "cached (60) [25]"


# ── the split's 分 land on the time entry too (2026-11-23) ───────────────────

def _run_with_part(tmp_path, credit, **kw):
    task = {"id": "A1", "content": "write memo (60) [40] {10}", "labels": ["m5x2"], "project_id": "P"}
    api, _ = _fake(task)
    parts = []

    def part(names, pts, key, task_id):
        parts.append((names, pts, key, task_id))
        return {"day": T, "id": "777", "pts": pts, "key": key}
    res = sf.run("A1", cache_file=tmp_path / "none.json", api=api, credit=credit,
                 label_for=lambda labels: "@m5x2", tie=lambda n, i: None, part=part, **kw)
    return res, parts


def test_run_puts_the_credited_points_on_the_time_entry(tmp_path):
    res, parts = _run_with_part(tmp_path, lambda item: {"results": [{}]}, pts=15, bonus=4,
                                done_name="drafted")
    names, pts, key, task_id = parts[0]
    assert (pts, key, task_id) == (19, "PH1", "A1")          # [15] + {4}, keyed by the done record
    assert "write memo" in names and "drafted" in names
    assert res["entry_part"] == {"day": T, "id": "777", "pts": 19, "key": "PH1"}


def test_run_puts_nothing_on_the_entry_when_the_credit_was_lost(tmp_path):
    res, parts = _run_with_part(tmp_path, lambda item: {"results": []}, pts=15)
    assert parts == [] and res["entry_part"] is None


def test_run_journals_the_entry_part_for_undo(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["record"] = kw.get("input")
        return None
    monkeypatch.setattr(sf.subprocess, "run", fake_run)
    _run_with_part(tmp_path, lambda item: {"results": [{}]}, pts=15, journal="/tmp/j")
    import json
    assert json.loads(seen["record"])["entry_part"]["id"] == "777"


def test_dtd_sh_split_writer_credits_the_entry_part_too():
    # dtd.sh's heredoc is a copy of run(): keep the two in step.
    body = (Path(__file__).parent / "dtd.sh").read_text().split("<< 'SPLITEOF'", 1)[1].split("\nSPLITEOF", 1)[0]
    assert "lib/entry_points.py', 'credit-part'" in body
    assert "'entry_part': entry_part," in body
    assert body.index("credit-part") < body.index("# 4. Journal for ctrl-z undo")


# ── task groups: a split's points come off the parent (2026-11-23) ─────────

def test_split_of_a_group_input_lowers_the_parent_by_points_only(tmp_path):
    task = {"id": "C1", "content": "draft reply (20) [40] {10}", "labels": ["m5x2", "agent"],
            "project_id": "P", "parent_id": "PAR"}
    api, calls = _fake(task)
    paid = []
    res = sf.run("C1", cache_file=tmp_path / "none.json", pts=15, bonus=4, api=api,
                 credit=lambda item: {"results": [{}]}, label_for=lambda l: "@m5x2",
                 tie=lambda n, i: None, part=lambda *a: None,
                 pay=lambda c, p, n, day: paid.append((c, p, n, day)) or
                 {"status": "ok", "key": f"{c}:{day}", "parent": p, "taken": [[n, None]]})
    assert res["ok"]
    assert len(paid) == 1
    child, parent, n, day = paid[0]
    assert (child, parent, n) == ("C1", "PAR", 15)          # the {4} bonus is not taken
    assert day.endswith(":split:PH1")                       # keyed by the done record
    posthoc_body = next(b for m, p, b in calls if m == "POST" and p == "/tasks")
    assert posthoc_body["labels"] == ["m5x2", "posthoc"]    # no agent/grp on the record


def test_split_takes_nothing_when_the_credit_was_lost_or_task_is_top_level(tmp_path):
    def boom(*a):
        raise AssertionError("parent must not be touched")
    child = {"id": "C1", "content": "x (10) [10]", "labels": [], "project_id": "P", "parent_id": "PAR"}
    api, _ = _fake(child)
    sf.run("C1", cache_file=tmp_path / "none.json", pts=5, api=api, credit=lambda item: None,
           label_for=lambda l: "@g245", tie=lambda n, i: None, part=lambda *a: None, pay=boom)
    top = {"id": "T1", "content": "x (10) [10]", "labels": [], "project_id": "P"}
    api, _ = _fake(top)
    sf.run("T1", cache_file=tmp_path / "none.json", pts=5, api=api,
           credit=lambda item: {"results": [{}]}, label_for=lambda l: "@g245",
           tie=lambda n, i: None, part=lambda *a: None, pay=boom)
