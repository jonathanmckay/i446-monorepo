#!/usr/bin/env python3
"""Port CAIS Veracross attendance (Recent Updates page text) into the vault.

Usage:
  veracross_attendance.py ingest --child Theo --text-file page.txt
  veracross_attendance.py render

ingest: parse the page text that Chrome's get_page_text returns for
  https://portals.veracross.com/cais/parent/student/<id>/recent-updates
  and merge new records into the log doc (idempotent: keyed by child+date+type).
render: rewrite the weekly summary block inside the Morning Routine section
  of independence-agency-enterprise.md (between sentinel comments).

The portal needs a logged-in browser, so fetching is done by the /attendance
skill through the Claude in Chrome extension; this script only parses and writes.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

CURR = Path.home() / "vault" / "xk87" / "xk23 学习 McKay Curriculum"
LOG = CURR / "cais-attendance.md"
DOC = CURR / "independence-agency-enterprise.md"
START, END = "<!-- attendance:start -->", "<!-- attendance:end -->"
SCHOOL_YEAR_START = date(2026, 8, 24)

LOG_HEADER = """---
title: "CAIS Attendance Log"
date: 2026-10-05
type: log
tags: [xk87, attendance]
source: veracross
status: active
---

Raw daily attendance from the Veracross parent portal (Recent Updates), one row per event. Written by `tools/xk87/veracross_attendance.py`; summarized in [[independence-agency-enterprise#Morning Routine]].

| Date | Child | Type | Detail | Time | Notes |
|---|---|---|---|---|---|
"""

DAY_RE = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), ([A-Z][a-z]{2}) (\d{1,2})$")


def _infer_date(mon: str, day: int, today: date) -> date:
    d = datetime.strptime(f"{mon} {day} {today.year}", "%b %d %Y").date()
    return d.replace(year=d.year - 1) if d > today + timedelta(days=7) else d


def parse(text: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    recs, cur = [], None
    i = 0
    while i < len(lines):
        m = DAY_RE.match(lines[i])
        if m:
            cur = {"date": _infer_date(m.group(2), int(m.group(3)), today)}
            i += 1
            continue
        if cur and lines[i] == "DAILY ATTENDANCE" and i + 2 < len(lines):
            rec = {"date": cur["date"], "type": lines[i + 1].title(), "detail": lines[i + 2],
                   "time": "", "notes": ""}
            j = i + 3
            while j + 1 < len(lines) and lines[j] in ("ARRIVAL TIME", "DISMISSAL TIME", "NOTES"):
                if lines[j] == "NOTES":
                    rec["notes"] = lines[j + 1]
                else:
                    rec["time"] = lines[j + 1]
                j += 2
            recs.append(rec)
            i = j
            continue
        i += 1
    return recs


def read_log() -> list[dict]:
    if not LOG.exists():
        return []
    rows = []
    for line in LOG.read_text().splitlines():
        if not re.match(r"^\| \d{4}-\d{2}-\d{2} \|", line):
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        rows.append({"date": date.fromisoformat(c[0]), "child": c[1], "type": c[2],
                     "detail": c[3], "time": c[4], "notes": c[5]})
    return rows


def write_log(rows: list[dict]) -> None:
    rows = sorted(rows, key=lambda r: (r["date"], r["child"]), reverse=True)
    body = "".join(f"| {r['date'].isoformat()} | {r['child']} | {r['type']} | {r['detail']} | {r['time']} | {r['notes']} |\n"
                   for r in rows)
    LOG.write_text(LOG_HEADER + body)


def ingest(child: str, text: str) -> tuple[int, int]:
    rows = read_log()
    have = {(r["child"], r["date"], r["type"]) for r in rows}
    new = 0
    for r in parse(text):
        key = (child, r["date"], r["type"])
        if key not in have:
            rows.append({**r, "child": child})
            have.add(key)
            new += 1
    write_log(rows)
    return new, len(rows)


def _minutes(t: str) -> int | None:
    try:
        dt = datetime.strptime(t.replace(" ", "").lower(), "%I:%M%p")
        return dt.hour * 60 + dt.minute
    except ValueError:
        return None


def _fmt(m: float) -> str:
    m = round(m)
    h, mm = divmod(m, 60)
    return f"{(h - 1) % 12 + 1}:{mm:02d}"


def summary(rows: list[dict]) -> str:
    children = sorted({r["child"] for r in rows}, key=lambda c: {"Theo": 0, "Ren": 1}.get(c, 9))
    out = ["#### School arrival (CAIS Veracross, weekly)", "",
           "Unlisted school days = on time. Source: [[cais-attendance]] (updated weekly by `/attendance`, run from `/xk887`).", ""]
    for child in children:
        cr = [r for r in rows if r["child"] == child]
        weeks: dict[date, list[dict]] = {}
        for r in cr:
            wk = r["date"] - timedelta(days=r["date"].weekday())
            weeks.setdefault(wk, []).append(r)
        tardy = [r for r in cr if r["type"] == "Tardy"]
        out += [f"**{child}:** {len(tardy)} tardies, "
                f"{sum(1 for r in cr if r['type'] == 'Absence')} absences, "
                f"{sum(1 for r in cr if r['type'] == 'Early Dismissal')} early dismissals this school year.", "",
                "| Week of | Tardies | Avg late arrival | Latest | Absences | Other |",
                "|---|---|---|---|---|---|"]
        for wk in sorted(weeks, reverse=True):
            w = weeks[wk]
            mins = [m for m in (_minutes(r["time"]) for r in w if r["type"] == "Tardy") if m]
            other = ", ".join(f"{r['type'].lower()} {r['time']}".strip() for r in w if r["type"] not in ("Tardy", "Absence"))
            notes = "; ".join(sorted({r["notes"] for r in w if r["type"] == "Absence" and r["notes"]}))
            ab = sum(1 for r in w if r["type"] == "Absence")
            out.append(f"| {wk.strftime('%b %-d')} | {len(mins) or 0} | {_fmt(statistics.mean(mins)) if mins else '—'} | "
                       f"{_fmt(max(mins)) if mins else '—'} | {ab}{' (' + notes + ')' if ab and notes else ''} | {other or ''} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render() -> None:
    rows = read_log()
    block = f"{START}\n{summary(rows)}{END}"
    s = DOC.read_text()
    if START in s and END in s:
        s = s[:s.index(START)] + block + s[s.index(END) + len(END):]
    else:
        anchor = s.index("### Morning Routine")
        nxt = s.find("\n#", anchor + 5)
        nxt = len(s) if nxt == -1 else nxt
        s = s[:nxt].rstrip("\n") + "\n\n" + block + "\n" + s[nxt:]
    s = re.sub(r"^updated: .*$", f"updated: {date.today().isoformat()}", s, count=1, flags=re.M)
    DOC.write_text(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("--child", required=True)
    i.add_argument("--text-file", required=True)
    sub.add_parser("render")
    a = ap.parse_args()
    if a.cmd == "ingest":
        new, total = ingest(a.child, Path(a.text_file).read_text())
        print(f"{a.child}: +{new} new records ({total} in log)")
    else:
        render()
        print(f"rendered summary into {DOC.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
