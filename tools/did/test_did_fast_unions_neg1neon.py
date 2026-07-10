"""Regression test: did-fast.py's --refresh-cache must union the -1neon block-ritual
cards into "today" via the DIRECT label endpoint.

Bug (2026-07-09): _refresh_task_queue_inner fetched only the 4 static label buckets
(0neon/1neon/夜neon/关键路径) directly; -1neon ritual cards reached the cache ONLY
through fetch_today's "today | overdue" FILTER query, whose index lags minutes
behind task creation. The daemon (re)creates the 5 ritual cards at each 2h block
boundary (~+30s); dtd.sh's watcher refreshes at ~+45s but the filter query hadn't
indexed them yet, so the new block's -1n cards didn't surface in dtd until the next
~3min refresh-cache.py daemon cycle ("dtd won't auto-refresh at block turnover").
refresh-cache.py already fixed this via DYNAMIC_TODAY_LABELS; did-fast must match.

The /tasks?label=-1neon endpoint is fresh within seconds, so the refresh now
fetch_label("-1neon") and unions (dedup by id, due<=today) into results["today"].
"""
from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "did-fast.py").read_text()


def test_refresh_unions_neg1neon_via_label_endpoint():
    inner = next(
        n for n in ast.walk(ast.parse(SRC))
        if isinstance(n, ast.FunctionDef) and n.name == "_refresh_task_queue_inner"
    )
    body = ast.get_source_segment(SRC, inner)
    # The direct label endpoint (fresh) must be used for -1neon, not just the
    # lagging today|overdue filter.
    assert 'fetch_label("-1neon")' in body, \
        "-1neon must be fetched via the direct label endpoint to beat filter-index lag"
    # And unioned into today (dedup by id), never added as a separate bucket key
    # (dtd reads ritual cards from `today`).
    assert 'results["today"].append' in body, \
        "-1neon label results must be unioned into results['today']"


def test_neg1neon_not_a_static_bucket():
    # -1neon should NOT be in the static label list (those become their own cache
    # keys, which dtd doesn't read); it belongs merged into `today`.
    inner = next(
        n for n in ast.walk(ast.parse(SRC))
        if isinstance(n, ast.FunctionDef) and n.name == "_refresh_task_queue_inner"
    )
    body = ast.get_source_segment(SRC, inner)
    labels_line = next(l for l in body.splitlines() if l.strip().startswith("labels = ["))
    assert "-1neon" not in labels_line, "-1neon must be unioned into today, not a static bucket key"


if __name__ == "__main__":
    import subprocess, sys
    sys.exit(subprocess.call(["python3", "-m", "pytest", __file__, "-v"]))
