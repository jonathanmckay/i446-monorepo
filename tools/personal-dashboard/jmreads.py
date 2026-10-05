"""jmreads: a Goodreads-style reading page for the personal dashboard.

Data source: the hcmc reviews database (~/vault/hcmc/reviews/<year>/*.md),
which is what the o315 blog publishes from. Each file's YAML frontmatter is a
book (or film); /book and /bookreview write these files. The o315 blog's own
content/reviews/ is used to link a published review.

Read-only. Served by dashboard.py at /jmreads (page) and /api/reads (JSON).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

REVIEWS_DIR = Path.home() / "vault" / "hcmc" / "reviews"
BLOG_REVIEWS_DIR = Path.home() / "vault" / "hcmp" / "o315" / "blog" / "content" / "reviews"
BLOG_URL = "https://jonathanmckay.com/reviews/{slug}/"
COVER_CACHE = Path.home() / ".cache" / "jmreads-covers.json"

# Annual book goal for the reading challenge. Placeholder until JM sets it.
READING_GOALS = {2026: 52}
DEFAULT_GOAL = 52

FEED_LIMIT = 60
API_TTL = 600            # seconds; the vault changes a few times a day at most
COVER_LOOKUPS_PER_CALL = 12
COVER_MISS_RETRY_DAYS = 30

_cache: dict = {"t": 0.0, "data": None}
_cache_lock = threading.Lock()
_cover_lock = threading.Lock()


# --- parsing -----------------------------------------------------------------

def _parse_scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def parse_file(path: Path) -> tuple[dict, str] | None:
    """Minimal frontmatter reader: flat `key: value` lines are all this
    database uses for the fields jmreads needs (lists like tags/related are
    skipped). Returns (frontmatter, body) or None if there is no frontmatter."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    fm: dict = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and m.group(2) and not m.group(2).startswith("["):
            fm[m.group(1)] = _parse_scalar(m.group(2))
    body = text[end + 4:].lstrip("\n")
    return fm, body


def _date(v) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def review_excerpt(body: str, limit: int = 320) -> str:
    """Plain-text start of the review. Drops the stub's 'Subjects:' line, the
    bold title line that opens every /bookreview review, and markdown."""
    lines = []
    for raw in body.splitlines():
        s = raw.strip()
        if not s or s.startswith("Subjects:") or s.startswith("<!--") or s.startswith(">"):
            continue
        if re.fullmatch(r"\*\*[^*]+\*\*", s):      # the bold title line
            continue
        if s.startswith("#"):
            continue
        lines.append(s)
    text = " ".join(lines)
    text = re.sub(r"!\[\[[^\]]*\]\]", "", text)
    text = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def headline(body: str) -> str:
    m = re.search(r"^\s*\*\*([^*]+)\*\*\s*$", body, re.M)
    return m.group(1).strip() if m else ""


# --- covers ------------------------------------------------------------------

def _load_cover_cache() -> dict:
    try:
        return json.loads(COVER_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _save_cover_cache(c: dict) -> None:
    try:
        COVER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = COVER_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(c))
        tmp.replace(COVER_CACHE)
    except OSError:
        pass


def _search_cover(title: str, author: str) -> str | None:
    q = {"title": re.sub(r":.*$", "", title).strip(), "limit": "3", "fields": "cover_i"}
    if author:
        q["author"] = author.split(",")[0].strip()
    url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "jmreads/1.0 (personal dashboard)"})
    with urllib.request.urlopen(req, timeout=4) as r:
        docs = json.load(r).get("docs", [])
    for d in docs:
        if d.get("cover_i"):
            return f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg"
    return None


def resolve_covers(books: list[dict]) -> None:
    """Fill book['cover']. ISBN → Open Library cover URL directly (the page
    falls back to a placeholder if OL has no image). No ISBN → a cached
    title/author search, a bounded number of new lookups per call."""
    with _cover_lock:
        cache = _load_cover_cache()
        todo = []
        now = time.time()
        for b in books:
            if b.get("isbn"):
                b["cover"] = f"https://covers.openlibrary.org/b/isbn/{b['isbn']}-M.jpg?default=false"
                continue
            key = f"{b['title']}|{b.get('author', '')}".lower()
            hit = cache.get(key)
            if hit and (hit.get("url") or now - hit.get("t", 0) < COVER_MISS_RETRY_DAYS * 86400):
                b["cover"] = hit.get("url")
            else:
                b["cover"] = None
                todo.append((key, b))
        todo = todo[:COVER_LOOKUPS_PER_CALL]
        if todo:
            def look(item):
                key, b = item
                try:
                    return key, b, _search_cover(b["title"], b.get("author", ""))
                except Exception:
                    return key, b, None
            with ThreadPoolExecutor(max_workers=6) as ex:
                for key, b, url in ex.map(look, todo):
                    cache[key] = {"url": url, "t": now}
                    b["cover"] = url
            _save_cover_cache(cache)


