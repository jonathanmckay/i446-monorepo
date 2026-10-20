#!/usr/bin/env python3
"""book-add — add a book to the vault reviews database by title.

Looks the title up on Open Library (no API key), falls back to Google Books,
and creates ~/vault/hcmc/reviews/<year>/<slug>.md with the same frontmatter
shape the existing book entries use (media: book, status: reading).

Usage:
  book-add.py <title words...> [--author X] [--pick N] [--dry-run]

--pick N chooses the Nth candidate (1-based) when the default pick is wrong;
the top 3 candidates are always printed so the user can re-run with --pick.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request

REVIEWS = os.path.expanduser("~/vault/hcmc/reviews")
UA = {"User-Agent": "jm-vault-book-add/1.0"}


def slugify(title):
    s = title.lower()
    s = re.sub(r"['’]", "", s)
    s = re.sub(r"[^a-z0-9一-鿿؀-ۿ]+", "-", s)
    return s.strip("-") or "untitled"


def search_openlibrary(query, author=None):
    q = {"q": query, "limit": "8", "fields":
         "title,author_name,first_publish_year,number_of_pages_median,isbn,subject,language,edition_count"}
    if author:
        q["q"] += " " + author
    url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        docs = json.load(r).get("docs", [])
    return [{
        "title": d.get("title", ""),
        "author": ", ".join(d.get("author_name", [])[:2]),
        "year": d.get("first_publish_year"),
        "pages": d.get("number_of_pages_median"),
        "isbn": (d.get("isbn") or [None])[0],
        "subjects": (d.get("subject") or [])[:5],
        "editions": d.get("edition_count", 0),
    } for d in docs]


def search_googlebooks(query, author=None):
    q = f"intitle:{query}" + (f"+inauthor:{author}" if author else "")
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
        {"q": q, "maxResults": "8"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
        items = json.load(r).get("items", [])
    out = []
    for it in items:
        v = it.get("volumeInfo", {})
        ids = {i.get("type"): i.get("identifier") for i in v.get("industryIdentifiers", [])}
        out.append({
            "title": v.get("title", ""),
            "author": ", ".join(v.get("authors", [])[:2]),
            "year": int(v["publishedDate"][:4]) if v.get("publishedDate", "")[:4].isdigit() else None,
            "pages": v.get("pageCount"),
            "isbn": ids.get("ISBN_13") or ids.get("ISBN_10"),
            "subjects": (v.get("categories") or [])[:5],
            "editions": 0,
        })
    return out


def rank(candidates, query):
    """Trust the search engine's relevance order (Open Library's q= ranking
    reliably puts the famous work first, even under a translated title its
    exact-match scoring would miss); just push author-less entries back."""
    return sorted(candidates, key=lambda c: 0 if c["author"] else 1)


def existing_entry(slug):
    hits = []
    for year_dir in sorted(os.listdir(REVIEWS)):
        p = os.path.join(REVIEWS, year_dir, slug + ".md")
        if os.path.exists(p):
            hits.append(p)
    return hits


def make_note(c, today):
    fm = [
        "---",
        f'title: "{c["title"]}"',
    ]
    if c["author"]:
        fm.append(f'author: "{c["author"]}"')
    fm += [
        f"date: {today.isoformat()}",
        "type: review",
        "media: book",
    ]
    if c.get("year"):
        fm.append(f"published: {c['year']}")
    if c.get("pages"):
        fm.append(f"pages: {c['pages']}")
    if c.get("isbn"):
        fm.append(f'isbn: "{c["isbn"]}"')
    fm += [
        "tags: [hcmc, review]",
        "source: openlibrary",
        "status: reading",
        "draft: true",
        "---",
        "",
    ]
    body = ""
    if c.get("subjects"):
        body = "Subjects: " + ", ".join(c["subjects"]) + "\n"
    return "\n".join(fm) + body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="+")
    ap.add_argument("--author")
    ap.add_argument("--pick", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    query = " ".join(args.title)

    candidates, src = [], "openlibrary"
    try:
        candidates = search_openlibrary(query, args.author)
    except Exception as e:
        print(f"openlibrary failed ({e}); trying google books", file=sys.stderr)
    if not candidates:
        try:
            candidates, src = search_googlebooks(query, args.author), "googlebooks"
        except Exception as e:
            sys.exit(f"ERROR: both lookups failed ({e})")
    if not candidates:
        sys.exit(f"ERROR: no matches for {query!r}")

    ranked = rank(candidates, query)
    if args.pick < 1 or args.pick > len(ranked):
        sys.exit(f"ERROR: --pick out of range (1-{len(ranked)})")
    chosen = ranked[args.pick - 1]
    chosen_src = src

    slug = slugify(chosen["title"])
    dupes = existing_entry(slug)
    if dupes:
        sys.exit(f"EXISTS: {dupes[0]}")

    today = datetime.date.today()
    path = os.path.join(REVIEWS, str(today.year), slug + ".md")
    note = make_note(chosen, today).replace("source: openlibrary", f"source: {chosen_src}")

    if args.dry_run:
        print(note)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(note)

    meta = " · ".join(str(x) for x in (chosen.get("year"),
                      f"{chosen['pages']}pp" if chosen.get("pages") else None) if x)
    print(f"+ {chosen['title']} — {chosen['author'] or 'unknown author'}"
          + (f" ({meta})" if meta else "")
          + f" → {os.path.relpath(path, os.path.expanduser('~/vault'))}")
    others = [c for i, c in enumerate(ranked[:3], 1) if i != args.pick]
    for i, c in enumerate(ranked[:3], 1):
        if c is not chosen:
            print(f"  alt {i}: {c['title']} — {c['author']} ({c.get('year')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
