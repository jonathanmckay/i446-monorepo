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
TO_READ_FILE = Path.home() / "vault" / "hcmc" / "to-read.md"

# Annual book goal for the reading challenge. Placeholder until JM sets it.
READING_GOALS = {2026: 60}   # matches the Goodreads 2026 challenge (2026-10-05)
DEFAULT_GOAL = 52

FEED_LIMIT = 60
API_TTL = 600            # seconds; the vault changes a few times a day at most
COVER_MISS_RETRY_DAYS = 7
SYNC_COVER_LIMIT = 5        # new covers fetched inline before responding

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
#
# Covers are resolved server-side and cached on disk, then served from
# /jmreads/cover/<key>, so the page never waits on Open Library's slow
# redirects. Lookup order per book: Open Library by ISBN, Google Books by
# ISBN, Open Library title+author search, Google Books title+author search.
# A background thread fills misses so /api/reads never blocks on the network.

COVER_DIR = Path.home() / ".cache" / "jmreads-covers"
_UA = {"User-Agent": "jmreads/1.0 (personal dashboard)"}
_filling = threading.Event()


def cover_key(b: dict) -> str:
    import hashlib
    raw = (b.get("isbn") or f"{b['title']}|{b.get('author', '')}").lower()
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def cover_path(key: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{16}", key or ""):
        return None
    p = COVER_DIR / f"{key}.jpg"
    return p if p.exists() else None


def _get(url: str, timeout: float = 6.0) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def _json(url: str):
    raw = _get(url)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _image(url: str | None) -> bytes | None:
    """Download and sanity-check an image (real covers are several KB)."""
    if not url:
        return None
    data = _get(url, timeout=10)
    if data and len(data) > 2500 and data[:3] in (b"\xff\xd8\xff", b"\x89PN", b"GIF", b"RIF"):
        return data
    return None


def _gbooks_thumb(query: str) -> str | None:
    d = _json("https://www.googleapis.com/books/v1/volumes?maxResults=5&q=" + urllib.parse.quote(query))
    for item in (d or {}).get("items", []):
        links = item.get("volumeInfo", {}).get("imageLinks", {})
        url = links.get("thumbnail") or links.get("smallThumbnail")
        if url:
            url = url.replace("http://", "https://").replace("&edge=curl", "")
            return re.sub(r"zoom=\d", "zoom=1", url)
    return None


def _ol_search(title: str, author: str) -> str | None:
    q = {"title": re.sub(r":.*$", "", title).strip(), "limit": "5", "fields": "cover_i"}
    if author:
        q["author"] = author.split(",")[0].strip()
    d = _json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(q))
    for doc in (d or {}).get("docs", []):
        if doc.get("cover_i"):
            return f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-M.jpg"
    return None


def _isbn10(isbn: str) -> str | None:
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "")
    if len(isbn) == 10:
        return isbn.upper()
    if len(isbn) == 13 and isbn.startswith("978"):
        core = isbn[3:12]
        ck = (11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11) % 11
        return core + ("X" if ck == 10 else str(ck))
    return None


def _amazon(isbn: str) -> str | None:
    """Amazon's public cover image by ISBN-10. A missing cover comes back as a
    tiny placeholder, which _image()'s size check rejects."""
    i10 = _isbn10(isbn)
    return f"https://images-na.ssl-images-amazon.com/images/P/{i10}.01.LZZZZZZZ.jpg" if i10 else None


def _ol_isbns(title: str, author: str) -> list[str]:
    """ISBNs of editions matching title AND author (never title alone)."""
    if not author:
        return []
    q = {"title": title, "author": author, "limit": "3", "fields": "isbn"}
    d = _json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(q))
    out: list[str] = []
    for doc in (d or {}).get("docs", []):
        for i in doc.get("isbn", []):
            if _isbn10(i) and i not in out:
                out.append(i)
    return out[:6]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _itunes(title: str, author: str) -> str | None:
    """Apple Books (ebook, then audiobook) cover, matched on title AND author.
    Finds covers Open Library and Amazon lack: new fiction, Great Courses
    lectures, photo books (2026-10-05: Teo's Durumi, Understanding Japan,
    Dune: Exposures, Dad Brain, The Virtues all hit)."""
    if not author:
        return None
    last = _norm(author).split()[-1]
    want = _norm(title)
    for media in ("ebook", "audiobook"):
        q = urllib.parse.urlencode({"media": media, "limit": "5", "term": f"{title} {author}"})
        d = _json("https://itunes.apple.com/search?" + q)
        for x in (d or {}).get("results", []):
            name = _norm(x.get("trackName") or x.get("collectionName"))
            if last in _norm(x.get("artistName")) and (name.startswith(want) or want.startswith(name)):
                art = x.get("artworkUrl100") or ""
                if art:
                    return re.sub(r"/\d+x\d+bb\.", "/600x600bb.", art)
    return None


