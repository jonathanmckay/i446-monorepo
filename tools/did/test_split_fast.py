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


def test_safe_name_strips_fullwidth_separators_did_fast_splits_on():
    # did-fast splits items on ，； since 2026-10-22; a CJK name holding one
    # would scatter the split's [N] across two /did items.
    p = sf.plan("回复王，李；张 (10) [10]", pts=5, today=T)
    assert not set("，；,;") & set(p["safe_name"]), p["safe_name"]
    dtd_sh = (Path(__file__).parent / "dtd.sh").read_text()
    assert "safe_name = re.sub(r'[,;，；]+'" in dtd_sh


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


def test_run_recurring_task_keeps_cadence(tmp_path):
    """v169 review: a plain due_date on a repeating task strips its recurrence;
    split renames the occurrence but never writes due_date on it."""
    task = {"id": "R1", "content": "cpap clean (30) [20]", "labels": [], "project_id": "P",
            "due": {"date": T, "is_recurring": True, "string": "every day"}}
    api, calls = _fake(task)
    res = sf.run("R1", cache_file=tmp_path / "none.json", pts=10, api=api,
                 credit=lambda item: {"results": [{}]}, label_for=lambda l: "@hcb",
                 tie=lambda n, i: None)
    assert res["ok"]
    upd = [c for c in calls if c[0] == "POST" and c[1] == "/tasks/R1"]
    assert upd and all("due_date" not in (c[2] or {}) for c in upd)