# --- model -------------------------------------------------------------------

def load_books(reviews_dir: Path = REVIEWS_DIR, blog_dir: Path = BLOG_REVIEWS_DIR) -> list[dict]:
    books = []
    for path in sorted(reviews_dir.glob("*/*.md")):
        if not re.fullmatch(r"\d{4}", path.parent.name):
            continue
        parsed = parse_file(path)
        if not parsed:
            continue
        fm, body = parsed
        # Only real entries: year/folder index notes ("Reviews — 2099") are
        # type: index, and films/other media live in the same folders.
        if fm.get("type") != "review" or fm.get("media", "book") != "book":
            continue
        title = str(fm.get("title") or path.stem)
        slug = str(fm.get("slug") or path.stem)
        published = (blog_dir / f"{slug}.md").exists() and fm.get("draft") is not True
        excerpt = review_excerpt(body)
        hl = headline(body)
        # A stub's title is the book; a review's title is "<Title Line>: <Book>".
        book_title = title
        if hl and title.startswith(hl.rstrip(".?!")):
            book_title = title[len(hl.rstrip(".?!")):].lstrip(":?! ").strip() or title
        books.append({
            "id": path.stem,
            "title": book_title,
            "headline": hl,
            "author": str(fm.get("author") or ""),
            "isbn": str(fm.get("isbn") or ""),
            "pages": fm.get("pages") if isinstance(fm.get("pages"), int) else None,
            "score": fm.get("score") if isinstance(fm.get("score"), int) else None,
            "status": str(fm.get("status") or ""),
            "source": str(fm.get("source") or ""),
            "date": _date(fm.get("date")),
            "finished": _date(fm.get("finished")),
            "series": str(fm.get("series") or ""),
            "series_number": fm.get("series_number"),
            "excerpt": excerpt if len(excerpt) > 40 else "",
            "url": BLOG_URL.format(slug=slug) if published else None,
            "goodreads": fm.get("goodreads_review"),
        })
    return books


def read_date(b: dict) -> date | None:
    """When the book counts as read, or None if it hasn't been."""
    if b["status"] == "reading":
        return None
    return b["finished"] or b["date"]


def build_events(books: list[dict]) -> list[dict]:
    ev = []
    for b in books:
        d, fin = b["date"], b["finished"]
        if b["source"] == "openlibrary":
            # Created by /book: the date is when it was added / started.
            if d:
                ev.append({"type": "started", "date": d, "book": b})
            if fin:
                ev.append({"type": "reviewed" if b["excerpt"] or b["score"] else "finished",
                           "date": fin, "book": b})
        elif d:
            # Written by /bookreview or imported: one event, read + rated/reviewed.
            ev.append({"type": "reviewed" if b["excerpt"] or b["score"] else "finished",
                       "date": d, "book": b})
    ev.sort(key=lambda e: (e["date"], {"started": 0, "finished": 1, "reviewed": 2}[e["type"]]),
            reverse=True)
    return ev


def build(today: date | None = None, reviews_dir: Path = REVIEWS_DIR,
          blog_dir: Path = BLOG_REVIEWS_DIR, covers: bool = True) -> dict:
    today = today or date.today()
    books = load_books(reviews_dir, blog_dir)
    year = today.year
    read = [b for b in books if (rd := read_date(b)) and rd.year == year]
    read.sort(key=lambda b: read_date(b), reverse=True)
    reading = sorted([b for b in books if b["status"] == "reading"],
                     key=lambda b: b["date"] or date.min, reverse=True)
    events = build_events(books)[:FEED_LIMIT]

    shown = {id(b) for b in reading} | {id(b) for b in read} | {id(e["book"]) for e in events}
    if covers:
        resolve_covers([b for b in books if id(b) in shown])

    goal = READING_GOALS.get(year, DEFAULT_GOAL)
    day_of_year = (today - date(year, 1, 1)).days + 1
    days_in_year = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    expected = goal * day_of_year / days_in_year
    rated = [b["score"] for b in read if b["score"]]

    def card(b):
        return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in b.items()}

    return {
        "year": year,
        "challenge": {
            "goal": goal,
            "read": len(read),
            "pct": round(100 * len(read) / goal) if goal else 0,
            "ahead": round(len(read) - expected),
            "pages": sum(b["pages"] or 0 for b in read),
            "avg_rating": round(sum(rated) / len(rated), 1) if rated else None,
            "reviews": sum(1 for b in read if b["excerpt"]),
        },
        "reading": [card(b) for b in reading],
        "read": [card(b) for b in read],
        "feed": [{"type": e["type"], "date": e["date"].isoformat(), "book": card(e["book"])}
                 for e in events],
    }


