#!/usr/bin/env python3
"""jmreads stories: newsletter issues shown as feed stories on /jmreads.

First (and so far only) source: The Economist's daily "The World in Brief"
email to the jbm Gmail (2026-11-21). `fetch` (LaunchAgent
com.jm.jmreads-stories, every 30 min) copies each issue's text/plain part to
~/.local/state/jmreads/stories/<id>.json and takes the email out of the inbox,
so the issue is read on the page instead of in ibx. Kept outside the vault on
purpose: a daily newsletter's text should not pile up in a git-pushed tree.

The RAW text is stored and parsed on every load, so a parser fix repairs
issues already on disk. An email is only archived once its parse yields
bullets; if the Economist changes its layout the mail stays where JM sees it.

load_stories / parse_brief are stdlib-only (imported by jmreads.py inside the
dashboard); the Gmail client is imported inside fetch().
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

STORIES_DIR = Path.home() / ".local" / "state" / "jmreads" / "stories"
QUERY = 'from:e.economist.com subject:"The World in Brief" newer_than:8d'
SUBJECT_PREFIX = "The World in Brief:"
SOURCE, KIND = "The Economist", "The World in Brief"
GMAIL_URL = "https://mail.google.com/mail/?authuser=jonathan.b.mckay@gmail.com#all/{id}"
KEEP_DAYS = 30
FEED_DAYS = 7

SECTIONS = ("Today’s top stories", "Today's top stories", "Word of the week",
            "Financial markets", "Today’s agenda", "Today's agenda")
FOOTERS = ("A MESSAGE FROM", "Download our app", "This email has been sent",
           "Learn about Economist")
# A photo credit sits alone above each agenda headline: "REUTERS",
# "AP PHOTO/OHAD ZWIGENBERG", "Photo: AP".
_CREDIT = re.compile(r"(?:Photo(?:graph)?s?|Illustration|Image)s?:\s*\S.{0,60}|[A-Z0-9 /.,&’'\-]{2,60}")
_PCT = re.compile(r"[-+−]?\d+(?:\.\d+)?%")


# --- parsing -----------------------------------------------------------------

def _paragraphs(text: str) -> list[str]:
    t = html.unescape(text).replace("\r", "").replace(" ", " ")
    t = re.sub(r"<[^>\n]{0,400}>", " ", t)                        # stray tags in the text part
    t = re.sub(r"[ \t]*\n?[ \t]*https?://\S+[ \t]*\n?", " ", t)   # tracking links, on their own lines
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" +([,.;:?!”)])", r"\1", t)                       # gaps the removed links leave
    t = re.sub(r"([“(]) +", r"\1", t)
    return [" ".join(x.strip() for x in p.split("\n")).strip()
            for p in re.split(r"\n\s*\n", t) if p.strip()]


def parse_brief(text: str) -> dict:
    """{"bullets": [top stories], "body": mdLite text with `## ` headings}.
    Empty bullets means the layout was not recognised."""
    paras = _paragraphs(text or "")
    start = next((i for i, p in enumerate(paras) if p in SECTIONS), None)
    if start is None:
        return {"bullets": [], "body": ""}
    paras = paras[start:]
    paras = paras[:next((i for i, p in enumerate(paras) if p.startswith(FOOTERS)), len(paras))]
    out, bullets, heading_next, market, row = [], [], False, False, []
    for p in paras:
        if re.search(r"\bSign Up$", p):          # house-ad block
            continue
        if p in SECTIONS:
            market = p == "Financial markets"
            out.append("## " + p)
            heading_next = False
            continue
        if market:
            if p.startswith("Data at"):
                market = False
                out.append(p)
                continue
            row.append(p)                        # index / level / change arrive as 3 paragraphs
            if _PCT.search(p):
                out.append(" ".join(row))
                row = []
            continue
        if _CREDIT.fullmatch(p):
            heading_next = True
            continue
        if heading_next:
            out.append("## " + p)
            heading_next = False
            continue
        if p.startswith("▸"):
            bullets.append(p.lstrip("▸ ").strip())
        out.append(p)
    return {"bullets": bullets, "body": "\n\n".join(out)}


# --- store -------------------------------------------------------------------

def load_stories(days: int = FEED_DAYS, today: date | None = None,
                 stories_dir: Path | None = None) -> list[dict]:
    """Stories of the last `days` days, newest first, parsed from stored raw text."""
    stories_dir = stories_dir or STORIES_DIR
    floor = ((today or date.today()) - timedelta(days=days)).isoformat()
    out = []
    for path in sorted(stories_dir.glob("*.json"), reverse=True):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if str(rec["date"]) < floor:
                continue
            parsed = parse_brief(rec.get("raw", ""))
            if not parsed["bullets"]:
                continue
            out.append({"id": path.stem, "source": rec.get("source", SOURCE),
                        "kind": rec.get("kind", KIND), "title": rec.get("title") or KIND,
                        "date": str(rec["date"]), "url": rec.get("url"),
                        "bullets": parsed["bullets"], "body": parsed["body"]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    out.sort(key=lambda s: s["date"], reverse=True)
    return out


def _plain(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts") or []:
        t = _plain(part)
        if t:
            return t
    return ""


def record_from_message(msg: dict) -> dict:
    """Gmail message (format=full) -> the stored record."""
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("subject", "")
    title = subject[len(SUBJECT_PREFIX):].strip() if subject.startswith(SUBJECT_PREFIX) else subject
    ts = int(msg["internalDate"]) / 1000
    return {"source": SOURCE, "kind": KIND, "title": title,
            "date": datetime.fromtimestamp(ts).date().isoformat(), "ts": ts,
            "gmail_id": msg["id"], "url": GMAIL_URL.format(id=msg["id"]),
            "raw": _plain(msg["payload"])}


def fetch(stories_dir: Path | None = None, service=None) -> int:
    stories_dir = stories_dir or STORIES_DIR
    if service is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ibx"))
        import email_watcher
        service = email_watcher.get_service("tokens-gmail.json", "gcp-oauth-gmail.keys.json")
    if service is None:
        print("jmreads-stories: no Gmail service (jbm tokens)", file=sys.stderr)
        return 1
    stories_dir.mkdir(parents=True, exist_ok=True)
    msgs = service.users().messages().list(userId="me", q=QUERY, maxResults=20).execute().get("messages", [])
    have = set()
    for p in stories_dir.glob("*.json"):
        try:
            have.add(json.loads(p.read_text(encoding="utf-8")).get("gmail_id"))
        except (OSError, ValueError):
            pass
    new = 0
    for m in msgs:
        if m["id"] in have:
            continue
        msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        rec = record_from_message(msg)
        if not parse_brief(rec["raw"])["bullets"]:
            print(f"jmreads-stories: unparsed layout, left in mail: {m['id']} {rec['title']!r}", file=sys.stderr)
            continue
        path = stories_dir / f"economist-{rec['date']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        new += 1
        drop = [l for l in ("INBOX", "UNREAD") if l in (msg.get("labelIds") or [])]
        if drop:   # "instead": the issue now lives on /jmreads, not in ibx
            service.users().messages().modify(userId="me", id=m["id"], body={"removeLabelIds": drop}).execute()
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in stories_dir.glob("*.json"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
    print(f"jmreads-stories: {new} new, {len(msgs)} matched")
    return 0


if __name__ == "__main__":
    sys.exit(fetch())
