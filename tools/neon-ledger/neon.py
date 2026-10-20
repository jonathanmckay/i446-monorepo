#!/usr/bin/env python3
"""neon — CLI over the semantic Neon ledger (lib/neon/ledger.py).

The store the excel-http daemon double-writes into (Neon-off-Excel step 1,
2026-10-19). Runs where the DB lives — Ix — or anywhere with $NEON_DB.

Usage:
  neon.py status                       # db path, event count, kinds, first/last ts
  neon.py import-jsonl [FILE ...]      # (re)build from the vault JSONL; default: all months
  neon.py day [YYYY-MM-DD] [--sheet S] # per-column credits vs last observed value
  neon.py log [--n 20]                 # newest events

History: this file was the 2026-06 prototype (events(domain, points) with a
hand-typed `add`). The daemon-fed ledger replaced it; there is deliberately no
`add` here — a credit that bypasses the daemon would be a credit Excel never
saw, which is the exact divergence the double-write exists to measure. Hand
credits still go through the daemon (/append with a src) until cutover.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from neon import ledger  # noqa: E402

LEDGER_DIR = Path(os.path.expanduser("~/vault/g245/neon-ledger"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="neon")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    pi = sub.add_parser("import-jsonl"); pi.add_argument("files", nargs="*")
    pd = sub.add_parser("day"); pd.add_argument("date", nargs="?"); pd.add_argument("--sheet", default="0分")
    pl = sub.add_parser("log"); pl.add_argument("--n", type=int, default=20)
    a = p.parse_args(argv)

    if a.cmd == "status":
        s = ledger.status()
        print(f"db      {s['db']}")
        print(f"events  {s['events']}  ({s['first']} → {s['last']})")
        for k, n in sorted(s["kinds"].items(), key=lambda kv: -kv[1]):
            print(f"  {k:<11}{n:>7}")
    elif a.cmd == "import-jsonl":
        files = [Path(f) for f in a.files] or sorted(LEDGER_DIR.glob("20??-??.jsonl"))
        st = ledger.import_jsonl(files)
        print(f"files {st['files']}  seen {st['seen']}  inserted {st['inserted']}  ignored {st['ignored']}")
    elif a.cmd == "day":
        d = a.date or _dt.date.today().isoformat()
        rows = ledger.day(d, a.sheet)
        if not rows:
            print(f"no events for {d} on {a.sheet}")
            return 0
        print(f"{a.sheet} {d}")
        print(f"  {'col':<4}{'field':<8}{'credits':>9}{'refs':>8}{'set':>9}{'observed':>10}{'n':>4}")
        for r in rows:
            f = lambda v: "" if v is None else f"{v:g}"  # noqa: E731
            print(f"  {r['col']:<4}{(r['field'] or '?'):<8}{f(r['credits']):>9}{f(r['ref_points']):>8}"
                  f"{f(r['last_set']):>9}{f(r['observed']):>10}{r['n_events']:>4}")
    elif a.cmd == "log":
        for ts, date, sheet, col, field, kind, pts, av, src, block in ledger.recent(a.n):
            pts_s = "" if pts is None else f"{pts:+g}"
            av_s = "" if av is None else f"={av:g}"
            print(f"  {ts}  {date or '----------'} {block or ' '} {sheet}!{col:<3}{(field or ''):<10}"
                  f"{kind:<11}{pts_s:>7} {av_s:>9}  {src or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