def data(force: bool = False) -> dict:
    with _cache_lock:
        if not force and _cache["data"] and time.time() - _cache["t"] < API_TTL:
            return _cache["data"]
    d = build()
    with _cache_lock:
        _cache.update(t=time.time(), data=d)
    return d


def invalidate() -> None:
    with _cache_lock:
        _cache.update(t=0.0, data=None)


# --- page --------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html>
<head>
<title>jmreads</title>
__SHARED_STYLE__
<style>
.reads { display: grid; grid-template-columns: 300px minmax(0, 1fr) 260px; gap: 32px; align-items: start; }
@media (max-width: 1100px) { .reads { grid-template-columns: 280px minmax(0, 1fr); } .shelf-col { display: none; } }
@media (max-width: 760px) { .reads { grid-template-columns: 1fr; } }
.serif { font-family: Georgia, 'Times New Roman', serif; }
.cover { width: 72px; height: 108px; object-fit: cover; border-radius: 3px; background: var(--badge-bg); flex: none;
         display: flex; align-items: center; justify-content: center; text-align: center; font-size: 9px; color: var(--h2);
         padding: 4px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.35); }
.cover.lg { width: 96px; height: 144px; }
.cover.sm { width: 54px; height: 81px; }
.book-row { display: flex; gap: 14px; margin-bottom: 18px; }
.book-row .t { font-family: Georgia, serif; font-size: 15px; font-weight: bold; line-height: 1.3; }
.book-row .a { font-size: 12px; color: var(--h2); margin-top: 4px; }
.challenge .big { font-family: Georgia, serif; font-size: 20px; line-height: 1.4; }
.bar { height: 10px; background: var(--badge-bg); border-radius: 5px; overflow: hidden; margin: 10px 0 6px; }
.bar > div { height: 100%; background: #9a8b6c; }
.stat-line { font-size: 12px; color: var(--h2); line-height: 1.8; }
.ev { display: flex; gap: 14px; padding: 18px 0; border-bottom: 1px solid var(--grid); }
.ev:last-child { border-bottom: none; }
.ev .who { font-size: 13px; line-height: 1.5; }
.ev .who b { font-weight: bold; }
.ev .when { font-size: 11px; color: var(--h2); white-space: nowrap; margin-left: auto; padding-left: 12px; }
.ev .head { display: flex; align-items: baseline; }
.avatar { width: 36px; height: 36px; border-radius: 50%; background: var(--nav); color: var(--nav-text); flex: none;
          display: flex; align-items: center; justify-content: center; font-size: 12px; letter-spacing: 1px; }
.stars { color: #e87400; letter-spacing: 1px; font-size: 14px; }
.stars .off { color: var(--tick); }
.ev .excerpt { font-family: Georgia, serif; font-size: 14px; line-height: 1.6; margin: 8px 0; }
.ev .hl { font-family: Georgia, serif; font-weight: bold; font-size: 15px; margin-top: 6px; }
.bookbox { display: flex; gap: 14px; border: 1px solid var(--grid); border-radius: 6px; padding: 12px; margin-top: 10px; }
.bookbox .t { font-family: Georgia, serif; font-size: 16px; font-weight: bold; }
.bookbox .a { font-size: 12px; color: var(--h2); margin-top: 4px; }
.links a { font-size: 12px; color: var(--nav-text); text-decoration: none; border-bottom: 1px dotted var(--nav-text); margin-right: 10px; }
.shelf { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
.shelf > div { min-width: 0; }
.shelf .cover { width: 100%; height: auto; aspect-ratio: 2 / 3; display: block; font-size: 7px; }
.muted { color: var(--h2); font-size: 12px; }
</style>
</head>
<body>
<div class="topbar">
  <h1>JM · READS</h1>
  <span>
    <a class="nav-link" href="/more">MORE</a>
    <a class="nav-link" href="/">← MAIN</a>
  </span>
</div>

<div class="reads">
  <div>
    <div class="card" style="margin-bottom:24px">
      <h2>Currently Reading</h2>
      <div id="reading"><div class="muted">loading…</div></div>
    </div>
    <div class="card challenge">
      <h2 id="chTitle">Reading Challenge</h2>
      <div id="challenge"><div class="muted">loading…</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Updates</h2>
    <div id="feed"><div class="muted">loading…</div></div>
  </div>

  <div class="card shelf-col">
    <h2 id="shelfTitle">Read This Year</h2>
    <div class="shelf" id="shelf"></div>
  </div>
</div>

<script>
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function cover(b, cls) {
  const label = `<div class="cover ${cls||''} serif">${esc(b.title)}</div>`;
  if (!b.cover) return label;
  // Open Library returns 404 (default=false) when it has no image: swap in the text cover.
  return `<img class="cover ${cls||''}" loading="lazy" src="${esc(b.cover)}" alt="${esc(b.title)}"
           onerror="this.outerHTML=${esc(JSON.stringify(label))}">`;
}
function stars(n) {
  if (!n) return '';
  return '<span class="stars">' + '★'.repeat(n) + '<span class="off">' + '★'.repeat(5 - n) + '</span></span>';
}
function ago(iso) {
  const d = new Date(iso + 'T12:00:00'), now = new Date();
  const days = Math.round((now - d) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 7) return days + 'd';
  if (days < 60) return Math.round(days / 7) + 'w';
  return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: now.getFullYear() === d.getFullYear() ? undefined : 'numeric'});
}
function series(b) { return b.series ? ` <span class="muted">(${esc(b.series)}${b.series_number ? ', #' + esc(b.series_number) : ''})</span>` : ''; }

fetch('/api/reads').then(r => r.json()).then(d => {
  // Currently reading
  document.getElementById('reading').innerHTML = d.reading.length ? d.reading.map(b => `
    <div class="book-row">${cover(b)}
      <div><div class="t">${esc(b.title)}</div><div class="a">by ${esc(b.author)}</div>
      <div class="a">since ${esc(ago(b.date))}${b.pages ? ' · ' + b.pages + ' pages' : ''}</div></div>
    </div>`).join('') : '<div class="muted">Nothing in progress. /book started &lt;title&gt;</div>';

  // Challenge
  const c = d.challenge;
  document.getElementById('chTitle').textContent = d.year + ' Reading Challenge';
  const pace = c.ahead === 0 ? 'right on schedule' : (c.ahead > 0 ? `${c.ahead} book${c.ahead === 1 ? '' : 's'} ahead of schedule` : `${-c.ahead} book${c.ahead === -1 ? '' : 's'} behind schedule`);
  document.getElementById('challenge').innerHTML = `
    <div class="big">You've read <b>${c.read}</b> of ${c.goal} books</div>
    <div class="bar"><div style="width:${Math.min(100, c.pct)}%"></div></div>
    <div class="stat-line">${c.read}/${c.goal} (${c.pct}%) · ${esc(pace)}</div>
    <div class="stat-line">${c.pages.toLocaleString()} pages logged · ${c.reviews} reviews written${c.avg_rating ? ' · avg ' + c.avg_rating + '★' : ''}</div>`;

  // Feed
  const verb = {started: 'started reading', finished: 'finished', reviewed: 'reviewed'};
  document.getElementById('feed').innerHTML = d.feed.map(e => {
    const b = e.book;
    const rating = e.type === 'reviewed' && b.score ? `<div>Rating ${stars(b.score)}</div>` : '';
    const hl = e.type === 'reviewed' && b.headline ? `<div class="hl">${esc(b.headline)}</div>` : '';
    const ex = e.type === 'reviewed' && b.excerpt ? `<div class="excerpt">${esc(b.excerpt)}</div>` : '';
    const links = [b.url ? `<a href="${esc(b.url)}" target="_blank">Read review</a>` : '',
                   b.goodreads ? `<a href="${esc(b.goodreads)}" target="_blank">Goodreads</a>` : ''].join('');
    return `<div class="ev">
      <div class="avatar">JM</div>
      <div style="flex:1; min-width:0">
        <div class="head"><div class="who"><b>Jonathan</b> ${verb[e.type]} <b class="serif">${esc(b.title)}</b></div>
          <div class="when">${esc(ago(e.date))}</div></div>
        ${rating}${hl}${ex}
        <div class="bookbox">${cover(b, 'lg')}
          <div><div class="t">${esc(b.title)}${series(b)}</div><div class="a">by ${esc(b.author)}</div>
          ${b.pages ? `<div class="a">${b.pages} pages</div>` : ''}
          <div class="links" style="margin-top:10px">${links}</div></div>
        </div>
      </div>
    </div>`;
  }).join('') || '<div class="muted">No updates yet.</div>';

  // Shelf
  document.getElementById('shelfTitle').textContent = 'Read in ' + d.year + ' (' + d.read.length + ')';
  document.getElementById('shelf').innerHTML = d.read.map(b => `<div title="${esc(b.title)}">${cover(b, 'sm')}</div>`).join('');
}).catch(err => {
  document.getElementById('feed').innerHTML = '<div class="muted">Failed to load /api/reads: ' + esc(err) + '</div>';
});
</script>
</body>
</html>
"""


def page(shared_style: str) -> str:
    return PAGE.replace("__SHARED_STYLE__", shared_style)
