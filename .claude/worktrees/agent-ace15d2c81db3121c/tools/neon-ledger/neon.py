#!/usr/bin/env python3
"""Neon ledger — PROTOTYPE alternative to the Excel points workbook.

The pitch: points are an APPEND-ONLY event log. Every "+N to domain on date" is
one immutable row. Daily totals, domain totals, the 0分 grand total — all are
DERIVED with SQL, never stored. That removes the two failure modes of the Excel
model: position-dependent column maps that drift (the 2026-04-28 column removal),
and the hard dependency on a live Excel GUI + AppleScript.

This is a prototype to evaluate the model, not a migration. The Excel workbook
stays the source of truth until/unless this proves workable.

Schema:
  events(id, ts, date, domain, points, source, note)
    - ts:     ISO timestamp the event was logged (audit trail)
    - date:   the day the points count toward (YYYY-MM-DD; may differ from ts)
    - domain: code (0g, i9, m5, 个, 媒, 思, hcb, xk, 社, n156, ...)
    - points: signed int (negatives allowed for corrections)
    - source: where it came from (did, 1nd, manual, import, ...)

Totals are views, so "what did the Excel cell say" becomes a query you can run,
diff, and audit. Corrections are new rows, never destructive edits.

Usage:
  neon.py add <domain> <points> [--date YYYY-MM-DD] [--source S] [--note "..."]
  neon.py today [--date YYYY-MM-DD]      # per-domain + grand total for a day
  neon.py domain <code> [--since YYYY-MM-DD]
  neon.py log [--n 20]                   # recent events
  neon.py total [--since YYYY-MM-DD]     # grand total
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date as _date, datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("NEON_DB", Path.home() / ".local/share/neon/neon.db"))

# Domain codes mirror the 0分 sheet columns (the authoritative per-domain set).
DOMAINS = {"0g", "i9", "m5", "个", "媒", "思", "hcb", "xk", "社", "n156", "-1n"}


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,
            date    TEXT NOT NULL,
            domain  TEXT NOT NULL,
            points  INTEGER NOT NULL,
            source  TEXT NOT NULL DEFAULT 'manual',
            note    TEXT
        )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_domain ON events(domain)")
    return c


def _now() -> str:
    # Passed in via env in tests/cron to stay deterministic; falls back to clock.
    return os.environ.get("NEON_NOW") or datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return os.environ.get("NEON_TODAY") or _date.today().isoformat()


def add(domain: str, points: int, *, on: str | None = None,
        source: str = "manual", note: str | None = None) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO events (ts, date, domain, points, source, note) VALUES (?,?,?,?,?,?)",
        (_now(), on or _today(), domain, int(points), source, note))
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def day_breakdown(on: str | None = None) -> tuple[list[tuple[str, int]], int]:
    c = _conn()
    on = on or _today()
    rows = c.execute(
        "SELECT domain, SUM(points) FROM events WHERE date=? GROUP BY domain ORDER BY 2 DESC",
        (on,)).fetchall()
    total = c.execute("SELECT COALESCE(SUM(points),0) FROM events WHERE date=?", (on,)).fetchone()[0]
    c.close()
    return [(d, int(p)) for d, p in rows], int(total)


def domain_total(domain: str, since: str | None = None) -> int:
    c = _conn()
    if since:
        v = c.execute("SELECT COALESCE(SUM(points),0) FROM events WHERE domain=? AND date>=?",
                      (domain, since)).fetchone()[0]
    else:
        v = c.execute("SELECT COALESCE(SUM(points),0) FROM events WHERE domain=?",
                      (domain,)).fetchone()[0]
    c.close()
    return int(v)


def grand_total(since: str | None = None) -> int:
    c = _conn()
    if since:
        v = c.execute("SELECT COALESCE(SUM(points),0) FROM events WHERE date>=?", (since,)).fetchone()[0]
    else:
        v = c.execute("SELECT COALESCE(SUM(points),0) FROM events").fetchone()[0]
    c.close()
    return int(v)


def recent(n: int = 20) -> list[tuple]:
    c = _conn()
    rows = c.execute(
        "SELECT date, domain, points, source, COALESCE(note,'') FROM events ORDER BY id DESC LIMIT ?",
        (n,)).fetchall()
    c.close()
    return rows


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="neon")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add")
    pa.add_argument("domain"); pa.add_argument("points", type=int)
    pa.add_argument("--date", dest="on"); pa.add_argument("--source", default="manual")
    pa.add_argument("--note")

    pt = sub.add_parser("today"); pt.add_argument("--date", dest="on")
    pd = sub.add_parser("domain"); pd.add_argument("code"); pd.add_argument("--since")
    pl = sub.add_parser("log"); pl.add_argument("--n", type=int, default=20)
    pg = sub.add_parser("total"); pg.add_argument("--since")

    a = p.parse_args(argv)
    if a.cmd == "add":
        if a.domain not in DOMAINS:
            print(f"warn: '{a.domain}' not a known domain {sorted(DOMAINS)}", file=sys.stderr)
        rid = add(a.domain, a.points, on=a.on, source=a.source, note=a.note)
        print(f"+{a.points} → {a.domain} on {a.on or _today()} (id {rid})")
    elif a.cmd == "today":
        rows, total = day_breakdown(a.on)
        for d, pts in rows:
            print(f"  {d:<6} {pts:>5}")
        print(f"  {'Σ':<6} {total:>5}")
    elif a.cmd == "domain":
        print(domain_total(a.code, a.since))
    elif a.cmd == "log":
        for date, dom, pts, src, note in recent(a.n):
            print(f"  {date}  {dom:<5} {pts:>+5}  {src:<8} {note}")
    elif a.cmd == "total":
        print(grand_total(a.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
