"""jmreads: read a review in place, ratings on cards, Write review → new-notes (2026-11-04)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import jmreads  # noqa: E402

REVIEW = """---
title: "Not Till We Are Lost"
type: review
media: book
author: "Dennis E. Taylor"
score: 4
status: finished
---
Bobs in space again.

> draft 2026-11-04: loved the Bill arc
"""


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    rev = tmp_path / "hcmc" / "reviews"
    (rev / "2026").mkdir(parents=True)
    (rev / "2026" / "not-till-we-are-lost.md").write_text(REVIEW)
    notes = tmp_path / "z_ibx" / "new-notes.md"
    notes.parent.mkdir()
    notes.write_text("note: DO NOT delete this doc\n\n")
    monkeypatch.setattr(jmreads, "REVIEWS_DIR", rev)
    monkeypatch.setattr(jmreads, "BLOG_REVIEWS_DIR", tmp_path / "blog")
    monkeypatch.setattr(jmreads, "NEW_NOTES", notes)
    return tmp_path


def test_view_review_returns_the_body_read_only(vault):
    ok, r = jmreads.view_review("not-till-we-are-lost")
    assert ok and r["title"] == "Not Till We Are Lost" and r["score"] == 4
    assert r["body"].startswith("Bobs in space again.") and "> draft" in r["body"]
    assert (vault / "hcmc/reviews/2026/not-till-we-are-lost.md").read_text() == REVIEW


def test_view_review_rejects_paths(vault):
    assert jmreads.view_review("../../etc/passwd") == (False, "unknown book ../../etc/passwd")


def test_write_review_seeds_new_notes_once_with_an_iw01_route(vault):
    ok, url = jmreads.request_review("not-till-we-are-lost")
    assert ok and url.startswith("obsidian://open?vault=vault&file=z_ibx")
    jmreads.request_review("not-till-we-are-lost")          # second click: no duplicate heading
    text = (vault / "z_ibx/new-notes.md").read_text()
    head = "## Not Till We Are Lost review iw01 -> hcmc/reviews/2026/not-till-we-are-lost"
    assert text.count(head) == 1 and text.startswith("note: DO NOT delete")


def test_page_has_view_button_and_card_ratings():
    html = jmreads.PAGE
    assert 'class="view-btn"' in html and "/jmreads/view/" in html
    assert "${b.score ? `<div class=\"a\">${stars(b.score)}</div>` : ''}" in html   # bookbox rating
    assert "${cover(b, 'sm')}${stars(b.score)}" in html                            # shelf rating