def fetch_cover(b: dict) -> tuple[bytes | None, str]:
    title = re.sub(r":.*$", "", b["title"]).strip()
    author = (b.get("author") or "").split(",")[0].strip()
    isbn = b.get("isbn")
    steps = []
    if isbn:
        steps.append(("openlibrary-isbn", lambda: f"https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false"))
        steps.append(("amazon-isbn", lambda: _amazon(isbn)))
        steps.append(("google-isbn", lambda: _gbooks_thumb(f"isbn:{isbn}")))
    # Title variants: the part before a colon (subtitle dropped), the part
    # after it (review titles are "<headline>: <book>"), and with any
    # "(Series, #n)" suffix removed.
    variants = []
    for t in (b["title"], b["title"].split(":", 1)[-1], re.sub(r"\s*\([^)]*#\d+\)\s*$", "", b["title"])):
        t = re.sub(r":.*$", "", t).strip()
        if t and t not in variants:
            variants.append(t)
    for t in variants:
        steps.append(("openlibrary-search", lambda t=t: _ol_search(t, b.get("author", ""))))
    # Editions found by title+author, tried on Amazon (covers OL lacks).
    for t in variants:
        steps.append(("amazon-ol-edition", lambda t=t: next(
            (u for u in (_amazon(i) for i in _ol_isbns(t, author)) if u and _image(u)), None)))
    for t in variants:
        steps.append(("apple-books", lambda t=t: _itunes(t, author)))
    # Last resort, by hand: Goodreads blocks scripted downloads, so a cover
    # only Goodreads has is captured from the book page in Chrome and saved
    # into COVER_DIR with src "goodreads-capture" (done for Understanding
    # Japan, 2026-10-05).
    # No title-only search: without the author it matched the wrong book 4 of 4
    # times (2026-10-05). A text card beats a wrong cover.
    steps.append(("google-search", lambda: _gbooks_thumb(
        f'intitle:"{title}"' + (f' inauthor:"{author}"' if author else ""))))
    for name, url_fn in steps:
        try:
            data = _image(url_fn())
        except Exception:
            data = None
        if data:
            return data, name
    return None, "none"


def _index() -> dict:
    try:
        return json.loads((COVER_DIR / "index.json").read_text())
    except (OSError, ValueError):
        return {}


def _save_index(idx: dict) -> None:
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = COVER_DIR / "index.json.tmp"
    tmp.write_text(json.dumps(idx, indent=0))
    tmp.replace(COVER_DIR / "index.json")


def fill_covers(books: list[dict], limit: int | None = None) -> dict:
    """Download covers for books that don't have one yet. Misses are retried
    after COVER_MISS_RETRY_DAYS. Returns counts by source."""
    with _cover_lock:
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        idx = _index()
        now = time.time()
        todo = []
        for b in books:
            k = cover_key(b)
            if cover_path(k):
                continue
            hit = idx.get(k)
            if hit and now - hit.get("t", 0) < COVER_MISS_RETRY_DAYS * 86400:
                continue
            todo.append((k, b))
        if limit:
            todo = todo[:limit]
        counts: dict = {}

        def work(item):
            k, b = item
            data, src = fetch_cover(b)
            return k, b, data, src

        with ThreadPoolExecutor(max_workers=6) as ex:
            for k, b, data, src in ex.map(work, todo):
                if data:
                    (COVER_DIR / f"{k}.jpg").write_bytes(data)
                idx[k] = {"src": src, "t": now, "title": b["title"]}
                counts[src] = counts.get(src, 0) + 1
        _save_index(idx)
        return counts


def attach_covers(books: list[dict]) -> list[dict]:
    """Point each book at its cached cover (or None) and return the ones still
    missing so a background fill can fetch them."""
    missing = []
    for b in books:
        k = cover_key(b)
        if cover_path(k):
            b["cover"] = f"/jmreads/cover/{k}"
        else:
            b["cover"] = None
            missing.append(b)
    return missing


def _background_fill(books: list[dict]) -> None:
    if _filling.is_set():
        return
    _filling.set()

    def run():
        try:
            if fill_covers(books):
                invalidate()   # next page load picks up the new covers
        finally:
            _filling.clear()
    threading.Thread(target=run, daemon=True).start()


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
            "reviewed": bool((excerpt if len(excerpt) > 40 else "") or isinstance(fm.get("score"), int)),
        })
    return books


