#!/usr/bin/env python3
"""neon — CLI over the semantic Neon ledger (lib/neon/ledger.py).

The store the excel-http daemon double-writes into (Neon-off-Excel step 1,
2026-10-19). Runs where the DB lives — Ix — or anywhere with $NEON_DB.

Usage:
  neon.py status                       # db path, event count, kinds, first/last ts
  neon.py import-jsonl [FILE ...]      # (re)build from the vault JSONL; default: all months
  neon.py day [YYYY-MM-DD] [--sheet S] # per-column credits vs last observed value
  neon.py log [--n 20]                 # newest events
  neon.py diff [YYYY-MM-DD] [--write]  # rule engine vs Excel for one day (step 3 parallel run)
  neon.py strict [YYYY-MM-DD] [--since D] [--write]
                                       # cutover gate: ledger ALONE vs Excel; --write updates
                                       # ~/vault/g245/neon-ledger/strict-streak.json (read by /0t)
                                       # --write → ~/vault/g245/neon-ledger/rules-diff/<date>.json + rules-diff-latest.json

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
from neon import ledger, rules  # noqa: E402

LEDGER_DIR = Path(os.path.expanduser("~/vault/g245/neon-ledger"))
DIFF_DIR = LEDGER_DIR / "rules-diff"
STREAK_PATH = LEDGER_DIR / "strict-streak.json"
STREAK_TARGET = 5


def run_strict(dates: list, write: bool) -> dict:
    """Strict-check each date; with write, merge into the streak file."""
    import json as _json
    ledger.repair_dates()
    state = {"target": STREAK_TARGET, "days": {}}
    if STREAK_PATH.exists():
        try:
            state = _json.loads(STREAK_PATH.read_text())
        except ValueError:
            pass
    for d in dates:
        state["days"][d] = rules.strict_check(d)
    state["streak"] = rules.streak(state["days"])
    state["target"] = STREAK_TARGET
    state["generated"] = _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    if write:
        STREAK_PATH.write_text(_json.dumps(state, ensure_ascii=False, indent=1) + "\n")
    return state


def print_strict(state: dict, dates: list) -> None:
    for d in dates:
        r = state["days"][d]
        mark = "✅" if r["pass"] else ("…" if not r["complete"] else "❌")
        print(f"{mark} {d}")
        for m in r["misses"]:
            print(f"   {m['col']} {m.get('field') or ''}: ledger {m['derived']:g} vs Excel {m['observed']:g}")
            for w in m["why"]:
                print(f"      · {w}")
    print(f"streak {state['streak']}/{state['target']}")


def run_diff(date_iso: str, write: bool) -> dict:
    """Derive the day, compare, optionally persist. Repairs date-less
    row-addressed events first (locks that beat the first dated write)."""
    import json as _json
    repaired = ledger.repair_dates()
    day = rules.derive_day(date_iso)
    day["event_blocks"] = rules.event_blocks(date_iso)
    day["summary"] = rules.summarize(day)
    day["generated"] = _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    day["repaired_dates"] = repaired
    if write:
        DIFF_DIR.mkdir(parents=True, exist_ok=True)
        text = _json.dumps(day, ensure_ascii=False, indent=1)
        (DIFF_DIR / f"{date_iso}.json").write_text(text + "\n")
        (LEDGER_DIR / "rules-diff-latest.json").write_text(text + "\n")
    return day


def print_diff(day: dict) -> None:
    s = day["summary"]
    print(f"0分 {day['date']}  ok={len(s['ok'])} mismatch={len(s['mismatch'])} partial={len(s['partial'])} "
          f"no-obs={len(s['no_observation'])}  weights={day.get('weights_known')}")
    print(f"  {'col':<4}{'status':<16}{'derived':>9}{'observed':>10}{'Δ':>8}  components / unknown")
    for col in ["D"] + rules.POINT_COLS:
        r = day["cols"][col]
        f = lambda v: "" if v is None else f"{v:g}"  # noqa: E731
        comp = ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) and v is not None else f"{k}={v}"
                         for k, v in r["components"].items())
        unk = (" | unknown: " + ", ".join(r["unknown"][:6]) + (" …" if len(r["unknown"]) > 6 else "")) if r["unknown"] else ""
        print(f"  {col:<4}{r['status']:<16}{f(r['derived']):>9}{f(r['observed']):>10}{f(r['delta']):>8}  {comp}{unk}")
    locks = {g: b["lock"] for g, b in day["blocks"].items() if b["lock"] is not None}
    print(f"  blocks  excel-locks={locks}  event-ts={day.get('event_blocks')}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="neon")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    pi = sub.add_parser("import-jsonl"); pi.add_argument("files", nargs="*")
    pd = sub.add_parser("day"); pd.add_argument("date", nargs="?"); pd.add_argument("--sheet", default="0分")
    pl = sub.add_parser("log"); pl.add_argument("--n", type=int, default=20)
    ps = sub.add_parser("strict"); ps.add_argument("date", nargs="?"); ps.add_argument("--since")
    ps.add_argument("--write", action="store_true")
    pf = sub.add_parser("diff"); pf.add_argument("date", nargs="?"); pf.add_argument("--write", action="store_true")
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
    elif a.cmd == "diff":
        d = a.date or (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        print_diff(run_diff(d, a.write))
    elif a.cmd == "strict":
        end = _dt.date.fromisoformat(a.date) if a.date else _dt.date.today() - _dt.timedelta(days=1)
        start = _dt.date.fromisoformat(a.since) if a.since else end
        dates = [(start + _dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        print_strict(run_strict(dates, a.write), dates)
    elif a.cmd == "log":
        for ts, date, sheet, col, field, kind, pts, av, src, block in ledger.recent(a.n):
            pts_s = "" if pts is None else f"{pts:+g}"
            av_s = "" if av is None else f"={av:g}"
            print(f"  {ts}  {date or '----------'} {block or ' '} {sheet}!{col:<3}{(field or ''):<10}"
                  f"{kind:<11}{pts_s:>7} {av_s:>9}  {src or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
