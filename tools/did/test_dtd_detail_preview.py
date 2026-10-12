#!/usr/bin/env python3
"""dtd ctrl-o toggles a detail pane (2026-10-06, user request "I want the
ability to see everything (links, description etc)").

Covers: the binding + hidden-by-default preview wired to task-detail.py with
the hidden id field {2}, the key legend, and task-detail.py's rendering
(description, comments minus dtd-short cache lines, link extraction, picker
rows ignored).
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DTD = (HERE / "dtd.sh").read_text()


def _load():
    spec = importlib.util.spec_from_file_location("task_detail_t", HERE / "task-detail.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_ctrl_o_and_ctrl_g_open_the_same_edit_screen():
    """2026-10-11: "ideally ctrl+g and ctrl+o go to the same place, and show the
    different fields that I could add along with all the context"."""
    action = "execute($DTD_EDIT {2})+reload($DTD_RELOAD)+transform-header($DTD_HDRGEN)"
    assert f'--bind "ctrl-o:{action}"' in DTD
    assert f'--bind "ctrl-g:{action}"' in DTD
    assert "--preview " not in DTD
    edit = DTD[DTD.index('cat > "$DTD_EDIT" << EDITEOF'):]
    edit = edit[:edit.index("\nEDITEOF\n")]
    assert 'python3 "$DTD_DETAIL" "\\$1"' in edit           # full context on top
    for label in ("Title", "Domain", "Points", "Description"):
        assert f'"{label}" "' in edit                          # each field labeled with its destination
    assert "--fields --id" in edit
    assert "ctrl-o/g: details+edit" in DTD


def test_render_description_comments_links():
    td = _load()
    task = {"content": "Call [Bob](https://example.com/bob) re lease (15) [20]",
            "description": "See https://docs.example.com/x. Then reply.",
            "labels": ["m5x2"], "due": {"string": "every day"}, "priority": 4}
    comments = [{"content": "dtd-short:abcd1234:Call Bob", "posted_at": "2026-10-01T00:00:00Z"},
                {"content": "notes in [doc](https://d.example.com/n)", "posted_at": "2026-10-02T00:00:00Z"}]
    out = re.sub(r"\033\[[0-9;]*m", "", td.render(task, comments, "Leasing"))
    assert "Call Bob re lease (15) [20]" in out          # markdown link collapsed in title
    assert "#Leasing · @m5x2 · due every day · p1" in out
    assert "See https://docs.example.com/x. Then reply." in out
    assert "dtd-short" not in out and "comments (1)" in out
    links = out.split("── links ──")[1].split()
    assert links == ["https://example.com/bob", "https://docs.example.com/x", "https://d.example.com/n"]


def test_render_empty_task():
    td = _load()
    out = td.render({"content": "plain"}, [], None)
    assert "no description, comments, or links" in out


def test_picker_rows_and_blank_print_nothing():
    for arg in ["BLOCK:戌", ""]:
        r = subprocess.run([sys.executable, str(HERE / "task-detail.py"), arg],
                           capture_output=True, text=True, timeout=5)
        assert r.returncode == 0 and r.stdout == ""
