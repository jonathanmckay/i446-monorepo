"""jmreads stories (2026-11-21): the Economist "World in Brief" email parsed
into a feed story and merged into /api/reads without disturbing book events."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import jmreads  # noqa: E402
import jmreads_stories as js  # noqa: E402

SAMPLE = """
 <link rel=stylesheet href=https://cdn.example.com/font-face.css>
 The Economist

https://click.e.example.com/?qs=AAA

November 21st 2026

The World in Brief

Photo: AP

Today&rsquo;s top stories

 ▸ Widgets entered a bear market. Acme’s
https://click.e.example.com/?qs=BBB
share price  fell by 8%.

 ▸ A second story about “
https://click.e.example.com/?qs=CCC
quoted things ” here.

 Word of the week

 Axis, our term for a thing.

 Financial markets

 S&P 500

7,175.8

 -2.0% ↓

 Topix

3,674.2

 -3.9% ↓

 Data at Nov 20th 2026 | Sources: X

Today’s agenda

AP PHOTO/SOME NAME

After the summit

 They came, they met.

Second paragraph.

 Expand your mind with our science newsletter
 Our correspondents explain
 Sign Up

A MESSAGE FROM ECONOMIST EDUCATION
 Save 25%

 This email has been sent to someone@example.com
"""


def _store(d: Path, day: str, raw: str = SAMPLE, title: str = "Widgets plunge"):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"economist-{day}.json").write_text(json.dumps(
        {"title": title, "date": day, "gmail_id": "g" + day, "url": "https://mail.example/x", "raw": raw}))


def test_parse_brief_bullets_headings_and_noise():
    r = js.parse_brief(SAMPLE)
    assert r["bullets"] == ["Widgets entered a bear market. Acme’s share price fell by 8%.",
                            "A second story about “quoted things” here."]
    paras = r["body"].split("\n\n")
    assert paras[0] == "## Today’s top stories"
    assert "S&P 500 7,175.8 -2.0% ↓" in paras and "Topix 3,674.2 -3.9% ↓" in paras
    assert "## After the summit" in paras and "They came, they met." in paras
    body = r["body"]
    for noise in ("http", "AP PHOTO", "Photo:", "Sign Up", "MESSAGE FROM", "example.com", "<link"):
        assert noise not in body, noise


def test_unrecognised_layout_yields_no_bullets():
    assert js.parse_brief("Hello\n\nA redesigned newsletter")["bullets"] == []
    assert js.parse_brief("")["bullets"] == []


def test_load_stories_window_order_and_bad_files(tmp_path):
    _store(tmp_path, "2026-11-21")
    _store(tmp_path, "2026-11-19")
    _store(tmp_path, "2026-11-01")                       # outside the 7-day window
    _store(tmp_path, "2026-11-20", raw="no known layout")  # unparsed: hidden, not a blank card
    (tmp_path / "economist-junk.json").write_text("{not json")
    got = js.load_stories(today=date(2026, 11, 21), stories_dir=tmp_path)
    assert [s["date"] for s in got] == ["2026-11-21", "2026-11-19"]
    assert got[0]["id"] == "economist-2026-11-21" and got[0]["body"] and len(got[0]["bullets"]) == 2


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
    assert d["feed"][0]["story"]["body"]                 # inline: readable offline
    assert d["feed"][1]["book"]["title"] == "A Book"


def test_build_without_stories_dir_is_books_only(tmp_path):
    d = jmreads.build(today=date(2026, 11, 21), reviews_dir=tmp_path, blog_dir=tmp_path,
                      covers=False, stories_dir=tmp_path / "missing")
    assert d["feed"] == []


class _Svc:
    """Minimal Gmail client double: users().messages().list/get/modify().execute()."""
    def __init__(self, msgs): self.msgs, self.modified = msgs, []
    def users(self): return self
    def messages(self): return self
    def list(self, **kw): self._r = {"messages": [{"id": i} for i in self.msgs]}; return self
    def get(self, id, **kw): self._r = self.msgs[id]; return self
    def modify(self, id, body, **kw): self.modified.append((id, body)); self._r = {}; return self
    def execute(self): return self._r


def _msg(mid, raw, labels):
    import base64
    return {"id": mid, "internalDate": "1795258952000", "labelIds": labels, "payload": {
        "mimeType": "multipart/alternative",
        "headers": [{"name": "Subject", "value": "The World in Brief: Widgets plunge"}],
        "parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(raw.encode()).decode()}},
                  {"mimeType": "text/html", "body": {"data": ""}}]}}


def test_fetch_stores_archives_and_is_idempotent(tmp_path):
    svc = _Svc({"m1": _msg("m1", SAMPLE, ["INBOX", "UNREAD", "CATEGORY_UPDATES"])})
    assert js.fetch(stories_dir=tmp_path, service=svc) == 0
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text())
    assert rec["title"] == "Widgets plunge" and rec["gmail_id"] == "m1" and "top stories" in rec["raw"]
    assert svc.modified == [("m1", {"removeLabelIds": ["INBOX", "UNREAD"]})]
    js.fetch(stories_dir=tmp_path, service=svc)          # second run: nothing new, no second modify
    assert len(svc.modified) == 1


def test_fetch_leaves_unparsed_mail_alone(tmp_path):
    svc = _Svc({"m2": _msg("m2", "A redesigned newsletter", ["INBOX", "UNREAD"])})
    js.fetch(stories_dir=tmp_path, service=svc)
    assert not list(tmp_path.glob("*.json")) and svc.modified == []


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
