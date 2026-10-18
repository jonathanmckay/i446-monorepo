#!/usr/bin/env python3
"""dtd log: what dtd recorded, across both machines (2026-10-17).

Reads ~/vault/z_ibx/dtd-log/<host>.jsonl, the per-host record logs the key
routers (pick records: action, row, query, list position) and dtd_outcome.py
(done records: bucket, estimate vs Toggl minutes, points, what did-fast
wrote, overdue days, age, impressions) append. Joined by task id.

Usage: dtd-log.py [--days N] [--host H] [--json]
  default: today. --days 7 = the last 7 days.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import statistics
import time

LOGDIR = os.path.expanduser("~/vault/z_ibx/dtd-log")


def load(days: int, host: str | None = None) -> list[dict]:
    since = time.mktime((dt.date.today() - dt.timedelta(days=days - 1)).timetuple())
    recs = []
    for p in sorted(glob.glob(os.path.join(LOGDIR, "*.jsonl"))):
        if host and os.path.basename(p) != f"{host}.jsonl":
            continue
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if float(r.get("ts", 0)) >= since:
                recs.append(r)
    recs.sort(key=lambda r: r.get("ts", 0))
    return recs


def summarize(recs: list[dict]) -> dict:
    done = [r for r in recs if r.get("kind") == "done"]
    picks = [r for r in recs if r.get("kind") == "pick"]
    ok = [r for r in done if r.get("status") in ("ok", "already", "recorded")]
    failed = [r for r in done if r.get("status") not in ("ok", "already", "recorded")]
    by_bucket = collections.Counter(r.get("bucket") for r in ok)
    by_host = collections.Counter(r.get("host") for r in ok)
    tasks = [r for r in ok if r.get("bucket") == "task"]
    no_value = [r for r in tasks if not r.get("points") and not r.get("bonus")]
    est_pairs = [(r["est"], r["actual"]) for r in ok if r.get("est") and r.get("actual")]
    queried = [p for p in picks if (p.get("q") or "").strip()]
    deep = [p for p in picks if p.get("pos") and p["pos"] > 15]
    return {
        "done": len(ok), "failed": len(failed), "by_bucket": dict(by_bucket), "by_host": dict(by_host),
        "points": sum((r.get("points") or 0) for r in tasks), "bonus": sum((r.get("bonus") or 0) for r in ok),
        "tasks_no_value": [r.get("content") for r in no_value],
        "overdue_median": statistics.median([r["overdue_days"] for r in tasks if r.get("overdue_days") is not None]) if any(r.get("overdue_days") is not None for r in tasks) else None,
        "age_median": statistics.median([r["age_days"] for r in ok if r.get("age_days") is not None]) if any(r.get("age_days") is not None for r in ok) else None,
        "est_vs_actual": est_pairs, "picks": len(picks), "picks_with_query": len(queried), "picks_beyond_15": len(deep),
        "queries": collections.Counter((p.get("q") or "").strip().lower() for p in queried).most_common(10),
        "failed_items": [(r.get("content"), r.get("writes")) for r in failed],
        "task_rows": tasks, "deep_picks": deep,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--host")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    recs = load(a.days, a.host)
    s = summarize(recs)
    if a.json:
        print(json.dumps({k: v for k, v in s.items() if k not in ("task_rows", "deep_picks")}, ensure_ascii=False, indent=1, default=str))
        return 0
    span = "today" if a.days == 1 else f"last {a.days} days"
    print(f"dtd log · {span} · {len(recs)} records")
    print(f"  done {s['done']}  ({', '.join(f'{k} {v}' for k, v in s['by_bucket'].items())})  by host {s['by_host']}  failed {s['failed']}")
    print(f"  task points {s['points']}  goal bonus {s['bonus']}  tasks with no value {len(s['tasks_no_value'])}")
    if s["overdue_median"] is not None or s["age_median"] is not None:
        print(f"  median overdue at completion {s['overdue_median']} d · median age since first shown {s['age_median']} d")
    if s["est_vs_actual"]:
        ratio = statistics.median(act / est for est, act in s["est_vs_actual"] if est)
        print(f"  estimate vs actual: {len(s['est_vs_actual'])} timed · median actual/est {ratio:.2f}")
    print(f"  picks {s['picks']} · with a query {s['picks_with_query']} · found beyond row 15: {s['picks_beyond_15']}")
    for q, n in s["queries"]:
        print(f"    query {q!r} ×{n}")
    if s["task_rows"]:
        print("  tasks:")
        for r in s["task_rows"]:
            est = f"({r['est']})" if r.get("est") else ""
            act = f" {r['actual']}m" if r.get("actual") else ""
            pts = f"[{r['points']}]" if r.get("points") else (f"{{{r['bonus']}}}" if r.get("bonus") else "[-]")
            od = f" +{r['overdue_days']}d" if r.get("overdue_days") else ""
            w = " ".join(f"{k}:{v}" for k, v in (r.get("writes") or {}).items())
            print(f"    {time.strftime('%H:%M', time.localtime(r['ts']))} {r.get('host','')[:3]} {r.get('content','')[:44]:<44} {est:>5}{act:>5} {pts:>6}{od:>5}  {w}")
    for c, w in s["failed_items"]:
        print(f"  ✗ {c}  {w}")
    for c in s["tasks_no_value"]:
        print(f"  no value: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
