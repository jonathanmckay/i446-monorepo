#!/usr/bin/env python3
"""Tests for undo-fast.py (dtd ctrl-z) and mark-completed.py --remove.

Excel (ix_run) and Todoist (_api) calls are monkey-patched to recorders, so
these tests verify op *collection* and journal mechanics, not the transports.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


uf = _load("undo_fast", "undo-fast.py")
mc = _load("mark_completed_t", "mark-completed.py")


# ---------------------------------------------------------------------------
# mark-completed --remove
# ---------------------------------------------------------------------------

def test_remove_names(tmp_path):
    p = tmp_path / "completed.json"
    today = date.today().isoformat()
    mc.append_names(["buy plants", "新闻"], today=today, path=p,
                    points={"buy plants": 20})
    data = mc.remove_names(["Buy Plants [20]"], path=p)
    assert data["names"] == ["新闻"]
    assert "buy plants" not in data.get("points", {})
    assert "buy plants" not in data.get("timestamps", {})


def test_remove_names_missing_file(tmp_path):
    data = mc.remove_names(["x"], path=tmp_path / "nope.json")
    assert data["names"] == []


# ---------------------------------------------------------------------------
# Journal mechanics
# ---------------------------------------------------------------------------

def test_journal_append_and_pop_lifo(tmp_path, monkeypatch):
    monkeypatch.setattr(uf, "reverse_record", lambda r, e: None)
    j = str(tmp_path / "j.jsonl")
    uf.journal_append(j, {"type": "done", "names": ["a"]})
    uf.journal_append(j, {"type": "defer", "names": ["b"]})
    r1 = uf.journal_pop_and_reverse(j, None, None, None)
    assert r1["ok"] and r1["names"] == ["b"]
    r2 = uf.journal_pop_and_reverse(j, None, None, None)
    assert r2["ok"] and r2["names"] == ["a"]
    r3 = uf.journal_pop_and_reverse(j, None, None, None)
    assert r3.get("error") == "nothing to undo"


def test_journal_stale_record_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(uf, "reverse_record", lambda r, e: None)
    j = tmp_path / "j.jsonl"
    j.write_text(json.dumps({"type": "done", "names": ["x"],
                             "date": "2020-01-01"}) + "\n")
    r = uf.journal_pop_and_reverse(str(j), None, None, None)
    assert "stale" in r.get("error", "")
    # record must NOT have been popped
    assert j.read_text().strip() != ""


def test_journal_corrupt_line_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(uf, "reverse_record", lambda r, e: None)
    j = tmp_path / "j.jsonl"
    good = json.dumps({"type": "done", "names": ["a"],
                       "date": date.today().isoformat()})
    j.write_text(good + "\n{not json\n")
    r = uf.journal_pop_and_reverse(str(j), None, None, None)
    assert "corrupt" in r.get("error", "")
    r2 = uf.journal_pop_and_reverse(str(j), None, None, None)
    assert r2["ok"] and r2["names"] == ["a"]


# ---------------------------------------------------------------------------
# Filter-file cleanup
# ---------------------------------------------------------------------------

def test_clean_filter_files(tmp_path):
    session = tmp_path / "session"
    removed = tmp_path / "removed"
    done = tmp_path / "done.json"
    session.write_text("Buy plants\nother task\n")
    removed.write_text("buy plants\n")
    done.write_text(json.dumps(["buy plants", "新闻"]))
    uf.clean_filter_files(["buy plants [20]"], str(session), str(removed), str(done))
    assert session.read_text() == "other task\n"
    assert removed.read_text() == ""
    assert json.loads(done.read_text()) == ["新闻"]


# ---------------------------------------------------------------------------
# Reversal op collection (mocked transports)
# ---------------------------------------------------------------------------

class FakeRes:
    returncode = 0
    stdout = "OK"
    stderr = ""


def test_reverse_done_collects_ops(monkeypatch):
    scripts = []
    api_calls = []
    monkeypatch.setattr(uf, "ix_run", lambda s, timeout=30.0: (scripts.append(s), FakeRes())[1])
    monkeypatch.setattr(uf, "_api", lambda m, p, b=None, timeout=15.0: api_calls.append((m, p, b)))

    out = {
        "results": [
            {   # 0n habit with pre-image + bonus points to 0分
                "name": "新闻", "step": "0n", "col": 8, "value": 35,
                "undo": {"prev_0n": ""},
                "0fen": {"col": "U", "points": 40},
            },
            {   # plain todoist task, non-recurring, closed
                "name": "buy plants", "step": "todoist",
                "0fen": {"col": "X", "points": 20},
                "todoist": {"id": "t1", "closed": True, "recurring": False,
                            "prev_due": "2026-06-04"},
            },
            {   # recurring todoist close → due restore
                "name": "hiit", "step": "todoist",
                "todoist": {"id": "t2", "closed": True, "recurring": True,
                            "prev_due": "2026-06-04"},
            },
            {   # variable item with posthoc + curly bonus
                "name": "walk", "step": "variable",
                "0fen": {"col": "W", "points": 15},
                "curly_q": 10,
                "posthoc": {"id": "p1", "closed": True},
            },
            {   # non-variable 1n+ write with pre-image + 0分 cell ref
                "name": "1s", "step": "1n", "col_letter": "D", "week_row": "12",
                "fen_col": "T", "undo": {"prev_1n_formula": ""},
            },
        ],
    }
    errors = []
    uf.reverse_didfast_output(out, "6/4", "2026-06-04", errors)
    assert errors == []

    # Todoist: reopen t1, reschedule t2, delete posthoc p1
    assert ("POST", "/tasks/t1/reopen", None) in api_calls
    assert ("POST", "/tasks/t2", {"due_date": "2026-06-04"}) in api_calls
    assert ("DELETE", "/tasks/p1", None) in api_calls

    joined = "\n".join(scripts)
    # 0n pre-image restore
    assert 'sheet "0n"' in joined and "cell 8 of row todayRow" in joined
    # 0分 strips: +40 (U), +20 (X), +15 (W), +10 (Q), +'1n+'!D12 (T)
    for term in ('"+40"', '"+20"', '"+15"', '"+10"', "+'1n+'!D12"):
        assert term in joined, f"missing strip term {term}"
    # 1n+ pre-image restore (empty → clear cell)
    assert 'range ("D12") of ws1n to ""' in joined


def test_reverse_defer_record(monkeypatch):
    api_calls = []
    monkeypatch.setattr(uf, "_api", lambda m, p, b=None, timeout=15.0: api_calls.append((m, p, b)))
    errors = []
    uf.reverse_record({
        "type": "defer", "names": ["buy plants"], "task_id": "t9",
        "prev_due": "2026-06-04", "stub_id": "s1",
        "date": date.today().isoformat(),
    }, errors)
    assert errors == []
    assert ("POST", "/tasks/t9", {"due_date": "2026-06-04"}) in api_calls
    assert ("DELETE", "/tasks/s1", None) in api_calls


def test_reverse_split_record(monkeypatch, tmp_path):
    api_calls = []
    monkeypatch.setattr(uf, "_api", lambda m, p, b=None, timeout=15.0: api_calls.append((m, p, b)))
    monkeypatch.setattr(uf, "ix_run", lambda s, timeout=30.0: FakeRes())
    monkeypatch.setattr(uf.mc, "COMPLETED", tmp_path / "completed.json")
    errors = []
    uf.reverse_record({
        "type": "split", "names": ["big task"], "task_id": "t5",
        "prev_content": "big task (60) [40]", "prev_due": "2026-06-04",
        "posthoc_id": "p7",
        "didfast": {"results": [{"name": "big task", "step": "variable",
                                 "0fen": {"col": "R", "points": 15}}]},
        "date": date.today().isoformat(),
    }, errors)
    assert errors == []
    assert ("DELETE", "/tasks/p7", None) in api_calls
    assert ("POST", "/tasks/t5", {"content": "big task (60) [40]",
                                  "due_date": "2026-06-04"}) in api_calls


# ---------------------------------------------------------------------------
# Delete undo (ctrl-x journaled, ctrl-z recreates)
# ---------------------------------------------------------------------------

def test_reverse_delete_recreates_task(monkeypatch):
    """Bug: ctrl-x never journaled, so ctrl-z undid the previous done action
    instead of the delete. A 'delete' record must recreate the task with its
    full pre-image (project, labels, priority, recurring due string)."""
    api_calls = []
    monkeypatch.setattr(uf, "_api", lambda m, p, b=None, timeout=15.0: api_calls.append((m, p, b)))
    errors = []
    uf.reverse_record({
        "type": "delete", "names": ["社+hcbp"],
        "task": {
            "id": "t77", "content": "社+hcbp (15) [10]",
            "description": "", "priority": 3, "labels": ["hcbp"],
            "project_id": "pj1", "section_id": None, "parent_id": None,
            "due": {"date": "2026-06-11", "string": "every day",
                    "is_recurring": True},
        },
        "date": date.today().isoformat(),
    }, errors)
    assert errors == []
    assert len(api_calls) == 1
    method, path, body = api_calls[0]
    assert (method, path) == ("POST", "/tasks")
    assert body["content"] == "社+hcbp (15) [10]"
    assert body["project_id"] == "pj1"
    assert body["labels"] == ["hcbp"]
    assert body["priority"] == 3
    # Recurring pre-image must restore the recurrence, not a flat date
    assert body["due_string"] == "every day"
    assert "due_date" not in body and "due_datetime" not in body
    # Null section/parent must not be sent
    assert "section_id" not in body and "parent_id" not in body


def test_reverse_delete_prefers_datetime_over_date(monkeypatch):
    api_calls = []
    monkeypatch.setattr(uf, "_api", lambda m, p, b=None, timeout=15.0: api_calls.append((m, p, b)))
    errors = []
    uf.reverse_record({
        "type": "delete", "names": ["standup"],
        "task": {"content": "standup",
                 "due": {"date": "2026-06-11",
                         "datetime": "2026-06-11T09:30:00",
                         "is_recurring": False}},
        "date": date.today().isoformat(),
    }, errors)
    assert errors == []
    _, _, body = api_calls[0]
    assert body["due_datetime"] == "2026-06-11T09:30:00"
    assert "due_date" not in body


def test_reverse_delete_minimal_record_falls_back_to_name(monkeypatch):
    """If the pre-image GET failed, the journal holds only the name; undo
    must still recreate a bare task rather than erroring."""
    api_calls = []
    monkeypatch.setattr(uf, "_api", lambda m, p, b=None, timeout=15.0: api_calls.append((m, p, b)))
    errors = []
    uf.reverse_record({"type": "delete", "names": ["lost task"], "task": {},
                       "date": date.today().isoformat()}, errors)
    assert errors == []
    assert api_calls[0][2]["content"] == "lost task"


def test_dtd_delete_script_journals_after_successful_delete():
    """Structural: dtd.sh's ctrl-x script must journal the delete for ctrl-z,
    and only after the DELETE succeeded (HTTP 2xx) — journaling a failed
    delete would make ctrl-z recreate a task that still exists."""
    src = (HERE / "dtd.sh").read_text()
    start = src.index("DTD_DELETE=")
    end = src.index("DELETEEOF\nchmod", start)
    section = src[start:end]
    assert "--append" in section, "delete script never journals for ctrl-z"
    assert section.index("-X DELETE") < section.index("--append"), \
        "journal must happen after the DELETE, not before"
    assert "http_code" in section and '== 2*' in section.replace('"', ''), \
        "journal append must be guarded on DELETE success"
    # Pre-image is fetched before the task is gone
    assert section.index("pre=") < section.index("-X DELETE")


# ---------------------------------------------------------------------------
# Strip-or-negate AppleScript shape
# ---------------------------------------------------------------------------

def test_strip_or_negate_negation_term():
    src = uf._strip_or_negate_lines('range ("U" & todayRow) of ws', "+15", "0")
    assert '"+15"' in src and '"-15"' in src
    src2 = uf._strip_or_negate_lines('range ("T" & todayRow) of ws', "+'1n+'!D12", "1")
    assert "\"+'1n+'!D12\"" in src2 and "\"-'1n+'!D12\"" in src2


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
