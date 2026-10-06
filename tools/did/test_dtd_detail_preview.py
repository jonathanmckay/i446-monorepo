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


def test_binding_and_preview_wiring():
    assert '--bind "ctrl-o:toggle-preview"' in DTD
    m = re.search(r'--preview "([^"]*)" --preview-window "([^"]*)"', DTD)
    assert m, "preview flags missing"
    assert m.group(1) == "python3 $DTD_DETAIL {2}"  # hidden id field, not the short display name
    assert "hidden" in m.group(2).split(",")       # never runs (no API calls) until toggled
    assert 'DTD_DETAIL="$HOME/i446-monorepo/tools/did/task-detail.py"' in DTD
    assert "ctrl-o: details" in DTD


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
