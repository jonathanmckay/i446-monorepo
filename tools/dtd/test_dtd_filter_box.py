#!/usr/bin/env python3
"""Feature (2026-10-17): a filter box in dtd web, like terminal dtd's query
line ("I want to filter xk87 right now"). Every word typed must match the
row's title, raw content or domain code, so a bare domain code scopes the
list to that domain and a word scopes it by text. The matcher is the page's
own JavaScript, run here under node against sample rows."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dtd  # noqa: E402

ROWS = [
    {"id": "1", "title": "fix bikes (delegate?)", "raw": "fix bikes (delegate?) (30) [20]", "domain": "xk87"},
    {"id": "2", "title": "kids nature lands end", "raw": "kids nature lands end {40}", "domain": "xk87"},
    {"id": "3", "title": "repair prius", "raw": "repair prius (30) [8]", "domain": "i447"},
    {"id": "4", "title": "2h IC time", "raw": "2h IC time {30}", "domain": "m5x2"},
]


def _js_filter():
    page = dtd.PAGE
    m = re.search(r"function fuzzyWord\(w, hay\)\{.*?\nfunction filterTasks\(tasks, q\)\{[^\n]*\}\n", page, re.S)
    assert m, "fuzzyWord/matchesFilter/filterTasks must be defined in the page script"
    return m.group(0)


def _run(query):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = _js_filter() + f"\nconst rows = {json.dumps(ROWS)};\n" \
         f"process.stdout.write(JSON.stringify(filterTasks(rows, {json.dumps(query)}).map(t => t.id)));"
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_page_has_the_filter_box_in_the_sticky_header():
    assert 'id="filterInput"' in dtd.PAGE
    assert dtd.PAGE.index('id="filterInput"') < dtd.PAGE.index('<main id="list">')
    assert "localStorage.setItem('dtd.filter'" in dtd.PAGE


def test_domain_code_scopes_to_that_domain():
    assert _run("xk87") == ["1", "2"]
    assert _run("XK87") == ["1", "2"]


def test_words_match_title_and_are_anded():
    assert _run("bike") == ["1"]
    assert _run("kids end") == ["2"]
    assert _run("kids prius") == []


def test_empty_query_keeps_everything():
    assert _run("") == ["1", "2", "3", "4"]
    assert _run("   ") == ["1", "2", "3", "4"]


def test_fuzzy_subsequence_tolerates_a_dropped_letter():
    # "biks" (bikes minus the e) still finds the bikes row via subsequence —
    # the fuzzy upgrade (wave 2 #1). The old substring matcher returned [].
    assert _run("biks") == ["1"]
    assert _run("naturlands") == ["2"]   # nature + lands, run together


def test_refill_wiring_present():
    # Enter on a query that matches nothing opens the add sheet pre-filled with
    # the typed text (wave 2 #1 "refill").
    assert "openAdd(q)" in dtd.PAGE
    assert "filterTasks(allTasks, q).length" in dtd.PAGE
    assert "typeof prefill === 'string'" in dtd.PAGE
