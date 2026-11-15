#!/usr/bin/env python3
"""toread-add: file a link or a dtd task on the JM Reads "want to read" shelf.

JM Reads (tools/personal-dashboard/jmreads.py, /jmreads on the personal
dashboard) builds that shelf from the table in ~/vault/hcmc/to-read.md: every
row whose title cell reads `Title — Author` is a card, linked when the Link
cell holds a markdown link. This script appends such a row.

  toread-add.py <url> [title words [— author]]   a link (title fetched from the page)
  toread-add.py <dtd row number>                 the task on that dtd row
  toread-add.py <task id | words of a task>      a task from dtd's cache
  toread-add.py <Title — Author>                 a plain title, no link

A task supplies its name (annotations and a leading "read:" stripped), its
first link, its (N) minutes, [N] 分 and its domain label. This script never
touches Todoist; it prints `task: <id>` so the /toread skill can delete the
task, which the shelf now replaces.

Output: `+ <Title — Author> → JM Reads (to read)[ <url>]` (+ `task: <id>`), or
`EXISTS: <row>` when the same link or title is already on the shelf.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
TO_READ = HOME / "vault" / "hcmc" / "to-read.md"
STATE = HOME / ".local" / "state" / "jm"
TASK_QUEUE = STATE / "task-queue.json"
DTD_ORDER = STATE / "dtd-order.json"
JMREADS = HOME / "i446-monorepo" / "tools" / "personal-dashboard" / "jmreads.py"
ROW_MAX_AGE = 3600   # a dtd row number older than this no longer means that row
SEP = " — "          # the separator jmreads.load_to_read requires in the title cell

URL_RE = re.compile(r"https?://[^\s<>()\[\]|]+(?:\([^\s()]*\))?[^\s<>()\[\]|.,;:!?]*")
MDLINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
ANN_RE = re.compile(r"\s*(?:\(\d+\)|\[[\d.+]+(?:/m)?\]|\{\d+\})")


# --- tasks --------------------------------------------------------------------
def load_tasks(path: Path = TASK_QUEUE) -> list[dict]:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    seen, out = set(), []
    for sec in d.values():
        if not isinstance(sec, list):
            continue
        for t in sec:
            if isinstance(t, dict) and t.get("id") is not None and str(t["id"]) not in seen:
                seen.add(str(t["id"]))
                out.append(t)
    return out


def row_task_id(n: int, path: Path = DTD_ORDER, now: float | None = None) -> str | None:
    """Same rule as agent_claims.row_task_id: row n of dtd's latest render."""
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    ids = d.get("ids") or []
    if (now or time.time()) - float(d.get("ts", 0)) > ROW_MAX_AGE or not 1 <= n <= len(ids):
        return None
    return str(ids[n - 1]) or None


def task_name(content: str) -> str:
    s = MDLINK_RE.sub(lambda m: "" if m.group(1) in ("", "(link)", "link") else m.group(1), content)
    s = URL_RE.sub("", s)
    s = ANN_RE.sub("", s)
    s = re.sub(r"\s@\S+", "", " " + s)
    return re.sub(r"\s+", " ", s).strip(" -:;,")


def find_task(query: str, tasks: list[dict], order: Path = DTD_ORDER) -> dict | str | None:
    """The task `query` names: a dtd row number, a task id, or words of its
    name. Returns the task, None for no match, or an error string when several
    names match."""
    q = query.strip()
    by_id = {str(t["id"]): t for t in tasks}
    if re.fullmatch(r"\d{1,3}", q):
        tid = row_task_id(int(q), order)
        if tid is None:
            return f"ERROR: no fresh dtd row {q} (open dtd so it re-renders)"
        return by_id.get(tid) or f"ERROR: dtd row {q} is task {tid}, not in the task cache"
    if q in by_id:
        return by_id[q]
    # The two-letter code dtd leads the row with (2026-11-15, lib/dtd_codes).
    try:
        sys.path.insert(0, str(Path.home() / "i446-monorepo" / "lib"))
        import dtd_codes
        ids = dtd_codes.lookup(q, tasks)
    except Exception:  # noqa: BLE001
        ids = []
    if len(ids) == 1:
        return by_id[ids[0]]
    if len(ids) > 1:
        return "ERROR: ambiguous task: " + "; ".join(task_name(by_id[i]["content"]) for i in ids[:6])
    ql = q.lower()
    hits = [t for t in tasks if ql and ql in task_name(t.get("content", "")).lower()]
    exact = [t for t in hits if task_name(t.get("content", "")).lower() == ql]
    hits = exact or hits
    if len(hits) > 1:
        return "ERROR: ambiguous task: " + "; ".join(task_name(t["content"]) for t in hits[:6])
    return hits[0] if hits else None