def load_to_read(path: Path = TO_READ_FILE) -> list[dict]:
    """The want-to-read shelf, from the hcmc/to-read.md table
    (| Title | Link | 分 | Time | Area |). Only rows whose title cell reads
    `Title — Author` count as reading material; emails and chores in the same
    table are skipped, as are rows whose Link is "Done". Kept out of the
    reviews database so unread books never count toward the challenge."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or " — " not in cells[0] or cells[1].lower() == "done":
            continue
        title, author = (x.strip() for x in cells[0].rsplit(" — ", 1))
        note = ""
        m = re.match(r"^(.*?)\s*\((.+)\)\s*$", author)
        if m:   # "Al-Ghazali (tr. T.J. Winter)": keep the name clean for cover search
            author, note = m[1], m[2]
        link = re.search(r"\((https?://[^)]+)\)", cells[1])
        out.append({"id": "", "title": title, "author": author, "note": note,
                    "isbn": "", "link": link[1] if link else None})
    return out


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
    to_read = load_to_read()

    shown = {id(b) for b in reading} | {id(b) for b in read} | {id(e["book"]) for e in events}
    if covers:
        shown_books = [b for b in books if id(b) in shown] + to_read
        missing = attach_covers(shown_books)
        if missing:
            # A few new books (the usual case: one just added via /book) are
            # fetched inline so the very next page load has their covers;
            # a large backlog goes to the background thread instead.
            if len(missing) <= SYNC_COVER_LIMIT and not _filling.is_set():
                fill_covers(missing)
                missing = attach_covers(shown_books)
            if missing:
                _background_fill(missing)

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
        "to_read": [card(b) for b in to_read],
        "read": [card(b) for b in read],
        "feed": [{"type": e["type"], "date": e["date"].isoformat(), "book": card(e["book"])}
                 for e in events],
    }


LAUNCHER = Path(__file__).with_name("open-claude-tab.sh")


def request_review(book_id: str) -> tuple[bool, str]:
    """Open a Claude tab on Straylight running /bookreview for one book.
    Takes a book id (the review file's stem), never free text, so the
    endpoint can only ever start a review of a book already in the vault."""
    import socket
    import subprocess
    book = next((b for b in load_books() if b["id"] == book_id), None)
    if not book:
        return False, f"unknown book {book_id}"
    if read_date(book) is None:
        return False, "still reading"
    prompt = f"/bookreview {book['title']}"
    if book["author"]:
        prompt += f" by {book['author']}"
    if "straylight" in socket.gethostname().lower():
        cmd = [str(LAUNCHER)]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "straylight-refit",
               "~/i446-monorepo/tools/personal-dashboard/open-claude-tab.sh"]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return False, "Straylight did not answer"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[-200:] or f"exit {r.returncode}"
    return True, r.stdout.strip()


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
.review-btn { font: 12px Georgia, serif; padding: 5px 12px; margin-right: 10px; border-radius: 3px; cursor: pointer;
              background: #409d69; color: #fff; border: 1px solid #357f55; }
.review-btn:disabled { background: var(--badge-bg); color: var(--h2); border-color: var(--grid); cursor: default; }
</style>
</head>
<body>
<div class="topbar">
  <h1>JM · READS</h1>
  <span>
    <a class="nav-link" href="http://ix:5555">JM-AI-DASH</a>
    <a class="nav-link" href="http://ix:5556">M5X2 AI</a>
    <a class="nav-link" href="/more">MORE</a>
    <a class="nav-link" href="/">← JM DASHBOARD</a>
  </span>
</div>

<div class="reads">
  <div>
    <div class="card" style="margin-bottom:24px">
      <h2>Currently Reading</h2>
      <div id="reading"><div class="muted">loading…</div></div>
    </div>
    <div class="card challenge" style="margin-bottom:24px">
      <h2 id="chTitle">Reading Challenge</h2>
      <div id="challenge"><div class="muted">loading…</div></div>
    </div>
    <div class="card">
      <h2>Want to Read</h2>
      <div id="toread"><div class="muted">loading…</div></div>
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

  // Want to read
  document.getElementById('toread').innerHTML = (d.to_read || []).length ? d.to_read.map(b => `
    <div class="book-row">${cover(b, 'sm')}
      <div><div class="t">${b.link ? `<a href="${esc(b.link)}" target="_blank">${esc(b.title)}</a>` : esc(b.title)}</div><div class="a">by ${esc(b.author)}</div>
      ${b.note ? `<div class="a">${esc(b.note)}</div>` : ''}</div>
    </div>`).join('') : '<div class="muted">Empty. Add `Title — Author` rows to hcmc/to-read.md</div>';

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
    const writeBtn = (b.status !== 'reading' && !b.reviewed)
      ? `<button class="review-btn" data-id="${esc(b.id)}">Write review</button>` : '';
    const links = [writeBtn, b.url ? `<a href="${esc(b.url)}" target="_blank">Read review</a>` : '',
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

  document.getElementById('feed').addEventListener('click', ev => {
    const btn = ev.target.closest('.review-btn');
    if (!btn || btn.disabled) return;
    btn.disabled = true; btn.textContent = 'Opening…';
    fetch('/jmreads/review/' + encodeURIComponent(btn.dataset.id), {method: 'POST'})
      .then(r => r.json()).then(j => {
        btn.textContent = j.ok ? (j.where === 'cmux' ? 'Opened in cmux' : 'Opened in Terminal') : 'Failed';
        if (!j.ok) { btn.title = j.error || ''; btn.disabled = false; }
      }).catch(() => { btn.textContent = 'Failed'; btn.disabled = false; });
  });

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
