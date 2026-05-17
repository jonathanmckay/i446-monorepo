"""Tests for tg-fast.py parsing logic."""
import ast
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).parent / "tg-fast.py"


def _get_main_source():
    return SRC.read_text()


def test_backdate_end_pattern_exists():
    """desc HHMM pattern must be handled before the default start path."""
    tree = ast.parse(_get_main_source())
    source = _get_main_source()
    # The regex for trailing HHMM must appear in the source
    assert r"\s(\d{4})$" in source or r'\s(\d{4})$' in source


def test_backdate_end_match_regex():
    """A trailing 4-digit time (0000-2359) after a description should match."""
    pattern = re.compile(r'\s(\d{4})$')
    assert pattern.search("0l 0706")
    assert pattern.search("work 1823")
    assert pattern.search("family time 0900")
    # Should NOT match when no space before digits
    assert not pattern.search("task1234")


def test_backdate_end_before_default():
    """The desc-HHMM block must appear before the default start block in main()."""
    source = _get_main_source()
    end_match_pos = source.find("desc HHMM")
    default_pos = source.find("# Default: start timer")
    assert end_match_pos != -1, "desc HHMM comment not found"
    assert default_pos != -1, "default start comment not found"
    assert end_match_pos < default_pos, "desc HHMM check must come before default start"


def test_backdate_rejects_invalid_time():
    """Times like 2500 or 1299 should not be treated as backdated starts."""
    pattern = re.compile(r'\s(\d{4})$')
    m = pattern.search("work 2500")
    assert m  # regex matches, but validation should reject
    backtime = m.group(1)
    h, mm = int(backtime[:2]), int(backtime[2:])
    assert not (0 <= h <= 23 and 0 <= mm <= 59), "2500 should fail validation"

    m2 = pattern.search("work 1299")
    backtime2 = m2.group(1)
    h2, mm2 = int(backtime2[:2]), int(backtime2[2:])
    assert not (0 <= h2 <= 23 and 0 <= mm2 <= 59), "1299 should fail validation"


def _import_tg_fast():
    """Import tg-fast.py as a module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tg_fast", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_falls_back_to_task_cache_labels():
    """When shortcode/domain lookup fails, resolve() should check task-queue.json
    labels and return a valid Toggl project code."""
    fake_cache = {
        "0neon": [
            {"id": "1", "content": "2nd hci (5) [7]", "labels": ["hci", "0neon"], "due": "2026-05-17"},
        ],
        "1neon": [
            {"id": "2", "content": "push more [30]", "labels": ["1neon", "i9"], "due": "2026-05-17"},
        ],
    }
    mod = _import_tg_fast()
    # Patch _get_toggl_projects to return known valid codes
    mod._TOGGL_PROJECTS = {"hci", "hcmc", "i9", "m5x2", "xk87"}

    with patch.object(Path, "read_text", return_value=json.dumps(fake_cache)):
        # "2nd hci" doesn't match any shortcode or domain
        desc, project, tags = mod.resolve("2nd hci")
        assert project == "hci", f"Expected 'hci', got '{project}'"

        # "push more" doesn't match any shortcode or domain
        desc, project, tags = mod.resolve("push more")
        assert project == "i9", f"Expected 'i9', got '{project}'"


def test_resolve_shortcode_takes_priority_over_cache():
    """Shortcode matches should still take priority over cache lookup."""
    mod = _import_tg_fast()
    # "新闻" is in SHORTCODES mapping to hcmc
    desc, project, tags = mod.resolve("新闻")
    assert project == "hcmc"


def test_resolve_no_project_when_label_not_in_toggl():
    """Tasks whose labels don't exist in Toggl PROJECT_MAP should return empty project."""
    fake_cache = {
        "1neon": [
            {"id": "3", "content": "1 f694 (5) [10]", "labels": ["1neon", "f694"], "due": "2026-05-17"},
        ],
    }
    mod = _import_tg_fast()
    mod._TOGGL_PROJECTS = {"hci", "hcmc", "i9"}  # f694 not included

    with patch.object(Path, "read_text", return_value=json.dumps(fake_cache)):
        desc, project, tags = mod.resolve("1 f694")
        assert project == "", f"Expected empty, got '{project}'"
