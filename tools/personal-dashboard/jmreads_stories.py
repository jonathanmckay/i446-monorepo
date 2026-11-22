"""jmreads stories: the daily news brief shown as a feed story on /jmreads.

Source (2026-11-21): ~/code/x953-hcmc0, the 05:25 job that takes The
Economist's "World in Brief" email, translates it to Chinese and adds 财新 and
الشرق الأوسط headlines. It used to email the result to JM; with --to-feed it
writes one JSON file a day to STORIES_DIR instead (x953's feed_writer.py owns
the schema) and this module reads them for jmreads.build().

Kept outside the vault on purpose: a daily brief should not pile up in a
git-pushed tree. Stdlib only; files older than KEEP_DAYS are pruned on load.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

STORIES_DIR = Path.home() / ".local" / "state" / "jmreads" / "stories"
FEED_DAYS = 7
KEEP_DAYS = 30


def _url(u) -> str:
    """Only http(s) links reach the page's href attributes."""
    u = str(u or "").strip()
    return u if re.match(r"https?://", u, re.I) else ""


def _story(path: Path, rec: dict) -> dict | None:
    sections = []
    for sec in rec.get("sections") or []:
        items = [{"title": str(i.get("title") or ""), "text": str(i.get("text") or ""), "url": _url(i.get("url"))}
                 for i in sec.get("items") or [] if i.get("title") or i.get("text")]
        if items:
            sections.append({"name": str(sec.get("name") or ""), "sub": str(sec.get("sub") or ""),
                             "lead": bool(sec.get("lead")), "rtl": bool(sec.get("rtl")), "items": items})
    if not sections:
        return None
    day = str(rec["date"])
    date.fromisoformat(day)
    return {"id": path.stem, "source": str(rec.get("source") or ""), "kind": str(rec.get("kind") or ""),
            "title": str(rec.get("title") or rec.get("title_en") or rec.get("kind") or ""),
            "date": day, "sections": sections,
            "links": [{"label": str(l.get("label") or ""), "url": _url(l.get("url"))}
                      for l in rec.get("links") or [] if _url(l.get("url"))]}


def load_stories(days: int = FEED_DAYS, today: date | None = None,
                 stories_dir: Path | None = None) -> list[dict]:
    """Stories of the last `days` days, newest first. A malformed file is
    skipped, never fatal to the page."""
    stories_dir = stories_dir or STORIES_DIR
    floor = ((today or date.today()) - timedelta(days=days)).isoformat()
    cutoff = time.time() - KEEP_DAYS * 86400
    out = []
    for path in stories_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                continue
            story = _story(path, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if story and story["date"] >= floor:
            out.append(story)
    out.sort(key=lambda s: (s["date"], s["id"]), reverse=True)
    return out
