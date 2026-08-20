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


def test_end_range_accepts_4digit_hhmm():
    """Regression: 'desc HHMM-HHMM' (4-digit range AFTER the description, e.g.
    '睡觉 2200-0600') must parse as a completed time range, not fall through to
    starting a timer. Previously the end-range regex only allowed 1-2 digit
    hours, so '睡觉 2200-0600' started a timer instead."""
    pattern = re.compile(r'(\d{1,4}(?::\d{2})?)\s*-\s*(\d{1,4}(?::\d{2})?)\s*$')
    m = pattern.search("睡觉 2200-0600")
    assert m, "'睡觉 2200-0600' must match the end-range pattern"
    mod = _import_tg_fast()
    assert mod._norm_time(m.group(1)) == "22:00"
    assert mod._norm_time(m.group(2)) == "06:00"


def test_norm_time_formats():
    """_norm_time normalizes HH:MM passthrough, 4-digit HHMM, 3-digit HMM,
    and bare hour."""
    mod = _import_tg_fast()
    assert mod._norm_time("22:00") == "22:00"
    assert mod._norm_time("2200") == "22:00"
    assert mod._norm_time("0600") == "06:00"
    assert mod._norm_time("930") == "09:30"
    assert mod._norm_time("9") == "09:00"
    assert mod._norm_time("21") == "21:00"


def test_end_range_still_matches_legacy_formats():
    """Broadening to \\d{1,4} must not break colon or single-digit-hour ranges."""
    pattern = re.compile(r'(\d{1,4}(?::\d{2})?)\s*-\s*(\d{1,4}(?::\d{2})?)\s*$')
    assert pattern.search("work 9-10")
    assert pattern.search("read 21:30-22:15")
    assert pattern.search("睡觉 22:00-06:00")


def test_desc_range_at_project_creates_range_entry(monkeypatch):
    """Regression (2026-07-13): 'desc TIME-TIME @project' — the documented
    <desc> <start>-<end> @<project> syntax — must create a completed range
    entry, not fall through to starting a new timer.

    Every range regex in _process_entry anchors the range to the very start
    or very end of the string. With '@project' trailing after the range,
    neither anchor matched, so the whole string fell through to the default
    start path. Since Toggl auto-stops the currently running entry whenever
    a new one starts, that silently corrupted an unrelated running timer
    (observed live: a 'Bill Hurwitz 1:1' entry got stretched and stripped of
    its project by this exact input pattern)."""
    mod = _import_tg_fast()
    captured = []
    monkeypatch.setattr(mod, "cmd_create_range",
                        lambda desc, project, tags, s, e: captured.append((desc, project, tags, s, e)))
    monkeypatch.setattr(mod, "cmd_start",
                        lambda desc, project, tags: (_ for _ in ()).throw(
                            AssertionError("must not fall through to cmd_start")))

    mod._process_entry("bill hurwitz 1408-1430 @i9")
    assert captured == [("bill hurwitz", "i9", [], "14:08", "14:30")]


def test_desc_range_at_project_range_at_start_still_works(monkeypatch):
    """Sanity check: range-at-start with a trailing @project already worked
    before this fix (the range anchors to the START, so @project trailing
    after the description doesn't interfere) — must keep working."""
    mod = _import_tg_fast()
    captured = []
    monkeypatch.setattr(mod, "cmd_create_range",
                        lambda desc, project, tags, s, e: captured.append((desc, project, tags, s, e)))
    mod._process_entry("1430-1500 ES 1:1 @i9")
    assert captured == [("ES 1:1", "i9", [], "14:30", "15:00")]


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


def test_resolve_sleep_shortcode_carries_no_tags():
    """Regression (2026-08-20 user report): 睡觉 entries created via /tg were
    tagged "-3", which they should never carry.

    Worse than cosmetic: tools/0t/0t-fast.py's compute_tag_minutes() has to
    explicitly skip SLEEP_PROJECT_ID entries when summing "-3"-tagged
    minutes into the AX column, specifically because 睡觉's own "-3" tag
    used to pollute that sum with a full night's sleep (regression
    2026-06-28, AX=439 -- see the comment there). That skip is keyed on
    project id, not the tag, so it still protects historical entries
    already carrying "-3" -- but nothing should be generating fresh
    "-3"-tagged 睡觉 entries going forward."""
    mod = _import_tg_fast()
    desc, project, tags = mod.resolve("睡觉")
    assert project == "睡觉"
    assert tags == [], f"睡觉 must carry no tags by default, got {tags!r}"


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


def test_main_splits_multiple_entries_incl_fullwidth_comma(monkeypatch, capsys):
    """Regression (2026-06-24): /tg with multiple comma-separated entries (incl.
    the fullwidth CJK comma ，) used to create ONE bogus entry. main() must split
    and process each, joining their outputs."""
    mod = _import_tg_fast()
    seen = []
    monkeypatch.setattr(mod, "resolve_do_session", lambda: None)
    monkeypatch.setattr(mod, "_process_entry", lambda e: seen.append(e) or f"ok:{e}")
    monkeypatch.setattr(mod.sys, "argv",
                        ["tg-fast.py", "0000-0930 睡觉， 0930-1000 一起饭, 1000-1034 ian 1:1"])
    mod.main()
    out = capsys.readouterr().out
    assert seen == ["0000-0930 睡觉", "0930-1000 一起饭", "1000-1034 ian 1:1"], seen
    assert out.count("ok:") == 3, "each entry must produce its own output line"


def test_single_entry_still_works(monkeypatch):
    """A lone entry (no comma) must still be processed exactly once."""
    mod = _import_tg_fast()
    seen = []
    monkeypatch.setattr(mod, "resolve_do_session", lambda: None)
    monkeypatch.setattr(mod, "_process_entry", lambda e: seen.append(e) or "ok")
    monkeypatch.setattr(mod.sys, "argv", ["tg-fast.py", "work 9-10"])
    mod.main()
    assert seen == ["work 9-10"]
