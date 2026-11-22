"""jmreads stories (2026-11-21): the Chinese daily brief written by
~/code/x953-hcmc0 (feed_writer.py) merged into /api/reads beside book events."""
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import jmreads  # noqa: E402
import jmreads_stories as js  # noqa: E402


def _store(d: Path, day: str, **over):
    rec = {"source": "The Economist", "kind": "世界简讯", "title": "科技股跌入熊市", "date": day,
           "sections": [
               {"name": "财新", "sub": "Caixin", "items": [
                   {"title": "财政收入转增", "text": "摘要", "url": "https://caixin.example/a"},
                   {"title": "坏链接", "text": "x", "url": "javascript:alert(1)"}]},
               {"name": "The Economist", "sub": "世界简讯", "lead": True, "items": [
                   {"title": "英伟达", "text": "股价下跌了14%。", "url": ""},
                   {"title": "数学家", "text": "声称解决了难题。", "url": ""}]},
               {"name": "الشرق الأوسط", "sub": "Asharq", "rtl": True, "items": [
                   {"title": "عنوان", "text": "نص", "url": "https://aawsat.example/1"}]}],
           "links": [{"label": "Anki", "url": "https://example.com/anki"}, {"label": "bad", "url": "ankimobile://x"}]}
    rec.update(over)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"brief-{day}.json"
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_stories_shape_and_link_sanitising(tmp_path):
    _store(tmp_path, "2026-11-21")
    s = js.load_stories(today=date(2026, 11, 21), stories_dir=tmp_path)[0]
    assert s["id"] == "brief-2026-11-21" and s["title"] == "科技股跌入熊市"
    assert [x["name"] for x in s["sections"]] == ["财新", "The Economist", "الشرق الأوسط"]
    assert s["sections"][1]["lead"] and s["sections"][2]["rtl"] and not s["sections"][0]["rtl"]
    assert s["sections"][0]["items"][0]["url"] == "https://caixin.example/a"
    assert s["sections"][0]["items"][1]["url"] == ""           # javascript: never reaches an href
    assert s["links"] == [{"label": "Anki", "url": "https://example.com/anki"}]


def test_load_stories_window_order_and_bad_files(tmp_path):
    _store(tmp_path, "2026-11-21")
    _store(tmp_path, "2026-11-19")
    _store(tmp_path, "2026-11-01")                       # outside the 7-day window
    _store(tmp_path, "2026-11-20", sections=[])          # empty brief: hidden, not a blank card
    _store(tmp_path, "2026-11-18", date="not-a-date")
    (tmp_path / "brief-junk.json").write_text("{not json")
    (tmp_path / "brief-list.json").write_text("[1, 2]")
    got = js.load_stories(today=date(2026, 11, 21), stories_dir=tmp_path)
    assert [s["date"] for s in got] == ["2026-11-21", "2026-11-19"]


def test_title_falls_back_to_english_then_kind(tmp_path):
    _store(tmp_path, "2026-11-21", title="", title_en="Tech stocks plunge")
    _store(tmp_path, "2026-11-20", title="")
    got = js.load_stories(today=date(2026, 11, 21), stories_dir=tmp_path)
    assert [s["title"] for s in got] == ["Tech stocks plunge", "世界简讯"]


def test_old_files_are_pruned(tmp_path):
    old = _store(tmp_path, "2026-10-01")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    js.load_stories(today=date(2026, 11, 21), stories_dir=tmp_path)
    assert not old.exists()


def test_build_merges_stories_with_book_events(tmp_path):
    reviews = tmp_path / "reviews" / "2026"
    reviews.mkdir(parents=True)
    (reviews / "a-book.md").write_text(
        "---\ntitle: A Book\nauthor: Someone\ntype: review\nmedia: book\nsource: openlibrary\n"
        "status: reading\ndate: 2026-11-21\n---\n")
    _store(tmp_path / "stories", "2026-11-21")
    _store(tmp_path / "stories", "2026-11-20")
    d = jmreads.build(today=date(2026, 11, 21), reviews_dir=tmp_path / "reviews",
                      blog_dir=tmp_path / "blog", covers=False, stories_dir=tmp_path / "stories")
    json.dumps(d)                                        # still serialisable
    assert [(e["type"], e["date"]) for e in d["feed"]] == [
        ("story", "2026-11-21"), ("started", "2026-11-21"), ("story", "2026-11-20")]
    assert d["feed"][0]["story"]["sections"][1]["items"][0]["text"]   # inline: readable offline
    assert d["feed"][1]["book"]["title"] == "A Book"


def test_build_without_stories_dir_is_books_only(tmp_path):
    d = jmreads.build(today=date(2026, 11, 21), reviews_dir=tmp_path, blog_dir=tmp_path,
                      covers=False, stories_dir=tmp_path / "missing")
    assert d["feed"] == []


def test_x953_feed_writer_output_loads(tmp_path):
    """Contract with the producer: what ~/code/x953-hcmc0/feed_writer.py writes
    is what load_stories reads. Skipped where that repo is not checked out."""
    import pytest
    x953 = Path.home() / "code" / "x953-hcmc0"
    if not (x953 / "feed_writer.py").exists():
        pytest.skip("x953-hcmc0 not on this host")
    sys.path.insert(0, str(x953))
    import feed_writer
    story = feed_writer.build_story(
        [{"title": "英伟达", "content": "股价下跌。", "url": ""}],
        caixin_articles=[{"title": "财新题", "content": "摘要", "url": "https://caixin.example/a"}],
        asharq_articles=[{"title": "عنوان", "content": "نص", "url": "https://aawsat.example/1"}],
        title="科技股跌入熊市", title_en="Tech stocks plunge", day=date(2026, 11, 21))
    feed_writer.write_story(story, out_dir=tmp_path)
    s = js.load_stories(today=date(2026, 11, 21), stories_dir=tmp_path)[0]
    assert s["title"] == "科技股跌入熊市" and s["kind"] == "世界简讯"
    lead = [x for x in s["sections"] if x["lead"]]
    assert len(lead) == 1 and lead[0]["items"][0] == {"title": "英伟达", "text": "股价下跌。", "url": ""}
    assert s["sections"][-1]["rtl"] and s["links"][0]["url"].startswith("https://")


def test_page_script_parses(tmp_path):
    """Regression (2026-11-21): PAGE is a plain Python string, so a JS `\\n`
    written with one backslash became a real newline inside a regex literal
    and the whole /jmreads script died with a SyntaxError (since 2026-11-04)."""
    import re
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    scripts = re.findall(r"<script>(.*?)</script>", jmreads.page(""), re.S)
    assert scripts
    f = tmp_path / "page.js"
    f.write_text("\n;\n".join(scripts), encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