def task_fields(t: dict) -> dict:
    content = t.get("content", "")
    blob = content + " " + str(t.get("description") or "")
    m = MDLINK_RE.search(blob)
    url = m.group(2) if m else (URL_RE.search(blob).group(0) if URL_RE.search(blob) else "")
    mins = re.search(r"\((\d+)\)", content)
    pts = re.search(r"\[(\d+)(?:\.\d+)?\]", content)
    labels = [l for l in t.get("labels", []) if not re.search(r"neon|^-?\d|^0g$", l)]
    # "read: X" / "read X": the shelf already says it is to be read.
    title = re.sub(r"(?i)^read\b[:\s]+", "", task_name(content)).strip() or task_name(content)
    return {"title": title, "url": url, "task_id": str(t.get("id")),
            "minutes": mins.group(1) if mins else "", "points": pts.group(1) if pts else "",
            "area": labels[0] if labels else ""}


# --- links --------------------------------------------------------------------
def domain(url: str) -> str:
    return re.sub(r"^www\.", "", urllib.parse.urlparse(url).netloc.lower())


def _meta(page: str, *names: str) -> str:
    for n in names:
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(n)}["\']'):
            m = re.search(pat, page, re.I)
            if m and m.group(1).strip():
                return html.unescape(m.group(1)).strip()
    return ""


def page_meta(url: str, fetch=None) -> tuple[str, str]:
    """(title, author-or-site) read from the page, ('', '') when unreachable."""
    try:
        if fetch is None:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (jmreads toread-add)"})
            with urllib.request.urlopen(req, timeout=8) as r:
                page = r.read(400_000).decode("utf-8", "replace")
        else:
            page = fetch(url)
    except Exception:
        return "", ""
    title = _meta(page, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title = html.unescape(m.group(1)).strip() if m else ""
    title = re.sub(r"\s+", " ", title)
    author = _meta(page, "author", "article:author", "og:site_name")
    if author.startswith("http"):
        author = ""
    # "Essay title | Site" / "Essay title - Site": the site is the byline, not the title.
    m = re.match(r"^(.{8,}?)\s+[|–—-]\s+([^|–—-]{2,40})$", title)
    if m:
        title, author = m.group(1).strip(), author or m.group(2).strip()
    return title, author


# --- the table ----------------------------------------------------------------
def _cell(s: str) -> str:
    # jmreads.load_to_read splits rows on every "|", escaped or not, so a pipe
    # in a title would shift the cells and drop the card.
    return re.sub(r"\s*\|\s*", " / ", s).strip()


def make_row(title: str, author: str, url: str = "", points: str = "",
             minutes: str = "", area: str = "") -> str:
    link = f"[{domain(url)}]({url})" if url else ""
    mins = f"{minutes}m" if minutes else ""
    return f"| {_cell(title)}{SEP}{_cell(author)} | {link} | {points} | {mins} | {area} |"


def existing(text: str, title: str, url: str) -> str | None:
    """The live (not Done) row already holding this link or this title."""
    tl = title.lower()
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[1].lower() == "done":
            continue
        if url and f"({url})" in line:
            return line.strip()
        if tl and cells[0].lower().split(SEP)[0].strip() == tl:
            return line.strip()
    return None


def append_row(text: str, row: str, today: datetime.date) -> str:
    """Insert `row` after the table's last row and bump frontmatter `updated:`."""
    lines = text.splitlines()
    last = max((i for i, l in enumerate(lines) if l.lstrip().startswith("|")), default=None)
    if last is None:
        lines += ["", "| Title | Link | 分 | Time | Area |", "| --- | --- | --- | --- | --- |", row]
    else:
        lines.insert(last + 1, row)
    out = "\n".join(lines) + "\n"
    if out.startswith("---"):
        end = out.find("\n---", 3)
        head = re.sub(r"(?m)^updated:.*$", f"updated: {today.isoformat()}", out[:end])
        out = head + out[end:]
    return out


def poke_dashboard() -> None:
    """Refresh the running dashboard's cached shelf (and fetch a cover if one
    exists), detached, the way book-add.py does. Best-effort."""
    try:
        subprocess.Popen([sys.executable, str(JMREADS), "--fill-covers"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        subprocess.Popen(["curl", "-s", "-m", "60", "-o", "/dev/null",
                          "http://localhost:5558/api/reads?fresh=1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


# --- resolve + main -----------------------------------------------------------
def resolve(text: str, tasks: list[dict], fetch=None, order: Path = DTD_ORDER) -> dict | str:
    """Fields for the row, or an 'ERROR: ...' string."""
    text = text.strip()
    if not text:
        return "ERROR: nothing to file (give a link, a dtd row number or a task)"
    m = URL_RE.search(text)
    f = {"title": "", "author": "", "url": "", "minutes": "", "points": "", "area": "hcmc"}
    if m:
        f["url"] = m.group(0)
        rest = (text[:m.start()] + " " + text[m.end():]).strip()
        if SEP in rest:
            f["title"], f["author"] = (x.strip() for x in rest.rsplit(SEP, 1))
        else:
            f["title"] = rest
    else:
        t = find_task(text, tasks, order)
        if isinstance(t, str):
            return t
        if t is not None:
            tf = task_fields(t)
            f.update({k: v for k, v in tf.items() if v})
            f["area"] = tf["area"] or f["area"]
        elif SEP in text:
            f["title"], f["author"] = (x.strip() for x in text.rsplit(SEP, 1))
        else:
            return f"ERROR: no dtd task matches {text!r}; for a plain title write 'Title — Author'"
        if SEP in f["title"] and not f["author"]:
            f["title"], f["author"] = (x.strip() for x in f["title"].rsplit(SEP, 1))
    if f["url"] and not (f["title"] and f["author"]):
        pt, pa = page_meta(f["url"], fetch)
        f["title"] = f["title"] or pt
        f["author"] = f["author"] or pa
    if f["url"]:
        f["author"] = f["author"] or domain(f["url"])
        f["title"] = f["title"] or urllib.parse.unquote(
            f["url"].rstrip("/").rsplit("/", 1)[-1]).replace("-", " ").replace("_", " ") or domain(f["url"])
    f["author"] = f["author"] or f["area"] or "task"
    if not f["title"]:
        return "ERROR: could not work out a title; pass one after the link"
    return f


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", nargs="+")
    ap.add_argument("--author")
    ap.add_argument("--area")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    f = resolve(" ".join(a.what), load_tasks())
    if isinstance(f, str):
        print(f, file=sys.stderr)
        return 1
    if a.author:
        f["author"] = a.author
    if a.area:
        f["area"] = a.area
    try:
        text = TO_READ.read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: cannot read {TO_READ}: {e}", file=sys.stderr)
        return 1
    dup = existing(text, f["title"], f["url"])
    if dup:
        print(f"EXISTS: {dup}")
        return 0
    row = make_row(f["title"], f["author"], f["url"], f["points"], f["minutes"], f["area"])
    if a.dry_run:
        print(row)
        return 0
    TO_READ.write_text(append_row(text, row, datetime.date.today()), encoding="utf-8")
    poke_dashboard()
    print(f"+ {f['title']}{SEP}{f['author']} → JM Reads (to read)" + (f" {f['url']}" if f["url"] else ""))
    if f.get("task_id"):
        print(f"task: {f['task_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
