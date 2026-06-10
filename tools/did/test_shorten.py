"""Unit tests for shorten.split_estimates — the pure parsing that keeps (N)/[N]
estimates out of the prose so they can be re-appended after Haiku shortening."""
import importlib

shorten = importlib.import_module("shorten")


def test_trailing_time_and_points():
    prose, est = shorten.split_estimates("LP scorecard: define 5 maintenance metrics (15) [30]")
    assert prose == "LP scorecard: define 5 maintenance metrics"
    assert est == "(15) [30]"


def test_no_estimates():
    prose, est = shorten.split_estimates("Reconnect QBO OAuth — unblocks m5x2 debt schedules")
    assert est == ""
    assert prose.startswith("Reconnect QBO OAuth")


def test_nested_and_brace():
    prose, est = shorten.split_estimates("150 pts ((10)) {15}")
    assert prose == "150 pts"
    assert est == "((10)) {15}"


def test_points_then_time_order_preserved():
    prose, est = shorten.split_estimates("Fill JM m5x2 roles & expectations doc [20] (20)")
    assert prose == "Fill JM m5x2 roles & expectations doc"
    assert est == "[20] (20)"


def test_g_bonus_token():
    prose, est = shorten.split_estimates("Ship the thing [0G]")
    assert prose == "Ship the thing"
    assert est == "[0G]"


def test_path_paren_not_treated_as_estimate():
    # Parenthetical paths contain non-digits and must NOT be stripped as estimates.
    prose, est = shorten.split_estimates("Hand Eldon the VP roadshow list (h335/i9/xbox)")
    assert est == ""
    assert "h335/i9/xbox" in prose


def test_short_task_below_cap_returns_none_short():
    # shorten_tasks should skip tasks whose prose is already within the cap.
    out = shorten.shorten_tasks([{"id": "x1", "content": "0t (5) [10]"}], max_new=0)
    assert "x1" not in out


def test_comment_cache_does_not_double_estimates(monkeypatch, tmp_path):
    # Regression: a cached short name (from the Todoist comment / sidecar) already
    # includes the estimates; resolve() must NOT re-append them.  Bug produced
    # "First pass 90 days doc {30} {30}".
    monkeypatch.setattr(shorten, "SIDECAR", tmp_path / "sc.json")
    content = "First pass at a first 90 days doc {30}"
    stored = "First pass 90 days doc {30}"  # full display, estimates included
    # Simulate the durable comment returning the full display.
    monkeypatch.setattr(shorten, "_comment_lookup", lambda tid, h: stored)
    monkeypatch.setattr(shorten, "_haiku_shorten", lambda prose: "SHOULD-NOT-CALL")
    out = shorten.shorten_tasks([{"id": "t1", "content": content}])
    assert out["t1"] == stored          # used verbatim
    assert out["t1"].count("{30}") == 1  # not doubled


def test_no_prune_preserves_other_callers_entries(monkeypatch, tmp_path):
    # Regression: pruning ids absent from `tasks` let did-fast and refresh-cache
    # (different task sets) delete each other's sidecar entries. A task missing
    # from this call's list must keep its sidecar entry.
    sc = tmp_path / "sc.json"
    monkeypatch.setattr(shorten, "SIDECAR", sc)
    sc.write_text('{"other": {"h": "deadbeef", "short": "kept"}}')
    shorten.shorten_tasks([{"id": "t1", "content": "0t (5) [10]"}])  # short task, no-op
    import json
    assert "other" in json.loads(sc.read_text())


def test_all_cache_writers_attach_short():
    # Regression: short names "all disappeared" because a cache-rebuild path
    # (did-fast --refresh-cache) wrote the cache without re-attaching short.
    # EVERY full-rebuild writer must call shorten.attach_to_cache so a refresh
    # never drops them.
    from pathlib import Path
    here = Path(__file__).resolve().parent
    for fname in ("did-fast.py", "refresh-cache.py"):
        src = (here / fname).read_text()
        assert "attach_to_cache" in src, f"{fname} must call shorten.attach_to_cache"


def test_drop_from_queue_preserves_short_on_other_tasks():
    # Regression: completing a task must not wipe the short names of the tasks
    # that remain in the cache.
    import importlib.util, json, tempfile, os
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "run_mod", Path(__file__).resolve().parent / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)

    tmp = tempfile.mkdtemp()
    cache_path = Path(tmp) / "task-queue.json"
    cache_path.write_text(json.dumps({
        "0neon": [
            {"id": "done1", "content": "finish the thing (10) [20]", "short": "finish thing (10) [20]"},
            {"id": "keep1", "content": "a very long task that got shortened (30) [60]",
             "short": "very long task (30) [60]"},
        ]
    }))
    run.TASK_QUEUE = cache_path
    run._drop_from_queue("done1")
    after = json.loads(cache_path.read_text())
    remaining = after["0neon"]
    assert [t["id"] for t in remaining] == ["keep1"], "completed task should be removed"
    assert remaining[0].get("short") == "very long task (30) [60]", "short must be preserved"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS: {fn.__name__}")
    print(f"{len(fns)} passed")
