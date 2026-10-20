#!/usr/bin/env python3
"""/1f693 survey: pure parts (spec parsing, task content, doc entry insertion)."""
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
_SPEC = importlib.util.spec_from_file_location("f693_form", HERE / "1f693.py")
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)


def test_parse_task_spec_defaults_and_overrides():
    assert m.parse_task_spec("call about rampdown") == ("call about rampdown", 15, 10)
    assert m.parse_task_spec("4 week checkin (30) [25]") == ("4 week checkin", 30, 25)
    assert m.parse_task_spec("[5] ping HR (5)") == ("ping HR", 5, 5)


def test_task_content_prefix_initials_upper():
    assert m.task_content("lr", "do a 4 week checkin") == "f693 LR: do a 4 week checkin (15) [10]"
    assert m.task_content("", "ask HR cabal (5) [3]") == "f693 ask HR cabal (5) [3]"


def test_clean_slots_drops_blank_rows_keeps_order():
    slots = [{"initials": "a", "task": ""}, {"initials": "jl", "task": " x "}, {"task": "y"}]
    assert m.clean_slots(slots) == [{"initials": "JL", "task": "x"}, {"initials": "", "task": "y"}]


def test_render_entry_and_insert_under_current_org():
    d = dt.date(2026, 10, 19)
    entry = m.render_entry([{"initials": "GI", "task": "find tutor (20)"}], d)
    assert entry.startswith("10.19\n<!-- paste the org screenshot")
    assert "**Stoplight:** _pending" in entry
    assert "* f693 GI: find tutor (20) [10]" in entry
    doc = "---\ntitle: x\n---\nintro\n\n## Current Org\n\n10.12\n![[old.png]]\n\n## Other\n"
    out = m.insert_entry(doc, entry)
    assert out.index("10.19") < out.index("10.12"), "newest entry goes first"
    assert out.index("## Current Org") < out.index("10.19")
    assert out.count("## Current Org") == 1 and "## Other" in out


def test_render_entry_without_todos_notes_none():
    assert "_(none recorded)_" in m.render_entry([])


def test_insert_entry_appends_anchor_when_missing():
    out = m.insert_entry("just text\n", "10.19\nx\n")
    assert "## Current Org\n\n10.19" in out


def test_dry_run_from_json_writes_nothing(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps([{"initials": "ab", "task": "thing (10) [5]"}, {"initials": "", "task": ""}]))
    before = m.F693_DOC.read_text() if m.F693_DOC.exists() else None
    r = subprocess.run([sys.executable, str(HERE / "1f693.py"), "--from-json", str(f), "--dry-run"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "todoist: f693 AB: thing (10) [5]" in r.stdout
    assert "--- f693.md entry ---" in r.stdout
    after = m.F693_DOC.read_text() if m.F693_DOC.exists() else None
    assert before == after
