"""Regression tests for 0t-fast.py."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

# Load the hyphenated module
_PATH = Path(__file__).parent / "0t-fast.py"
_SPEC = importlib.util.spec_from_file_location("zerot_fast", _PATH)
zerot_fast = importlib.util.module_from_spec(_SPEC)
sys.modules["zerot_fast"] = zerot_fast
_SPEC.loader.exec_module(zerot_fast)


def test_tag_columns_match_live_headers():
    """0n headers are AU='1+', AV='-1', AW='-2', AX='-3', AS='其他人'. The -1 tag
    must write to AV and -2 to AW. Regression: a '1+' column was inserted at AU,
    so an old map of -1→AU duplicated -1 points into the 1+ column (AU + AV)."""
    assert zerot_fast.TAG_COLUMNS["-1"] == "AV"
    assert zerot_fast.TAG_COLUMNS["-2"] == "AW"
    assert zerot_fast.TAG_COLUMNS["-3"] == "AX"
    assert zerot_fast.TAG_COLUMNS["其他人"] == "AS"
    assert zerot_fast.TAG_COLUMNS["xk87"] == "AZ"


def test_tag_columns_agree_with_daemon():
    """0t-fast and build-order-daemon both write 0n tag columns; they must not
    drift (the original bug was 0t-fast lagging the daemon after a column shift)."""
    daemon_path = Path(__file__).resolve().parents[2] / "scripts" / "build-order-daemon.py"
    spec = importlib.util.spec_from_file_location("bod_daemon", daemon_path)
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
    shared = set(zerot_fast.TAG_COLUMNS) & set(daemon.TOGGL_TAG_COLS)
    assert shared, "expected overlapping tag keys between the two maps"
    for tag in shared:
        assert zerot_fast.TAG_COLUMNS[tag] == daemon.TOGGL_TAG_COLS[tag], (
            f"{tag}: 0t-fast={zerot_fast.TAG_COLUMNS[tag]} "
            f"daemon={daemon.TOGGL_TAG_COLS[tag]}")


def test_compute_tag_minutes_excludes_sleep_from_minus3():
    """睡觉 (sleep) carries the -3 tag but is tracked in column D. It must NOT be
    summed into the -3/AX tag column, or AX reads as a full night of sleep
    (regression 2026-06-28: AX=439)."""
    sleep = {"duration": 400 * 60, "tags": ["-3"], "project_id": zerot_fast.SLEEP_PROJECT_ID}
    media = {"duration": 30 * 60, "tags": ["-3"], "project_id": 109932707}  # 新闻/hcmc
    with patch.object(zerot_fast, "get_toggl_entries", return_value=[sleep, media]):
        tag_totals, _ = zerot_fast.compute_tag_minutes(date(2026, 6, 27), date(2026, 6, 28))
    assert tag_totals.get("-3") == 30, f"AX must exclude sleep; got {tag_totals.get('-3')}"


def test_daemon_compute_toggl_totals_excludes_sleep_from_AX():
    """The daemon writes today's AX; it must also exclude sleep from -3."""
    daemon_path = Path(__file__).resolve().parents[2] / "scripts" / "build-order-daemon.py"
    spec = importlib.util.spec_from_file_location("bod_daemon2", daemon_path)
    daemon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daemon)
    sleep = {"duration": 400 * 60, "tags": ["-3"], "project_id": daemon.SLEEP_PROJECT_ID}
    media = {"duration": 25 * 60, "tags": ["-3"], "project_id": 109932707}
    with patch.object(daemon, "_toggl_get", return_value=[sleep, media]):
        totals = daemon.compute_toggl_totals(date(2026, 6, 28))
    assert totals.get("AX") == 25, f"daemon AX must exclude sleep; got {totals.get('AX')}"


def test_mark_night_hcmc_targets_yesterday_row():
    """night hcmc minutes must be logged to the date the entry occurred (yesterday),
    not today. Otherwise sleep-bridging hcmc points land one row too late."""
    captured = {}

    class _FakeProc:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    def _fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _FakeProc()

    with patch.object(zerot_fast.subprocess, "run", side_effect=_fake_run):
        zerot_fast.mark_night_hcmc(35, date(2026, 5, 6))

    arg = captured["cmd"][-1]
    # The arg passed to did-fast must include the M/D of the entry's date,
    # so did-fast routes the write to that row instead of today's.
    assert arg == "night hcmc 35 5/6", (
        f"expected explicit M/D for yesterday's row, got {arg!r}"
    )


def test_tag_and_project_minutes_only_count_target_day():
    """All tag and project minute totals must only sum the target day (yesterday),
    not both days. Summing today's entries inflates yesterday's row."""
    yesterday = date(2026, 5, 16)
    today = date(2026, 5, 17)

    yesterday_entries = [
        {"duration": 18000, "tags": ["-3", "xk87"], "project_id": 163129781},  # 300min
    ]
    today_entries = [
        {"duration": 24000, "tags": ["-3", "xk87"], "project_id": 163129781},  # 400min
    ]

    def fake_entries(d):
        if d == yesterday:
            return yesterday_entries
        return today_entries

    with patch.object(zerot_fast, "get_toggl_entries", side_effect=fake_entries):
        tag_totals, proj_totals = zerot_fast.compute_tag_minutes(yesterday, today)

    # Tags must only count yesterday
    assert tag_totals["-3"] == 300, f"-3 tag should be 300 (yesterday only), got {tag_totals['-3']}"
    assert tag_totals["xk87"] == 300, f"xk87 tag should be 300 (yesterday only), got {tag_totals['xk87']}"
    # Projects should be empty (xk87 matched by tag, not project_id)
    assert not proj_totals, f"proj_totals should be empty, got {proj_totals}"


def test_write_tag_minutes_uses_absolute_overwrite():
    """Tag/project minute totals in 0n must use absolute overwrite (set value of),
    not append. These are recalculated totals from Toggl, not incremental points.
    Appending caused ballooning values when 0t or the daemon ran multiple times."""
    import ast
    source = Path(__file__).parent.joinpath("0t-fast.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "write_tag_minutes":
            body_src = ast.get_source_segment(source, node)
            assert "set value of" in body_src, (
                "write_tag_minutes must use absolute overwrite (set value of) for Toggl minute totals"
            )
            assert "oldVal" not in body_src, (
                "write_tag_minutes must NOT append to old formula — these are absolute totals"
            )
            return
    raise AssertionError("write_tag_minutes function not found")


def test_marks_done_then_refreshes_dtd_cache():
    """/0t records 0t in completed-today via mark_done(), but dtd only reloads on a
    cache mtime change — so 0t-fast must run did-fast --refresh-cache after marking
    done, else 0t lingers on the dtd list (regression 2026-06-30)."""
    import ast
    src = (Path(__file__).parent / "0t-fast.py").read_text()
    main = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    body = ast.get_source_segment(src, main)
    assert '"--refresh-cache"' in body, "0t-fast main() must refresh the dtd cache"
    # and it must come AFTER mark_done() so completed-today is already written
    assert body.index("mark_done()") < body.index('"--refresh-cache"'), \
        "refresh must follow mark_done()"
