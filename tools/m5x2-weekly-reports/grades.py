#!/usr/bin/env python3
"""JM's A-F grades for f695 reviews, stored in the f695 doc itself (2026-10-28).

The doc is the durable store, so a grade survives rebuilds and reads fine in
Obsidian:

  m5x2  link line under `## m5x2 weekly updates` ends with ` — grade: B`
        - [Andie · Leasing](https://mail.google.com/...#all/<thread>) — Subject (10/26) — grade: B
  i9    the entry's closing line under `## Weekly updates`
        **JM (B):** comments      (cleared → **JM:** comments)

The /f695 dashboard page reads these via read_grades() and writes them via
set_m5x2() / set_i9() through the personal dashboard's /f695/grade endpoint.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

DOC = Path.home() / "vault" / "h335" / "f693" / "1-f695-weekly-updates.md"
GRADE_RE = re.compile(r"^[A-F][+-]?$")
M5X2_SECTION = "## m5x2 weekly updates"
I9_SECTION = "## Weekly updates"
_TAIL = re.compile(r" — grade: [A-F][+-]?\s*$")
_JM = re.compile(r"^\*\*JM(?: \(([A-Fa-f][+-]?)\))?:\*\*")


def _section(lines: list[str], heading: str) -> tuple[int, int]:
    """[start, end) line span of a `## ` section (start = heading line), or (-1, -1)."""
    for i, line in enumerate(lines):
        if line.strip() == heading:
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            return i, j
    return -1, -1


def _check(grade: str) -> str:
    grade = (grade or "").strip().upper()
    if grade and not GRADE_RE.match(grade):
        raise ValueError(f"bad grade {grade!r}")
    return grade


def read_grades(text: str) -> dict:
    """{"m5x2": {thread: grade}, "i9": {"YYYY-MM-DD|Person": grade}}"""
    lines = text.splitlines()
    out: dict = {"m5x2": {}, "i9": {}}
    a, b = _section(lines, M5X2_SECTION)
    for line in lines[a + 1:b] if a >= 0 else []:
        t = re.search(r"#all/([0-9a-f]+)\)", line)
        g = re.search(r" — grade: ([A-F][+-]?)\s*$", line)
        if t and g:
            out["m5x2"][t.group(1)] = g.group(1)
    a, b = _section(lines, I9_SECTION)
    week = person = None
    for line in lines[a + 1:b] if a >= 0 else []:
        m = re.match(r"^### Week of (\d{4})\.(\d{2})\.(\d{2})", line)
        if m:
            week, person = dt.date(*map(int, m.groups())).isoformat(), None
            continue
        p = re.match(r"^#### (.+?)(\s*\(late\))?\s*$", line)
        if p:
            person = p.group(1).strip()
            continue
        j = _JM.match(line.strip())
        if j and j.group(1) and week and person:
            out["i9"][f"{week}|{person}"] = j.group(1).upper()
    return out


def set_m5x2(text: str, thread: str, grade: str) -> str:
    """Set (or clear, grade="") the grade on the link line for `thread`.
    KeyError if the thread has no link line yet."""
    grade = _check(grade)
    lines = text.splitlines(keepends=True)
    a, b = _section([l.rstrip("\n") for l in lines], M5X2_SECTION)
    for i in range(a + 1, b) if a >= 0 else []:
        if f"#all/{thread})" in lines[i]:
            nl = "\n" if lines[i].endswith("\n") else ""
            base = _TAIL.sub("", lines[i].rstrip("\n"))
            lines[i] = base + (f" — grade: {grade}" if grade else "") + nl
            return "".join(lines)
    raise KeyError(thread)


def set_i9(text: str, week: str, person: str, grade: str) -> str:
    """Set (or clear) the **JM (X):** grade of one i9 entry. Adds a bare
    `**JM (X):**` line at the end of the entry if it has no JM line yet.
    KeyError if the week/person entry doesn't exist."""
    grade = _check(grade)
    want = dt.date.fromisoformat(week)
    lines = text.splitlines(keepends=True)
    a, b = _section([l.rstrip("\n") for l in lines], I9_SECTION)
    if a < 0:
        raise KeyError(week)
    i, cur_week, start = a + 1, None, -1
    while i < b:
        line = lines[i].strip()
        m = re.match(r"^### Week of (\d{4})\.(\d{2})\.(\d{2})", line)
        if m:
            cur_week = dt.date(*map(int, m.groups()))
        p = re.match(r"^#### (.+?)(\s*\(late\))?\s*$", line)
        if p and cur_week == want and p.group(1).strip() == person:
            start = i
            break
        i += 1
    if start < 0:
        raise KeyError(f"{week}|{person}")
    end = start + 1
    while end < b and not re.match(r"^#{2,4} ", lines[end]):
        end += 1
    tag = f"**JM ({grade}):**" if grade else "**JM:**"
    for k in range(start + 1, end):
        if _JM.match(lines[k].strip()):
            lines[k] = _JM.sub(tag, lines[k].lstrip(), count=1)
            return "".join(lines)
    if not grade:
        return text
    # no JM line yet: insert before the entry's trailing blank lines
    k = end
    while k - 1 > start and not lines[k - 1].strip():
        k -= 1
    lines.insert(k, f"\n{tag}\n")
    return "".join(lines)


def _link_first(text: str, link: dict) -> str:
    """Add the m5x2 link line /ibxm5x2 would have added (same code path), so a
    review graded before /ibxm5x2 linked it still gets a durable line."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m5x2_weekly_links", Path(__file__).resolve().parent.parent / "ibx" / "m5x2_weekly_links.py")
    wl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wl)
    u = {**link, "date": dt.date.fromisoformat(link["date"])}
    return wl.insert_links(text, [u])[0]


def save(kind: str, grade: str, *, thread: str = "", week: str = "", person: str = "",
         link: dict | None = None, doc: Path = DOC) -> None:
    """link = {thread, who, topic, subject, date, sender} lets an m5x2 review
    with no link line yet be linked first instead of failing."""
    text = doc.read_text(encoding="utf-8")
    if kind == "m5x2":
        try:
            new = set_m5x2(text, thread, grade)
        except KeyError:
            if not link:
                raise
            new = set_m5x2(_link_first(text, link), thread, grade)
    else:
        new = set_i9(text, week, person, grade)
    if new != text:
        tmp = doc.with_suffix(".md.tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(doc)
