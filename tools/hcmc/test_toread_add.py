"""toread-add (2026-11-15): a link or a dtd task becomes a JM Reads to-read card."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("toread_add", HERE / "toread-add.py")
ta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ta)

sys.path.insert(0, str(HERE.parent / "personal-dashboard"))
import jmreads  # noqa: E402

TASKS = [
    {"id": "T1", "content": "read: AI Futures Q3.5 timelines update [(link)](https://blog.aifutures.org/p/q35) (20) [10]",
     "labels": ["hcmc"]},
    {"id": "T2", "content": "1 i447 (20) [25]", "labels": ["1neon", "i447"]},
    {"id": "T3", "content": "read newmark report (20) [20]", "labels": ["m5x2"]},
]
PAGE = '<html><head><meta property="og:title" content="Q3.5 update &amp; more"><meta name="author" content="Daniel K"></head></html>'
DOC = """---
title: To Read
updated: 2026-10-09
---

| Title | Link | 分 | Time | Area |
| --- | --- | --- | --- | --- |
| Email | [gmail](https://mail.google.com/x) | | | |
| Old Essay — Someone | Done | | | hcmc |

trailing note
"""


def _order(tmp_path, ids, age=0):
    p = tmp_path / "order.json"
    p.write_text(json.dumps({"ts": time.time() - age, "ids": ids}))
    return p


def test_task_by_name_keeps_link_estimates_and_label(tmp_path):
    f = ta.resolve("ai futures", TASKS, fetch=lambda u: PAGE, order=_order(tmp_path, []))
    assert f["title"] == "AI Futures Q3.5 timelines update"      # "read:" and annotations gone
    assert f["url"] == "https://blog.aifutures.org/p/q35"
    assert (f["minutes"], f["points"], f["area"], f["task_id"]) == ("20", "10", "hcmc", "T1")
    assert f["author"] == "Daniel K"                              # byline fetched from the page


def test_task_by_dtd_row_and_stale_row(tmp_path):
    f = ta.resolve("2", TASKS, order=_order(tmp_path, ["T1", "T3"]))
    assert f["title"] == "newmark report" and f["area"] == "m5x2" and f["author"] == "m5x2"
    assert ta.resolve("2", TASKS, order=_order(tmp_path, ["T1", "T3"], age=7200)).startswith("ERROR")


def test_link_uses_page_title_and_author_then_falls_back_to_domain():
    f = ta.resolve("https://example.com/post/my-essay", [], fetch=lambda u: PAGE)
    assert (f["title"], f["author"], f["area"]) == ("Q3.5 update & more", "Daniel K", "hcmc")

    def down(u):
        raise OSError("offline")
    f = ta.resolve("https://www.example.com/post/my-essay", [], fetch=down)
    assert (f["title"], f["author"]) == ("my essay", "example.com")


def test_link_with_typed_title_skips_the_fetch():
    def boom(u):
        raise AssertionError("should not fetch")
    f = ta.resolve("https://example.com/x Malleable Software — Litt et al", [], fetch=boom)
    assert (f["title"], f["author"]) == ("Malleable Software", "Litt et al")


def test_unknown_words_are_an_error_not_a_card(tmp_path):
    assert ta.resolve("no such thing", TASKS, order=_order(tmp_path, [])).startswith("ERROR")
    f = ta.resolve("Mencius — Mencius", TASKS, order=_order(tmp_path, []))
    assert (f["title"], f["author"], f["url"]) == ("Mencius", "Mencius", "")


def test_row_lands_in_the_table_and_jmreads_shows_it(tmp_path):
    row = ta.make_row("AI Futures Q3.5 | timelines", "Daniel K", "https://blog.aifutures.org/p/q35", "10", "20", "hcmc")
    out = ta.append_row(DOC, row, datetime.date(2026, 11, 15))
    lines = out.splitlines()
    assert lines.index(row) == lines.index("| Old Essay — Someone | Done | | | hcmc |") + 1
    assert "trailing note" in out and "updated: 2026-11-15" in out
    p = tmp_path / "to-read.md"
    p.write_text(out)
    cards = jmreads.load_to_read(p)
    assert [c["link"] for c in cards] == ["https://blog.aifutures.org/p/q35"]   # Done row + email skipped
    assert cards[0]["author"] == "Daniel K" and cards[0]["title"].startswith("AI Futures Q3.5")


def test_existing_matches_link_or_title_but_not_done_rows():
    doc = ta.append_row(DOC, ta.make_row("My Essay", "A", "https://e.com/1"), datetime.date(2026, 11, 15))
    assert ta.existing(doc, "other", "https://e.com/1")
    assert ta.existing(doc, "my essay", "")
    assert ta.existing(doc, "Old Essay", "") is None
    assert ta.existing(doc, "new thing", "https://e.com/2") is None
