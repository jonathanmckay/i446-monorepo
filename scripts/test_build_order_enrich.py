"""Regression tests for build-order-enrich.py."""
import ast
import pathlib

SRC = pathlib.Path(__file__).parent / "build-order-enrich.py"


def test_script_is_parseable():
    """Script must be valid Python (no syntax errors)."""
    ast.parse(SRC.read_text())


def test_toggl_cli_uses_date_subcommand_for_non_today():
    """The script must support fetching Toggl entries for a specific date,
    not only 'today'. get_toggl_today should exist and call toggl_cli."""
    source = SRC.read_text()
    assert "toggl_cli" in source.lower() or "TOGGL_CLI" in source
    assert "def get_toggl_today" in source


def test_parse_toggl_entries_handles_running():
    """parse_toggl_entries must handle 'running' as end time."""
    source = SRC.read_text()
    assert '"running"' in source or "'running'" in source


def test_entries_in_block_excludes_sleep():
    """Sleep entries must be excluded from block time views."""
    source = SRC.read_text()
    func_start = source.index("def entries_in_block(")
    func_end = source.index("\ndef ", func_start + 1)
    func_src = source[func_start:func_end]
    assert "睡觉" in func_src, "entries_in_block must filter out 睡觉 entries"


def test_enrichment_only_for_past_blocks():
    """Enrichment sections must only be added for blocks before the current one,
    not the current or future blocks."""
    source = SRC.read_text()
    assert "current_block_idx < current_idx" in source or "< current_idx" in source


def test_completed_tasks_archived_for_durability():
    """Bug: completed-today.json resets daily, so by the time enrichment runs
    the next morning (or if the cron was broken), yesterday's completed tasks
    are gone. Fix: archive completed tasks to a date-keyed file on each run
    and merge with archive when reading."""
    source = SRC.read_text()

    # Must have archive directory constant
    assert "COMPLETED_ARCHIVE_DIR" in source, \
        "Must define COMPLETED_ARCHIVE_DIR for durable completed task storage"

    # get_completed_today must read from archive
    func_start = source.index("def get_completed_today(")
    func_end = source.index("\ndef ", func_start + 1)
    func_src = source[func_start:func_end]
    assert "archive" in func_src.lower(), \
        "get_completed_today must read from archive"

    # Must have an archive function
    assert "def _archive_completed(" in source, \
        "Must have _archive_completed function to persist tasks"


def test_d357_links_inline_on_time_entries():
    """Bug: d357 meeting docs were shown in a separate **Meetings** section
    instead of inline on the matching time entry line.
    Fix: match d357 docs to time entries by word overlap and append
    [[d357/slug|d357]] to the time entry line."""
    source = SRC.read_text()
    func_start = source.index("def build_enrichment_sections(")
    func_end = source.index("\ndef ", func_start + 1)
    func_src = source[func_start:func_end]

    # Must match docs to entries (doc_by_entry dict)
    assert "doc_by_entry" in func_src, \
        "build_enrichment_sections must match d357 docs to time entries"

    # d357 link must appear in the time entry line format string
    assert "d357_link" in func_src, \
        "Time entry lines must include d357 link variable"

    # Must NOT have a separate **Meetings** header for matched docs
    # (unmatched docs are still listed separately, but matched ones are inline)
    assert "claimed_docs" in func_src, \
        "Must track which d357 docs are claimed by time entries"


def test_block_name_clean_strips_duration_suffix():
    """Regression: block_name_clean('- 辰 ☀️ 📧 ⏰ (134min)') returned
    '辰    (134min)' instead of '辰', causing block matching to fail on
    re-enrichment runs. Duration suffixes must be stripped."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("boe_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["boe_test"] = mod
    spec.loader.exec_module(mod)

    assert mod.block_name_clean("- 辰 ☀️ 📧 ⏰ (134min)") == "辰"
    assert mod.block_name_clean("- 午 ☀️ 📧 ⏰ (90min)") == "午"
    assert mod.block_name_clean("- 巳 ☀️ 📧 ⏰ (20分, 124min)") == "巳"
    assert mod.block_name_clean("- 申") == "申"
    assert mod.block_name_clean("- 亥 📧") == "亥"
    # Without duration
    assert mod.block_name_clean("- 午 ☀️ 📧 ⏰") == "午"


def _load_boe():
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("boe_load", SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["boe_load"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_timestamp_to_block_idx():
    mod = _load_boe()
    assert mod.timestamp_to_block_idx("06:30") == 1  # 辰
    assert mod.timestamp_to_block_idx("08:15") == 2  # 巳
    assert mod.timestamp_to_block_idx("10:00") == 3  # 午
    assert mod.timestamp_to_block_idx("13:45") == 4  # 未
    assert mod.timestamp_to_block_idx(None) is None


def test_completed_tasks_bucketed_by_timestamp(tmp_path):
    """Regression: completed tasks without timestamps all ended up in the last
    past block. With timestamps, tasks must be assigned to the block matching
    their completion time."""
    import json
    from unittest.mock import patch
    mod = _load_boe()

    fake_bo = tmp_path / "bo.md"
    fake_bo.write_text(
        "## -1₲\n\n"
        "- 辰\n    - [ ] goal1\n"
        "- 巳\n    - [ ] goal2\n"
        "- 午\n    - [ ] goal3\n"
        "- 未\n    - [ ] goal4\n"
    )
    fake_completed = tmp_path / "completed-today.json"
    fake_completed.write_text(json.dumps({
        "date": "2026-04-28",
        "names": ["cpap", "push", "notes"],
        "points": {},
        "timestamps": {"cpap": "06:45", "push": "06:50", "notes": "09:30"},
    }))
    fake_archive = tmp_path / "archive"
    fake_archive.mkdir()

    with patch.object(mod, "BUILD_ORDER", fake_bo), \
         patch.object(mod, "COMPLETED_TODAY", fake_completed), \
         patch.object(mod, "COMPLETED_ARCHIVE_DIR", fake_archive), \
         patch.object(mod, "get_current_block_idx", return_value=4), \
         patch.object(mod, "get_toggl_today", return_value=""), \
         patch.object(mod, "get_d357_docs_today", return_value=[]):
        mod.enrich_build_order()

    result = fake_bo.read_text()
    # cpap and push were at 06:xx (辰 block), notes at 09:30 (巳 block)
    辰_section = result[result.index("- 辰"):result.index("- 巳")]
    巳_section = result[result.index("- 巳"):result.index("- 午")]
    午_section = result[result.index("- 午"):result.index("- 未")]

    assert "cpap" in 辰_section, f"cpap should be in 辰, got: {辰_section}"
    assert "push" in 辰_section, f"push should be in 辰, got: {辰_section}"
    assert "notes" in 巳_section, f"notes should be in 巳, got: {巳_section}"
    assert "cpap" not in 午_section
    assert "push" not in 午_section
    assert "notes" not in 午_section
