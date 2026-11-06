#!/usr/bin/env python3
"""What JM did in untracked time ranges, from local logs (/auton156).

  evidence.py 0612-0631 0633-0638 [--date YYYY-MM-DD]

One line per event, sorted: neon ledger writes, Todoist cards closed through
did-fast, and prompts typed into Claude sessions on this machine. Toggl and
Todoist activity come from their MCP tools, see the skill.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

LEDGER = Path.home() / "vault/g245/neon-ledger"
COMPLETED = Path.home() / ".local/state/jm/completed-today.json"
PROJECTS = Path.home() / ".claude/projects"
SKIP_KINDS = {"baseline", "snapshot", "reconcile", "ack"}
SKIP_PROMPT = ("<system", "Base directory", "Another Claude", "<bash-stdout", "[Request interrupted",
               "Follow the d357 skill")   # the last is janus's automated meeting filing
MACHINE_VIA = {"inline", "sameday"}   # agent repairs, not something JM did


def parse_ranges(args: list, day: dt.date) -> list:
    out = []
    for a in args:
        m = re.fullmatch(r"(\d{1,2}):?(\d{2})-(\d{1,2}):?(\d{2})", a)
        if not m:
            raise SystemExit(f"bad range {a!r}, want HHMM-HHMM")
        h1, m1, h2, m2 = map(int, m.groups())
        out.append((dt.datetime.combine(day, dt.time(h1, m1)),
                    dt.datetime.combine(day, dt.time(h2, m2))))
    return out


def inside(t: dt.datetime, ranges: list) -> bool:
    return any(lo <= t < hi for lo, hi in ranges)


def ledger_events(ranges: list, day: dt.date) -> list:
    out = []
    try:
        lines = open(LEDGER / f"{day:%Y-%m}.jsonl", encoding="utf-8")
    except FileNotFoundError:
        return out
    for line in lines:
        try:
            e = json.loads(line)
            t = dt.datetime.fromisoformat(e["ts"])
        except (ValueError, KeyError):
            continue
        if e.get("kind") in SKIP_KINDS or e.get("via") in MACHINE_VIA or not inside(t, ranges):
            continue
        out.append((t, "neon", f"{e.get('sheet')}!{e.get('col')} {e.get('date')}: {e.get('src')}"))
    return out


def completed_events(ranges: list, day: dt.date) -> list:
    try:
        c = json.loads(COMPLETED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if c.get("date") != day.isoformat():
        return []
    out = []
    for name, ts in (c.get("done_at") or {}).items():
        t = dt.datetime.fromtimestamp(ts)
        if inside(t, ranges):
            out.append((t, "done", name))
    return out


def prompt_text(rec: dict) -> str:
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return c if isinstance(c, str) else ""


def prompt_events(ranges: list, day: dt.date, root: Path = PROJECTS) -> list:
    since = dt.datetime.combine(day, dt.time()).timestamp()
    out = []
    for f in root.glob("*/*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        for line in open(f, errors="replace"):
            if '"type":"user"' not in line and '"type": "user"' not in line:
                continue
            try:
                r = json.loads(line)
                t = (dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                     .astimezone().replace(tzinfo=None))
            except (ValueError, KeyError):
                continue
            if r.get("isSidechain") or r.get("toolUseResult") or not inside(t, ranges):
                continue
            text = prompt_text(r).strip()
            if text and not text.startswith(SKIP_PROMPT):
                args = re.search(r"<command-name>(.*?)</command-name>.*?<command-args>(.*?)</command-args>",
                                 text, re.S)
                text = f"{args.group(1)} {args.group(2)}" if args else text
                out.append((t, "prompt", f"[{f.stem[:6]}] {' '.join(text.split())[:160]}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ranges", nargs="+")
    ap.add_argument("--date")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    ranges = parse_ranges(a.ranges, day)
    events = ledger_events(ranges, day) + completed_events(ranges, day) + prompt_events(ranges, day)
    for t, kind, text in sorted(events, key=lambda e: e[0]):
        print(f"{t:%H:%M} {kind:<6} {text}")
    if not events:
        print("no local evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
