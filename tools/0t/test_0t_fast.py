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


def test_xk87_matched_by_tag_not_project():
    """xk87 minutes for AZ must be matched by the 'xk87' Toggl tag,
    not by project_id. Tags sum across both days (like other tags)."""
    yesterday = date(2026, 5, 16)
    today = date(2026, 5, 17)

    yesterday_entries = [
        {"duration": 18000, "tags": ["-3", "xk87"], "project_id": 163129781},  # 300min
    ]
    today_entries = [
        {"duration": 24000, "tags": ["xk87"], "project_id": 163129781},  # 400min
    ]

    def fake_entries(d):
        if d == yesterday:
            return yesterday_entries
        return today_entries

    with patch.object(zerot_fast, "get_toggl_entries", side_effect=fake_entries):
        tag_totals, proj_totals = zerot_fast.compute_tag_minutes(yesterday, today)

    # xk87 must be in tag_totals (matched by tag), not proj_totals
    assert "xk87" in tag_totals, "xk87 should be matched by tag, not project_id"
    assert tag_totals["xk87"] == 700, f"xk87 tag should sum both days (700), got {tag_totals['xk87']}"
    assert not proj_totals, f"proj_totals should be empty, got {proj_totals}"


def test_write_tag_minutes_appends_not_overwrites():
    """Tag/project writes must append to existing cell formulas, not overwrite.
    Overwriting destroys points from other sources (did-fast, manual)."""
    import ast
    source = Path(__file__).parent.joinpath("0t-fast.py").read_text()
    tree = ast.parse(source)

    # Find the write_tag_minutes function
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "write_tag_minutes":
            body_src = ast.get_source_segment(source, node)
            # Must NOT contain "set value of" (overwrite pattern)
            assert "set value of" not in body_src, (
                "write_tag_minutes must append (formula & \"+N\"), not overwrite (set value of)"
            )
            # Must contain the append pattern
            assert '& "+"' in body_src or "& \"+{" in body_src or 'oldVal & "+"' in body_src or "oldVal" in body_src, (
                "write_tag_minutes must read old formula and append"
            )
            return
    raise AssertionError("write_tag_minutes function not found")
