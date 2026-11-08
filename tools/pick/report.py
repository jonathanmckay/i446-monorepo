#!/usr/bin/env python3
"""report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] — agent-picks scorecard.

For /1s. Over the window: picks, ready/abstain/fail, accepted 分, review
minutes, reject rate, spend, and net 分 per review minute against JM's
~1.5 分 per waking minute baseline (1,450 分/day over ~16 h). Also lists
receipts still waiting on him and classes calibration has disabled.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import picks  # noqa: E402

BASELINE = 1.5


def _ts(day: str) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").timestamp())


def report(evs: list[dict], since: int, until: int) -> str:
    win = [e for e in evs if since <= e.get("ts", 0) < until]
    count = lambda ev: sum(1 for e in win if e.get("event") == ev)  # noqa: E731
    acc = [e for e in win if e.get("event") == "accept"]
    rej = [e for e in win if e.get("event") == "reject"]
    fen = sum(e.get("value") or 0 for e in acc)
    review_min = sum((e.get("review_sec") or 0) for e in acc + rej) / 60
    cost = sum((e.get("cost") or 0) for e in win) + sum((e.get("picker_cost") or 0) for e in win)
    decided = len(acc) + len(rej)
    per_min = fen / review_min if review_min else None
    waiting = sorted({e["task_id"] for e in evs if e.get("event") == "ready"
                      and picks.status(e["task_id"]) == "ready"})
    lines = [
        f"picks {count('pick')} · ready {count('ready')} · abstain {count('abstain')} · fail {count('fail')}",
        f"accepted {len(acc)} ({fen} 分) · rejected {len(rej)}"
        + (f" · reject rate {len(rej) / decided:.0%}" if decided else ""),
        f"review {review_min:.0f} min · spend ${cost:.2f}",
        ("net " + (f"{per_min:.1f} 分/review-min vs {BASELINE} baseline → "
                   + ("PAYS" if per_min > BASELINE else "DOES NOT PAY")) if per_min is not None
         else "net: no reviewed outcomes yet"),
        f"waiting on JM: {len(waiting)}" + (f" ({', '.join(waiting)})" if waiting else ""),
    ]
    dis = picks.disabled_classes(evs)
    if dis:
        lines.append("disabled: " + "; ".join(f"{k} ({v})" for k, v in dis.items()))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    today = dt.date.today()
    ap.add_argument("--since", default=str(today - dt.timedelta(days=7)))
    ap.add_argument("--until", default=str(today + dt.timedelta(days=1)))
    a = ap.parse_args()
    print(report(picks.events(), _ts(a.since), _ts(a.until)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
